import os
from datetime import datetime, timedelta, timezone
from functools import wraps

from jose import JWTError, jwt
from litestar import Litestar, Request, get, post
from litestar.exceptions import HTTPException
from litestar.response import Response
from litestar.status_codes import HTTP_401_UNAUTHORIZED, HTTP_403_FORBIDDEN
from passlib.context import CryptContext
from psycopg.errors import UniqueViolation

from db import SCHEMA, connect
from rules import judge

SECRET = os.environ.get("JWT_SECRET", "pvivscan-dev-secret")
pwd = CryptContext(schemes=["bcrypt"], deprecated="auto")
USERS = {
    "scanner": {"role": "writer", "password_hash": pwd.hash("scan123456")},
    "watcher": {"role": "reader", "password_hash": pwd.hash("watch123456")},
    "chief": {"role": "chief", "password_hash": pwd.hash("chief123456")},
}


def dump(row):
    out = dict(row)
    for key, val in list(out.items()):
        if hasattr(val, "isoformat"):
            out[key] = val.isoformat()
    return out


def seed():
    with connect() as conn:
        conn.execute(SCHEMA)
        n = conn.execute("SELECT COUNT(*) AS n FROM iv_scans").fetchone()["n"]
        now = datetime.now(timezone.utc)
        if n == 0:
            samples = [
                ("阵列A-串03", "一号场区", 41.2, 9.1, 0.78, "合格"),
                ("阵列B-串11", "二号场区", 38.0, 8.4, 0.61, "衰减"),
            ]
            for code, site, voc, isc, ff, expect in samples:
                verdict, reason = judge(ff)
                assert verdict == expect
                conn.execute(
                    """INSERT INTO iv_scans
                       (string_code, site, voc_v, isc_a, fill_factor, status, verdict, reason,
                        created_by, created_at, processed_at)
                       VALUES (%s,%s,%s,%s,%s,'done',%s,%s,'scanner',%s,%s)""",
                    (code, site, voc, isc, ff, verdict, reason, now, now),
                )
        # 观察员 watcher 的临时值班长签字授权：可签字，但仍不可提交扫描
        g = conn.execute(
            "SELECT COUNT(*) AS n FROM sign_grants WHERE username='watcher'"
        ).fetchone()["n"]
        if g == 0:
            conn.execute(
                """INSERT INTO sign_grants (username, granted_by, created_at, expires_at)
                   VALUES ('watcher', 'chief', %s, NULL)""",
                (now,),
            )
        conn.commit()


seed()


def user_from(request: Request):
    auth = request.headers.get("authorization", "")
    if not auth.lower().startswith("bearer "):
        return None
    try:
        payload = jwt.decode(auth.split(" ", 1)[1].strip(), SECRET, algorithms=["HS256"])
    except JWTError:
        return None
    sub = payload.get("sub")
    if sub not in USERS:
        return None
    return {"username": sub, "role": payload.get("role")}


def need_login(request: Request):
    user = user_from(request)
    if user is None:
        raise HTTPException(status_code=HTTP_401_UNAUTHORIZED, detail="未登录")
    return user


def need_writer(request: Request):
    user = need_login(request)
    if user["role"] != "writer":
        raise HTTPException(status_code=HTTP_403_FORBIDDEN, detail="仅扫描员可提交IV扫描")
    return user


def can_sign(conn, user) -> bool:
    """值班长本人，或持有效临时签字授权（如观察员 watcher）。"""
    if user["role"] == "chief":
        return True
    row = conn.execute(
        """SELECT 1 AS ok FROM sign_grants
           WHERE username=%s AND (expires_at IS NULL OR expires_at > %s)
           LIMIT 1""",
        (user["username"], datetime.now(timezone.utc)),
    ).fetchone()
    return row is not None


@get("/api/health")
async def health() -> dict:
    return {"status": "ok", "service": "pv-string-iv-scan"}


@post("/api/auth/login")
async def login(request: Request) -> dict:
    data = await request.json()
    username = (data.get("username") or "").strip()
    password = data.get("password") or ""
    user = USERS.get(username)
    if not user or not pwd.verify(password, user["password_hash"]):
        raise HTTPException(status_code=HTTP_401_UNAUTHORIZED, detail="用户名或密码错误")
    exp = datetime.now(timezone.utc) + timedelta(hours=8)
    token = jwt.encode(
        {"sub": username, "role": user["role"], "exp": exp}, SECRET, algorithm="HS256"
    )
    return {"access_token": token, "username": username, "role": user["role"]}


@get("/api/me")
async def me(request: Request) -> dict:
    user = need_login(request)
    with connect() as conn:
        return {
            "username": user["username"],
            "role": user["role"],
            "can_sign": can_sign(conn, user),
        }


@get("/api/logs")
async def list_logs(request: Request) -> list:
    need_login(request)
    with connect() as conn:
        rows = conn.execute(
            """SELECT id, string_code, site, voc_v, isc_a, fill_factor, status, verdict, reason,
                      created_by, created_at, processed_at
               FROM iv_scans ORDER BY id DESC"""
        ).fetchall()
        return [dump(r) for r in rows]


