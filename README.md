# Data Analysis Agent
1
让用户上传 CSV/Excel，用自然语言提问，LangGraph Agent 自动生成 SQL 查询数据并回答。

## 架构

- **Backend**: FastAPI + LangGraph + LangChain + SQLAlchemy (SQLite)
- **Frontend**: React + Vite + TypeScript
- **LLM**: OpenAI 兼容接口

每个上传的文件落到独立的 SQLite 文件（`data/sqlite/{file_id}.db`），表名固定 `uploaded_data`，互不干扰。

## 本地开发

### 1. 后端

需要 Python 3.11+。

```bash
cd backend
cp .env.example .env
# 编辑 .env 填入 OPENAI_API_KEY

python -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\activate
pip install -r requirements.txt

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

访问 http://localhost:5173。Vite 已配 `/api` 代理到 `http://localhost:8000`。

### 3. Docker（可选）

```bash
cp .env.example .env  # 项目根目录
# 编辑 .env 填入 OPENAI_API_KEY
docker compose up --build
```

- 前端: http://localhost:5173
- 后端: http://localhost:8000
- Redis: localhost:6379

## API 概览

| 方法 | 路径 | 用途 |
|------|------|------|
| POST | `/api/v1/upload` | 上传 CSV/Excel，返回 `file_id` |
| GET  | `/api/v1/datasources` | 列出已上传数据源 |
| GET  | `/api/v1/datasources/{id}/preview` | 预览数据前 5 行 |
| POST | `/api/v1/chat` | 发消息，body 含 `session_id`、`message`、`data_source_id` |
| GET  | `/api/v1/health` | 健康检查 |

## 安全说明

- SQL 工具只接受 `SELECT` / `WITH` 查询，禁止 `INSERT/UPDATE/DELETE/DROP/...` 关键字和 `;` 反引号
- 结果限制 100 行
- 上传文件大小限制 50MB
- **没有 Python REPL 沙箱**，因此 `analyze_with_python` 工具已被移除，分析全部走 SQL

## 目录

```
backend/
  app/
    api/         FastAPI 路由
    agents/      LangGraph 图与工具
    services/    数据加载
    utils/       数据库引擎、日志
    config.py
    main.py
  requirements.txt
  Dockerfile
frontend/
  src/
    components/  Chat/Upload/Sidebar/Chart
    hooks/       useChat, useUpload
    services/    api 封装
    types/
  index.html
  vite.config.ts
  tsconfig.json
  package.json
docker-compose.yml
```
