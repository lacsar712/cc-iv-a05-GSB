import os
from datetime import datetime, timedelta, timezone
from functools import wraps

from jose import JWTError, jwt
from litestar import Litestar, Request, delete, get, post
from litestar.exceptions import HTTPException
from litestar.response import Response
from litestar.status_codes import HTTP_401_UNAUTHORIZED, HTTP_403_FORBIDDEN
from passlib.context import CryptContext

from db import SCHEMA, connect
from rules import judge

SECRET = os.environ.get("JWT_SECRET", "pvivscan-dev-secret")
pwd = CryptContext(schemes=["bcrypt"], deprecated="auto")
USERS = {
    "scanner": {"role": "writer", "password_hash": pwd.hash("scan123456")},
    "watcher": {"role": "reader", "password_hash": pwd.hash("watch123456")},
    "chief": {"role": "chief", "password_hash": pwd.hash("chief123456")},
}
# 临时值班长签字权限
GRANT_PERMISSION = "ticket_sign"
GRANT_TTL = timedelta(hours=8)


def dump(row):
    out = dict(row)
    for key, val in list(out.items()):
        if hasattr(val, "isoformat"):
            out[key] = val.isoformat()
    return out


def now_utc():
    return datetime.now(timezone.utc)


def today():
    return now_utc().date()