@post("/api/logs", status_code=201)
async def create_log(request: Request) -> dict:
    user = need_writer(request)
    data = await request.json()
    code = (data.get("string_code") or "").strip()
    site = (data.get("site") or "").strip()
    if not code:
        raise HTTPException(status_code=400, detail="组串编号不能为空")
    if not site:
        raise HTTPException(status_code=400, detail="场区不能为空")
    try:
        voc = float(data.get("voc_v"))
        isc = float(data.get("isc_a"))
        ff = float(data.get("fill_factor"))
    except (TypeError, ValueError):
        raise HTTPException(status_code=400, detail="电压电流与填充因子必须是数字")
    now = datetime.now(timezone.utc)
    today = now.date()
    with connect() as conn:
        # 入队闸口：该场区当日停电工作票必须已签完，否则拒绝写入
        ticket = conn.execute(
            "SELECT status FROM work_tickets WHERE site=%s AND work_date=%s",
            (site, today),
        ).fetchone()
        if ticket is None or ticket["status"] != "signed":
            raise HTTPException(
                status_code=HTTP_403_FORBIDDEN,
                detail="该场区当日停电工作票未签完，禁止写入",
            )
        row = conn.execute(
            """INSERT INTO iv_scans
               (string_code, site, voc_v, isc_a, fill_factor, status, created_by, created_at)
               VALUES (%s,%s,%s,%s,%s,'pending',%s,%s)
               RETURNING id, string_code, site, voc_v, isc_a, fill_factor, status, verdict, reason,
                         created_by, created_at, processed_at""",
            (code, site, voc, isc, ff, user["username"], now),
        ).fetchone()
        conn.commit()
        return dump(row)


@post("/api/tickets", status_code=201)
async def create_ticket(request: Request) -> dict:
    user = need_login(request)
    data = await request.json()
    site = (data.get("site") or "").strip()
    if not site:
        raise HTTPException(status_code=400, detail="场区不能为空")
    now = datetime.now(timezone.utc)
    today = now.date()
    with connect() as conn:
        try:
            # 申请记录与痕迹同批落库
            with conn.transaction():
                row = conn.execute(
                    """INSERT INTO work_tickets (site, work_date, status, applicant, created_at)
                       VALUES (%s,%s,'pending',%s,%s)
                       RETURNING id, site, work_date, status, applicant, created_at,
                                 signed_by, signed_at""",
                    (site, today, user["username"], now),
                ).fetchone()
                conn.execute(
                    """INSERT INTO ticket_traces (ticket_id, action, actor, detail, created_at)
                       VALUES (%s,'apply',%s,%s,%s)""",
                    (row["id"], user["username"], f"发起{site}当日停电工作票申请", now),
                )
        except UniqueViolation:
            raise HTTPException(status_code=409, detail="该场区当日停电工作票已存在")
        conn.commit()
        return dump(row)


@get("/api/tickets")
async def list_tickets(request: Request) -> list:
    need_login(request)
    with connect() as conn:
        rows = conn.execute(
            """SELECT id, site, work_date, status, applicant, created_at, signed_by, signed_at
               FROM work_tickets ORDER BY id DESC"""
        ).fetchall()
        return [dump(r) for r in rows]


@get("/api/tickets/check")
async def check_ticket(request: Request) -> dict:
    need_login(request)
    site = (request.query_params.get("site") or "").strip()
    today = datetime.now(timezone.utc).date()
    status = "none"
    if site:
        with connect() as conn:
            row = conn.execute(
                "SELECT status FROM work_tickets WHERE site=%s AND work_date=%s",
                (site, today),
            ).fetchone()
        if row is not None:
            status = row["status"]
    return {
        "site": site,
        "work_date": today.isoformat(),
        "status": status,
        "signed": status == "signed",
    }


@get("/api/tickets/traces")
async def list_traces(request: Request) -> list:
    need_login(request)
    with connect() as conn:
        rows = conn.execute(
            """SELECT t.id, t.ticket_id, t.action, t.actor, t.detail, t.created_at, w.site
               FROM ticket_traces t
               JOIN work_tickets w ON w.id = t.ticket_id
               ORDER BY t.id DESC"""
        ).fetchall()
        return [dump(r) for r in rows]


@post("/api/tickets/{ticket_id:int}/sign")
async def sign_ticket(request: Request, ticket_id: int) -> dict:
    user = need_login(request)
    now = datetime.now(timezone.utc)
    with connect() as conn:
        # 签字记录与痕迹必须同批落库，半截签字不算数
        with conn.transaction():
            row = conn.execute(
                """SELECT id, site, work_date, status, applicant, created_at, signed_by, signed_at
                   FROM work_tickets WHERE id=%s FOR UPDATE""",
                (ticket_id,),
            ).fetchone()
            if row is None:
                raise HTTPException(status_code=404, detail="工作票不存在")
            if row["status"] != "pending":
                raise HTTPException(status_code=409, detail="该工作票已签字")
            if row["applicant"] == user["username"]:
                raise HTTPException(
                    status_code=HTTP_403_FORBIDDEN,
                    detail="发起人禁止给本人负责的场区代签",
                )
            if not can_sign(conn, user):
                raise HTTPException(
                    status_code=HTTP_403_FORBIDDEN, detail="无值班长签字权限"
                )
            row = conn.execute(
                """UPDATE work_tickets
                   SET status='signed', signed_by=%s, signed_at=%s
                   WHERE id=%s
                   RETURNING id, site, work_date, status, applicant, created_at,
                             signed_by, signed_at""",
                (user["username"], now, ticket_id),
            ).fetchone()
            conn.execute(
                """INSERT INTO ticket_traces (ticket_id, action, actor, detail, created_at)
                   VALUES (%s,'sign',%s,%s,%s)""",
                (ticket_id, user["username"], f"{row['site']}当日停电工作票签字放行", now),
            )
        conn.commit()
        return dump(row)


app = Litestar(
    route_handlers=[
        health,
        login,
        me,
        list_logs,
        create_log,
        create_ticket,
        list_tickets,
        check_ticket,
        list_traces,
        sign_ticket,
    ]
)
