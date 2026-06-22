# 架构

## 组件概览

```
┌─────────────┐     ┌──────────────┐     ┌─────────────┐
│   浏览器    │────▶│   Nginx      │────▶│  Backend    │
│  React SPA  │     │  (反代+静态) │     │  FastAPI    │
└─────────────┘     └──────────────┘     └──────┬──────┘
                                               │
                          ┌────────────────────┼────────────────────┐
                          ▼                    ▼                    ▼
                    ┌──────────┐        ┌──────────┐         ┌──────────┐
                    │  Redis   │        │ SQLite   │         │   LLM    │
                    │  会话    │        │ main +   │         │ OpenAI / │
                    │  限流    │        │ per-src  │         │ Anthropic│
                    └──────────┘        └──────────┘         └──────────┘
```

## 后端分层

| 层 | 路径 | 职责 |
|----|------|------|
| 路由 | `app/api/routes.py`、`app/api/auth_routes.py` | HTTP 端点，参数校验，错误转换 |
| 中间件 | `app/api/middleware.py` | 限流、request_id 注入 |
| 服务 | `app/services/` | 业务编排（chat / data / session / metadata / streaming / auth） |
| Agent | `app/agents/` | LangGraph 图、状态、工具、LLM provider |
| 工具 | `app/utils/` | database / logger / pii_mask / rate_limit / log_scrub / request_id |
| 配置 | `app/config.py` | pydantic-settings 加载 .env + 启动校验 |

## 数据存储

### 主库 (main.db)

- 一个 SQLite 文件，存 `users` 表 + 元数据 sidecar
- 由 SQLAlchemy engine 管理，`get_engine(None)` 取默认
- 路径由 `DATABASE_URL` 控制，默认 `sqlite:///./data/main.db`

### 数据源库 (per-source SQLite)

- 每个上传文件落到独立 SQLite：`data/sqlite/{file_id}.db`
- 表名固定 `uploaded_data`（多 sheet 的 Excel 会有多张表）
- 与主库物理隔离，删除数据源 = 删文件 + 删 sidecar，无主库迁移

### Redis

- 会话：`session:{session_id}`，TTL 30 分钟，写操作重置
- 限流：`rl:user:{user_id}` / `rl:ip:{ip}` ZSET 滑动窗口
- Refresh token 吊销列表：`revoked:refresh:{jti}`

## Agent 工作流

LangGraph 图节点：

1. **理解意图**：LLM 判断是否需要查数据 / 画图
2. **工具调用**：
    - `query_database(sql)` — 执行只读 SQL，返回 JSON
    - `get_table_schema()` — 读 `uploaded_data` 表结构
    - `create_chart(type, title, x_data, series)` — 注册图表
3. **后处理**：图表 → ECharts option，写入 `chart_data`

SSE 流式：每个节点产出的事件实时推给前端，不等整轮结束。

## 安全分层

| 层 | 措施 |
|----|------|
| 网络 | Nginx 反代，安全头，TLS 交给上游 |
| 鉴权 | JWT access + refresh，bcrypt 哈希 |
| 多租户 | 数据源 / 会话带 owner_id，跨用户 404 |
| 限流 | per-IP / per-user 滑动窗口 |
| SQL 沙箱 | 只允许 SELECT / WITH，禁止写操作 |
| PII 脱敏 | 上传 / 查询出口 / 审计日志 / LLM prompt 四层 |
| 启动校验 | JWT_SECRET <32 字节直接 fatal |
| 日志脱敏 | ScrubFilter 对所有日志行跑正则替换 |

## 可观测性

- **日志**：JSON 结构化，字段 `timestamp / level / logger / message / request_id / extras`，PII 脱敏后输出
- **request_id**：每请求一个 UUID，贯穿 access log + 业务 log + 响应 header
- **metrics**：Prometheus `/metrics`，默认 `http_requests_total` / `http_request_duration_seconds` / `http_requests_in_progress`

## 备份

- `scripts/backup.py`：sqlite VACUUM INTO + redis BGSAVE + 上传 MinIO
- `scripts/restore.py`：拉最新备份覆盖本地
- 调度靠外部（cron / K8s CronJob）
- 保留期 `BACKUP_RETENTION_DAYS`（默认 7 天）
