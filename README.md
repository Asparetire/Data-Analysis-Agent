# Data Analysis Agent

让用户上传 CSV/Excel，用自然语言提问，LangGraph Agent 自动生成 SQL 查询数据并回答，
必要时返回 ECharts 图表配置供前端渲染。

## 架构

- **Backend**: FastAPI + LangGraph + LangChain + SQLAlchemy (SQLite) + Redis
- **Frontend**: React + Vite + TypeScript + ECharts + Zustand
- **LLM**: OpenAI 兼容接口 或 Anthropic 兼容接口（`LLM_PROVIDER` 切换）

每个上传的文件落到独立的 SQLite 文件（`data/sqlite/{file_id}.db`），表名固定 `uploaded_data`，互不干扰。

## 本地开发

### 1. 后端

需要 Python 3.11+。建议使用 Conda 虚拟环境：

```bash
cd backend
conda create -n data-analysis-agent python=3.11 -y
conda activate data-analysis-agent
pip install -r requirements.txt

cp .env.example .env
# 编辑 .env 填入 LLM_PROVIDER / OPENAI_API_KEY（或 ANTHROPIC_API_KEY）
# JWT_SECRET 必须是 ≥32 字节的随机串，否则启动会 fatal

# 启动 Redis（也可直接用 docker compose up redis）
redis-server
# 或者：docker run -p 6379:6379 -d redis:7-alpine

uvicorn app.main:app --reload --port 8000
```

启动后访问 http://localhost:8000/docs 看 API 文档。
本地想跑 mock LLM 不调真实接口：`LLM_MOCK=1 uvicorn app.main:app ...`。

### 2. 前端

需要 Node 18+。

```bash
cd frontend
npm install
npm run dev
```

访问 http://localhost:5173 。Vite 已配 `/api` 代理到 `http://localhost:8000`。

### 3. Docker（生产形态）

```bash
cp .env.example .env  # 项目根目录
# 编辑 .env 填入 OPENAI_API_KEY / JWT_SECRET / MIGRATION_ADMIN_PASSWORD / MINIO_ROOT_PASSWORD
docker compose up --build
```

- 入口（Nginx，前端静态 + /api 反代）: http://localhost
- MinIO 控制台: http://localhost:9001
- Redis: localhost:6379（本地开发保留 host 端口；生产部署可去掉）
- 后端 8000 / MinIO 9000 仅在 compose 网络内暴露，不直接访问

多阶段镜像：backend ~250MB（builder 装 gcc，runtime 不带），frontend = nginx:alpine + 静态 dist。TLS 交给上游（云 LB / Cloudflare），nginx 只听 80。

### 4. 测试

```bash
# 后端单测 + HTTP 集成测试（fakeredis + tmp 目录，无需真实 Redis / LLM）
cd backend
pytest

# 前端单测
cd frontend
npm run test

# E2E（Playwright，会自动起 backend + frontend）
cd frontend
npm run e2e
```

E2E 跑前需先启 Redis；CI 会作为 service container 自动拉起。

## 会话管理

后端用 Redis 保存会话，键 `session:{session_id}`，TTL 30 分钟，任何写操作都会重置 TTL。

字段：
- `session_id` (UUID4)
- `owner_id` (Phase 4A: 创建者 user id，ACL 检查依据)
- `data_source_id` (绑定的数据源，绑定后禁止跨数据源访问)
- `data_source_ids` (多数据源模式下的备选列表)
- `chat_history` (对话记录，每条含 `role/content/timestamp/chart_data/sql_query`)
- `intermediate_results` (最近一次查询结果 JSON 快照)
- `last_query` (最近一次 SQL)
- `created_at` / `updated_at`

## API 概览

所有 `/api/v1/*` 路由除 `/auth/register`、`/auth/login`、`/auth/refresh`、`/health` 外都要求 `Authorization: Bearer <access_token>`。

### 认证（Phase 4A）

| 方法 | 路径 | 用途 |
|------|------|------|
| POST | `/api/v1/auth/register` | 邮箱+密码注册，返回 access + refresh token |
| POST | `/api/v1/auth/login` | 登录，返回 token 对 |
| POST | `/api/v1/auth/refresh` | 用 refresh token 换新 token 对，旧 refresh 立即吊销（rotation） |
| POST | `/api/v1/auth/logout` | 吊销当前 refresh token |
| GET  | `/api/v1/auth/me` | 返回当前用户信息 |

Access token TTL 默认 15 分钟，refresh token TTL 7 天，可在 `.env` 调整。

### 数据源