def seed():
    with connect() as conn:
        conn.execute(SCHEMA)
        n = conn.execute("SELECT COUNT(*) AS n FROM iv_scans").fetchone()["n"]
        if n == 0:
            now = now_utc()
            samples = [
                ("一号场区", "阵列A-串03", 41.2, 9.1, 0.78, "合格"),
                ("二号场区", "阵列B-串11", 38.0, 8.4, 0.61, "衰减"),
            ]
            for site, code, voc, isc, ff, expect in samples:
                verdict, reason = judge(ff)
                assert verdict == expect
                conn.execute(
                    """INSERT INTO iv_scans
                       (string_code, voc_v, isc_a, fill_factor, status, verdict, reason,
                        site, created_by, created_at, processed_at)
                       VALUES (%s,%s,%s,%s,'done',%s,%s,%s,'scanner',%s,%s)""",
                    (code, voc, isc, ff, verdict, reason, site, now, now),
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


def need_chief(request: Request):
    user = need_login(request)
    if user["role"] != "chief":
        raise HTTPException(status_code=HTTP_403_FORBIDDEN, detail="仅值班长可操作授权")
    return user


def has_sign_power(conn, user) -> bool:
    """值班长本人，或持有未过期、未收回的临时签字授权（观察员走这条路）。"""
    if user["role"] == "chief":
        return True
    row = conn.execute(
        """SELECT 1 AS ok FROM sign_grants
           WHERE username = %s AND permission = %s
             AND NOT revoked AND expires_at > %s
           LIMIT 1""",
        (user["username"], GRANT_PERMISSION, now_utc()),
    ).fetchone()
    return row is not None


@get("/api/health")
async def health() -> dict:
    return {"status": "ok", "service": "pv-string-iv-scan"}


@post("/api/auth/login", status_code=200)
async def login(request: Request) -> dict:
    data = await request.json()
    username = (data.get("username") or "").strip()
    password = data.get("password") or ""
    user = USERS.get(username)
    if not user or not pwd.verify(password, user["password_hash"]):
        raise HTTPException(status_code=HTTP_401_UNAUTHORIZED, detail="用户名或密码错误")
    exp = now_utc() + timedelta(hours=8)
    token = jwt.encode(
        {"sub": username, "role": user["role"], "exp": exp}, SECRET, algorithm="HS256"
    )
    return {"access_token": token, "username": username, "role": user["role"]}


@get("/api/logs")
async def list_logs(request: Request) -> list:
    need_login(request)
    with connect() as conn:
        rows = conn.execute(
            """SELECT id, string_code, voc_v, isc_a, fill_factor, status, verdict, reason,
                      site, created_by, created_at, processed_at
               FROM iv_scans ORDER BY id DESC"""
        ).fetchall()
        return [dump(r) for r in rows]


@post("/api/logs", status_code=201)
async def create_log(request: Request) -> dict:
    user = need_writer(request)
    data = await request.json()
    code = (data.get("string_code") or "").strip()
    site = (data.get("site") or "").strip()
    if not site:
        raise HTTPException(status_code=400, detail="场区不能为空")
    if not code:
        raise HTTPException(status_code=400, detail="组串编号不能为空")
    try:
        voc = float(data.get("voc_v"))
        isc = float(data.get("isc_a"))
        ff = float(data.get("fill_factor"))
    except (TypeError, ValueError):
        raise HTTPException(status_code=400, detail="电压电流与填充因子必须是数字")
    now = now_utc()
    with connect() as conn:
        # 入队前先问该场区当日停电工作票签没签完；写入通道硬拦截，未签一律拒收
        ticket = conn.execute(
            """SELECT id, status FROM work_tickets
               WHERE site = %s AND work_date = %s""",
            (site, today()),
        ).fetchone()
        if ticket is None or ticket["status"] != "signed":
            raise HTTPException(
                status_code=HTTP_403_FORBIDDEN,
                detail=f"场区 {site} 当日停电工作票未签完，禁止入队",
            )
        row = conn.execute(
            """INSERT INTO iv_scans
               (string_code, voc_v, isc_a, fill_factor, status, site, created_by, created_at)
               VALUES (%s,%s,%s,%s,'pending',%s,%s,%s)
               RETURNING id, string_code, voc_v, isc_a, fill_factor, status, verdict, reason,
                         site, created_by, created_at, processed_at""",
            (code, voc, isc, ff, site, user["username"], now),
        ).fetchone()
        conn.commit()
        return dump(row)


@get("/api/tickets")
async def list_tickets(request: Request) -> dict:
    need_login(request)
    with connect() as conn:
        rows = conn.execute(
            """SELECT id, site, work_date, status, applicant, signed_by, signed_at, created_at
               FROM work_tickets ORDER BY id DESC"""
        ).fetchall()
        traces = conn.execute(
            """SELECT id, ticket_id, site, action, actor, detail, created_at
               FROM ticket_traces ORDER BY id DESC"""
        ).fetchall()
    tickets = [dump(r) for r in rows]
    return {
        "pending": [t for t in tickets if t["status"] == "pending"],
        "signed": [t for t in tickets if t["status"] == "signed"],
        "traces": [dump(r) for r in traces],
    }


@post("/api/tickets", status_code=201)
async def apply_ticket(request: Request) -> dict:
    user = need_login(request)
    if user["role"] not in ("writer", "chief"):
        raise HTTPException(status_code=HTTP_403_FORBIDDEN, detail="观察员为只读账号，不能发起申请")
    data = await request.json()
    site = (data.get("site") or "").strip()
    if not site:
        raise HTTPException(status_code=400, detail="场区不能为空")
    now = now_utc()
    work_date = today()
    with connect() as conn:
        dup = conn.execute(
            "SELECT id FROM work_tickets WHERE site = %s AND work_date = %s",
            (site, work_date),
        ).fetchone()
        if dup:
            raise HTTPException(status_code=409, detail="该场区当日停电工作票已存在")
        # 申请记录与痕迹同批落库
        row = conn.execute(
            """INSERT INTO work_tickets (site, work_date, status, applicant, created_at)
               VALUES (%s,%s,'pending',%s,%s)
               RETURNING id, site, work_date, status, applicant, signed_by, signed_at, created_at""",
            (site, work_date, user["username"], now),
        ).fetchone()
        conn.execute(
            """INSERT INTO ticket_traces (ticket_id, site, action, actor, detail, created_at)
               VALUES (%s,%s,'apply',%s,%s,%s)""",
            (row["id"], site, user["username"],
             f"{user['username']} 发起 {site} 当日停电工作票申请", now),
        )
        conn.commit()
        return dump(row)


@post("/api/tickets/{ticket_id:int}/sign", status_code=200)
async def sign_ticket(request: Request, ticket_id: int) -> dict:
    user = need_login(request)
    now = now_utc()
    with connect() as conn:
        if not has_sign_power(conn, user):
            raise HTTPException(status_code=HTTP_403_FORBIDDEN, detail="需要值班长签字权限")
        row = conn.execute(
            """SELECT id, site, work_date, status, applicant
               FROM work_tickets WHERE id = %s FOR UPDATE""",
            (ticket_id,),
        ).fetchone()
        if row is None:
            raise HTTPException(status_code=404, detail="工作票不存在")
        if row["applicant"] == user["username"]:
            raise HTTPException(status_code=HTTP_403_FORBIDDEN, detail="发起人禁止给本人负责的场区代签")
        if row["status"] != "pending":
            raise HTTPException(status_code=409, detail="该工作票已签字")
        # 签字记录与痕迹同批落库：同一事务提交，半截签字整体回滚不算数
        signed = conn.execute(
            """UPDATE work_tickets
               SET status = 'signed', signed_by = %s, signed_at = %s
               WHERE id = %s
               RETURNING id, site, work_date, status, applicant, signed_by, signed_at, created_at""",
            (user["username"], now, ticket_id),
        ).fetchone()
        conn.execute(
            """INSERT INTO ticket_traces (ticket_id, site, action, actor, detail, created_at)
               VALUES (%s,%s,'sign',%s,%s,%s)""",
            (ticket_id, row["site"], user["username"],
             f"{user['username']} 签发 {row['site']} 当日停电工作票", now),
        )
        conn.commit()
        return dump(signed)


@get("/api/grants")
async def list_grants(request: Request) -> list:
    need_login(request)
    with connect() as conn:
        rows = conn.execute(
            """SELECT id, username, permission, granted_by, created_at, expires_at, revoked
               FROM sign_grants
               WHERE NOT revoked AND expires_at > %s
               ORDER BY id DESC""",
            (now_utc(),),
        ).fetchall()
        return [dump(r) for r in rows]


@post("/api/grants", status_code=201)
async def create_grant(request: Request) -> dict:
    user = need_chief(request)
    data = await request.json()
    username = (data.get("username") or "").strip()
    if username not in USERS:
        raise HTTPException(status_code=400, detail="目标用户不存在")
    now = now_utc()
    with connect() as conn:
        row = conn.execute(
            """INSERT INTO sign_grants (username, permission, granted_by, created_at, expires_at)
               VALUES (%s,%s,%s,%s,%s)
               RETURNING id, username, permission, granted_by, created_at, expires_at, revoked""",
            (username, GRANT_PERMISSION, user["username"], now, now + GRANT_TTL),
        ).fetchone()
        conn.commit()
        return dump(row)


@delete("/api/grants/{grant_id:int}", status_code=200)
async def revoke_grant(request: Request, grant_id: int) -> dict:
    user = need_chief(request)
    with connect() as conn:
        row = conn.execute(
            """UPDATE sign_grants SET revoked = true
               WHERE id = %s
               RETURNING id, username, permission, granted_by, created_at, expires_at, revoked""",
            (grant_id,),
        ).fetchone()
        if row is None:
            raise HTTPException(status_code=404, detail="授权不存在")
        conn.commit()
        return dump(row)


app = Litestar(
    route_handlers=[
        health,
        login,
        list_logs,
        create_log,
        list_tickets,
        apply_ticket,
        sign_ticket,
        list_grants,
        create_grant,
        revoke_grant,
    ]
)
