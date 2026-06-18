# API

所有端点都在 `/api/v1` 前缀下。除 `/auth/register`、`/auth/login`、`/auth/refresh`、`/health/*`、`/metrics` 外都要求 `Authorization: Bearer <access_token>`。

完整交互式文档在运行时访问 `/docs`（Swagger UI）或 `/redoc`（ReDoc）。本页只列概要。

## 认证

| 方法 | 路径 | 用途 | 鉴权 |
|------|------|------|------|
| POST | `/auth/register` | 邮箱+密码注册，返回 access + refresh token | 无 |
| POST | `/auth/login` | 登录，返回 token 对 | 无 |
| POST | `/auth/refresh` | 用 refresh token 换新 token 对，旧 refresh 立即吊销 | 无 |
| POST | `/auth/logout` | 吊销当前 refresh token | Bearer |
| GET  | `/auth/me` | 返回当前用户信息 | Bearer |

**Token TTL**：access 15 分钟，refresh 7 天，可在 `.env` 调整。

## 数据源

| 方法 | 路径 | 用途 |
|------|------|------|
| POST | `/upload` | 上传 CSV/Excel/JSON，返回 `file_id` |
| GET  | `/datasources` | 列出**自己**的数据源（按 mtime 倒序） |
| PATCH| `/datasources/{id}` | 重命名（修改 display_name） |
| DELETE| `/datasources/{id}` | 删除数据源 + 关联会话 |
| GET  | `/datasources/{id}/preview` | 预览前 N 行（默认 5） |
| GET  | `/datasources/{id}/schema` | 字段名与推断类型 |
| GET  | `/datasources/{id}/rows` | 分页浏览（`offset/limit/sort/dir`） |
| GET  | `/datasources/{id}/tables` | 列出该数据源的所有表（多 sheet 场景） |
| GET  | `/datasources/{id}/lineage` | 查询历史 |

**ACL**：跨用户访问统一返回 404，不暴露数据源存在性。

## 会话 & 对话

| 方法 | 路径 | 用途 |
|------|------|------|
| POST | `/sessions` | 创建新会话，返回 session_id 和初始 payload |
| GET  | `/sessions/{id}` | 读取会话（含 `ttl_seconds`） |
| PATCH| `/sessions/{id}` | 合并更新字段，重置 TTL |
| DELETE| `/sessions/{id}` | 显式删除会话 |
| POST | `/chat` | 同步发送消息，返回完整响应 |
| POST | `/chat/stream` | SSE 流式发送 |

### `/chat/stream` 事件协议

```
event: token
data: {"content": "..."}

event: tool_call
data: {"tool": "query_database", "input": {"sql_query": "SELECT ..."}}

event: tool_result
data: {"tool": "query_database", "output": [...]}

event: chart
data: {"chart_type": "line", "title": "...", "x_data": [...], "series": [...]}

event: end
data: {}

event: error
data: {"code": 500, "message": "..."}
```

## 系统

| 方法 | 路径 | 用途 |
|------|------|------|
| GET | `/health/live` | liveness，恒 200 |
| GET | `/health/ready` | readiness，Redis + DB 检查，失败 503 |
| GET | `/health` | `/health/ready` 别名，向后兼容 |
| GET | `/metrics` | Prometheus 文本格式 |

## 绑定规则

- 创建会话时不绑定数据源；第一次 `/chat` 请求带 `data_source_id` 时绑定
- 绑定后再用不一致的 `data_source_id` 调 `/chat` 返回 `403`
- 前端收到 403 会自动重置会话

## 限流

| 端点 | 维度 | 默认 / 分钟 |
|------|------|------|
| `/auth/login` | IP | 20 |
| `/auth/register` | IP | 10（login 的一半） |
| `/auth/refresh` | IP | 20 |
| `/chat`、`/chat/stream`、`/upload` | user | 60 |

超限返回 `429` + `Retry-After` header。Redis 故障时 fail open（不锁死 API）。