| 方法 | 路径 | 用途 |
|------|------|------|
| POST | `/api/v1/upload` | 上传 CSV/Excel/JSON，返回 `file_id` |
| GET  | `/api/v1/datasources` | 列出**自己**的数据源（按 mtime 倒序） |
| PATCH| `/api/v1/datasources/{id}` | 重命名（修改 display_name） |
| DELETE| `/api/v1/datasources/{id}` | 删除数据源 + 关联会话 |
| GET  | `/api/v1/datasources/{id}/preview` | 预览前 N 行 |
| GET  | `/api/v1/datasources/{id}/schema` | 返回字段名与推断类型 |
| GET  | `/api/v1/datasources/{id}/rows` | 分页浏览（Phase 4D：`offset/limit/sort/dir`） |
| GET  | `/api/v1/datasources/{id}/tables` | 列出该数据源下所有表 |
| GET  | `/api/v1/datasources/{id}/lineage` | 返回该数据源的查询历史（Phase 4D lineage） |

### 会话 & 对话

| 方法 | 路径 | 用途 |
|------|------|------|
| POST | `/api/v1/sessions` | 创建新会话，返回 session_id 和初始 payload |
| GET  | `/api/v1/sessions/{id}` | 读取会话（含 `ttl_seconds`） |
| PATCH| `/api/v1/sessions/{id}` | 合并更新字段，重置 TTL |
| DELETE| `/api/v1/sessions/{id}` | 显式删除会话 |
| POST | `/api/v1/chat` | 同步发送消息，返回完整响应 |
| POST | `/api/v1/chat/stream` | SSE 流式发送，事件类型见下 |
| GET  | `/api/v1/health` | 健康检查（包含 redis 状态） |

### 绑定规则

- 创建会话时不绑定数据源；第一次 `/chat` 请求带 `data_source_id` 时绑定。
- 绑定后再用 `data_source_id` 调 `/chat`，若与绑定值不一致返回 `403`。
- 前端收到 403 会自动重置会话，用户重新发送即可。

### `/chat/stream` 事件协议

- `event: token` — 增量 token
- `event: tool_call` — Agent 调用工具（query_database / get_table_schema / create_chart）
- `event: tool_result` — 工具返回结果
- `event: chart` — ECharts option，前端直接渲染
- `event: end` — 一轮对话结束
- `event: error` — 出错（含 `code` 和 `message`）

## Agent 工具

后端 LangGraph Agent 注册了以下工具：

- `query_database(sql_query)`: 执行只读 SQL，返回 JSON
- `get_table_schema()`: 读取 `uploaded_data` 表结构
- `create_chart(chart_type, title, x_data, series)`: 注册一个图表，
  后处理节点会把它转换为 ECharts option 并写入 `chart_data` 字段

## 安全说明（Phase 4）

### 认证 & 多租户

- JWT access + refresh token，refresh 轮换 + 吊销（登出立即失效）
- 邮箱+密码注册，bcrypt 哈希
- 所有数据源 / 会话带 `owner_id`，跨用户访问统一返回 404（不暴露存在性）
- 启动时 `migrate_ownerless_data` 把旧的无主数据回填到默认 admin

### 限流（Phase 4B）

- `/auth/login`、`/auth/register`、`/auth/refresh` 走 per-IP 限流（默认 20/分钟，register 减半）
- `/chat`、`/chat/stream`、`/upload` 走 per-user 限流（默认 60/分钟）
- Redis ZSET 滑动窗口，超限返回 429 + `Retry-After`
- Redis 故障时 fail open（不锁死 API）

### SQL 沙箱（Phase 4C）

- SQL 工具只接受 `SELECT` / `WITH` 查询，禁止 `INSERT/UPDATE/DELETE/DROP/...` 关键字和 `;` 反引号
- 结果限制 100 行
- 上传文件大小限制 50MB
- 会话与数据源绑定后，禁止跨数据源访问
- 没有 Python REPL 沙箱，分析全部走 SQL

### PII 脱敏（Phase 4C，4 层防御）

1. **上传时**：写入 SQLite 前对 email / 手机号 / 身份证列做掩码
2. **查询出口**：`/datasources/{id}/rows` 返回前再过一遍 `mask_rows`
3. **审计日志**：写入 lineage 前脱敏
4. **LLM prompt**：送给模型的样本数据脱敏

### 启动校验

- `JWT_SECRET` 必须是 ≥32 字节且不是占位符（`LLM_MOCK=1` 或 `JWT_SECRET_DEV_OK=1` 可豁免本地开发）
- `init_users_table` 失败直接 crash，避免带病启动

## 目录

```
backend/
  app/
    api/         FastAPI 路由 + 中间件（rate limit）
    agents/      LangGraph 图、状态、工具、mock LLM
    services/    auth / chat / data / metadata / session / streaming
    utils/       数据库、日志、PII 脱敏、限流
    config.py
    main.py
  tests/         pytest 单测 + HTTP 集成测试
  requirements.txt
  Dockerfile
  environment.yml
frontend/
  src/
    components/  Chat / Upload / Sidebar / Chart
    hooks/       useChat, useUpload
    pages/       Home, Analysis
    services/    API 封装（含 SSE 解析）
    store/       zustand 全局状态（authStore 等）
    types/
    utils/
  e2e/           Playwright E2E
  index.html
  vite.config.ts
  playwright.config.ts
  tsconfig.json
  package.json
docker-compose.yml
```
