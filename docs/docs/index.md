# Data Analysis Agent

让用户上传 CSV / Excel / JSON，用自然语言提问，LangGraph Agent 自动生成 SQL 查询数据并回答，必要时返回 ECharts 图表配置供前端渲染。

## 特性

- **自然语言查询**：用中文或英文提问，Agent 自动生成并执行 SQL
- **多格式上传**：CSV / Excel (xlsx, xls) / JSON
- **图表自动生成**：Agent 决定是否需要可视化，返回 ECharts option
- **流式响应**：SSE 推送 token / 工具调用 / 图表事件
- **多租户**：JWT 认证 + 数据源 ACL，A 用户看不到 B 用户的数据
- **限流**：per-IP 限流防爆破，per-user 限流防滥用
- **PII 脱敏**：上传 / 查询 / 日志 / prompt 四层防御
- **可观测性**：结构化 JSON 日志 + request_id + Prometheus metrics
- **备份**：SQLite + Redis 定时备份到 MinIO (S3 兼容)

## 快速开始

```bash
# 1. 后端
cd backend
conda create -n data-analysis-agent python=3.11 -y
conda activate data-analysis-agent
pip install -r requirements.txt
cp .env.example .env  # 填入 OPENAI_API_KEY / JWT_SECRET（≥32 字节）
redis-server  # 另开终端
uvicorn app.main:app --reload --port 8000

# 2. 前端
cd frontend
npm install
npm run dev  # http://localhost:5173

# 3. 生产形态（Docker）
cp .env.example .env  # 项目根目录
docker compose up --build  # http://localhost
```

## 文档导航

- [用户手册](user-manual.md) — 注册、上传、对话、数据源管理
- [API](api.md) — HTTP 端点清单（详细规格走 Swagger `/docs`）
- [架构](architecture.md) — 后端组件、数据流
- [部署](deployment.md) — Docker、env、备份恢复、升级
- [性能基线](performance-baseline.md) — k6 跑出的端点延迟基线

## 技术栈

| 层 | 技术 |
|----|------|
| 后端 | FastAPI + LangGraph + LangChain + SQLAlchemy (SQLite) + Redis |
| 前端 | React + Vite + TypeScript + ECharts + Zustand |
| LLM | OpenAI 兼容 或 Anthropic 兼容（`LLM_PROVIDER` 切换） |
| 监控 | Prometheus + 结构化 JSON 日志 |
| 备份 | MinIO (S3 兼容) |
| 文档 | MkDocs Material |
