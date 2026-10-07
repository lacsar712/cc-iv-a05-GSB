# 光伏组串IV扫描台

扫描员提交组串开路电压、短路电流与填充因子。写入后走 PostgreSQL 通知通道叫醒独立工人，工人不轮询空转。填充因子不低于 0.72 为合格，否则衰减。页面是 Vue 3。

## 工作票放行

扫描写入前，该场区当日停电工作票必须已签完，否则接口直接拒写（前端提示只是辅助，闸口在后端）。

1. 现场在顶栏「工作票」专页对场区发出当日申请（待签区）。
2. 值班长签字后该场区当日放行（已签区）；签字记录与痕迹同事务落库，半截签字不算数。
3. 发起人禁止给本人负责的场区代签。
4. 观察员 watcher 持临时值班长签字授权：可以签字，但仍不能提交扫描。
5. 痕迹区可查申请与签字记录；三区无记录时显示「目前空闲」。

接口：`POST /api/tickets` 申请、`GET /api/tickets` 列表、`GET /api/tickets/check?site=` 当日状态、`POST /api/tickets/{id}/sign` 签字、`GET /api/tickets/traces` 痕迹、`GET /api/me` 当前权限。

## 技术栈

- 后端：Litestar、Uvicorn、psycopg 同步写入
- 工人：`LISTEN/NOTIFY` 唤醒后认领
- 前端：Vue 3、Vite、nginx 反代 `/api`

## 端口

| 服务 | 地址 |
|------|------|
| 页面 | http://localhost:3202 |
| 接口 | http://localhost:8202 |
| PostgreSQL | localhost:54402（库名 `pvivscan`） |

## 账号

| 用户 | 密码 | 权限 |
|------|------|------|
| scanner | scan123456 | 可提交 |
| watcher | watch123456 | 只读，持临时签字授权（可签不可写） |
| chief | chief123456 | 值班长，可签字 |

## 启动

```bash
cd projects/22-pv-string-iv-scan
docker compose up --build
```

健康检查：`GET http://localhost:8202/api/health`

## 种子

| 组串 | 场区 | 填充因子 | 结论 |
|------|------|----------|------|
| 阵列A-串03 | 一号场区 | 0.78 | 合格 |
| 阵列B-串11 | 二号场区 | 0.61 | 衰减 |
