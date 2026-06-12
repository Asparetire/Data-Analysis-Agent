# Data Analysis Agent

让用户上传 CSV/Excel，用自然语言提问，LangGraph Agent 自动生成 SQL 查询数据并回答，
必要时返回 ECharts 图表配置供前端渲染。

## 架构

- **Backend**: FastAPI + LangGraph + LangChain + SQLAlchemy (SQLite) + Redis
- **Frontend**: React + Vite + TypeScript + ECharts + Zustand
- **LLM**: OpenAI 兼容接口

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
# 编辑 .env 填入 OPENAI_API_KEY 和 REDIS_URL

# 启动 Redis（也可直接用 docker compose up redis）
redis-server
# 或者：docker run -p 6379:6379 -d redis:7-alpine

uvicorn app.main:app --reload --port 8000
```

启动后访问 http://localhost:8000/docs 看 API 文档。

### 2. 前端

需要 Node 18+。

```bash
cd frontend
npm install
npm run dev
```

访问 http://localhost:5173 。Vite 已配 `/api` 代理到 `http://localhost:8000`。

### 3. Docker（可选）

```bash
cp .env.example .env  # 项目根目录
# 编辑 .env 填入 OPENAI_API_KEY
docker compose up --build
```

- 前端: http://localhost:5173
- 后端: http://localhost:8000
- Redis: localhost:6379

## 会话管理

后端用 Redis 保存会话，键 `session:{session_id}`，TTL 30 分钟，任何写操作都会重置 TTL。

字段：
- `session_id` (UUID4)
- `data_source_id` (绑定的数据源，绑定后禁止跨数据源访问)
- `chat_history` (对话记录，每条含 `role/content/timestamp/chart_data/sql_query`)
- `intermediate_results` (最近一次查询结果 JSON 快照)
- `last_query` (最近一次 SQL)
- `created_at` / `updated_at`

## API 概览

| 方法 | 路径 | 用途 |
|------|------|------|
| POST | `/api/v1/upload` | 上传 CSV/Excel/JSON，返回 `file_id` |
| GET  | `/api/v1/datasources` | 列出已上传数据源 |
| GET  | `/api/v1/datasources/{id}/preview` | 预览数据前 N 行 |
| GET  | `/api/v1/datasources/{id}/schema` | 返回字段名与推断类型 |
| POST | `/api/v1/chat` | 发送消息，body 含 `session_id`、`message`、`data_source_id` |
| POST | `/api/v1/sessions` | 创建新会话，返回 session_id 和初始 payload |
| GET  | `/api/v1/sessions/{id}` | 读取会话（含 `ttl_seconds`） |
| PATCH| `/api/v1/sessions/{id}` | 合并更新字段，重置 TTL |
| DELETE| `/api/v1/sessions/{id}` | 显式删除会话 |
| GET  | `/api/v1/health` | 健康检查（包含 redis 状态） |

### 绑定规则

- 创建会话时不绑定数据源；第一次 `/chat` 请求带 `data_source_id` 时绑定。
- 绑定后再用 `data_source_id` 调 `/chat`，若与绑定值不一致返回 `403`。
- 前端收到 403 会自动重置会话，用户重新发送即可。

## Agent 工具

后端 LangGraph Agent 注册了以下工具：

- `query_database(sql_query)`: 执行只读 SQL，返回 JSON
- `get_table_schema()`: 读取 `uploaded_data` 表结构
- `create_chart(chart_type, title, x_data, series)`: 注册一个图表，
  后处理节点会把它转换为 ECharts option 并写入 `chart_data` 字段

## 安全说明

- SQL 工具只接受 `SELECT` / `WITH` 查询，禁止 `INSERT/UPDATE/DELETE/DROP/...` 关键字和 `;` 反引号
- 结果限制 100 行
- 上传文件大小限制 50MB
- 会话与数据源绑定后，禁止跨数据源访问
- 没有 Python REPL 沙箱，分析全部走 SQL

## 目录

```
backend/
  app/
    api/         FastAPI 路由
    agents/      LangGraph 图、状态、工具
    services/    数据加载、chat 编排、session 管理
    utils/       数据库、日志
    config.py
    main.py
  requirements.txt
  Dockerfile
  environment.yml
frontend/
  src/
    components/  Chat / Upload / Sidebar / Chart
    hooks/       useChat, useUpload
    pages/       Home, Analysis
    services/    API 封装
    store/       zustand 全局状态
    types/
    utils/
  index.html
  vite.config.ts
  tsconfig.json
  package.json
docker-compose.yml
```