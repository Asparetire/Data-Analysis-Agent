# 性能基线

本页由 Phase 5E 的 k6 脚本 `perf/k6-baseline.js` 跑出。基线数会随代码和依赖变化漂移，重大改动后应重跑更新本页。

## 如何产生基线

```bash
# 1. 起 backend（mock 模式，不依赖真实 LLM）
cd backend
LLM_MOCK=1 JWT_SECRET=test-only-32-bytes-long-aaaaaaaaaa \
  DATABASE_URL=sqlite:///./data-perf/main.db \
  DATA_DIR=./data-perf \
  uvicorn app.main:app --port 8000 &

# 2. 注册测试用户 + 上传测试 CSV（拿 access_token + file_id）
#    完整步骤见 perf/README.md
TEST_DATASOURCE_ID=$DS_ID k6 run perf/k6-baseline.js
```

k6 默认输出每个端点的 p50/p90/p95/p99 + RPS + 失败率，threshold 内置在脚本里（跑失败非 0 退出）。

## 测试环境

跑基线前填写本次环境（基线数随硬件 / 负载漂移，无环境信息则无意义）：

| 项 | 值 |
|----|----|
| 硬件 | CPU / RAM / SSD（本机跑后填） |
| OS | （填） |
| Docker | （填，如未用 Docker 可删） |
| k6 | （填，`k6 version`） |
| Backend | LLM_MOCK=1（不调真实 LLM） |
| 负载 | 20 VU 恒定，30s |

## 端点基线

跑完 k6 后把数字填入下表。未填代表该次基线未覆盖。

| 端点 | p50 | p95 | p99 | RPS | 说明 |
|------|-----|-----|-----|-----|------|
| `/api/v1/health/ready` | | | | | 无鉴权，纯依赖检查 |
| `/api/v1/auth/login` | | | | | bcrypt 哈希主导 |
| `/api/v1/datasources` | | | | | 文件系统 + sidecar JSON |
| `/api/v1/datasources/{id}/preview` | | | | | SQLite SELECT |
| `/api/v1/datasources/{id}/rows` | | | | | 分页 + PII 脱敏 |

## 不测的端点

- `/api/v1/chat`、`/api/v1/chat/stream` — LLM 调用延迟由 provider 决定，基线无意义
- `/api/v1/upload` — 涉及文件解析 + SQLite 入库，I/O 主导，不适合恒定 VU 模型

## Threshold

脚本内置 threshold，跑失败会非 0 退出，方便 CI 卡：

- `http_req_duration{endpoint:health}` p95 < 200ms
- `http_req_duration{endpoint:login}` p95 < 1500ms（bcrypt 故意慢）
- `http_req_duration{endpoint:datasources|preview|rows}` p95 < 500ms
- `http_req_failed` rate < 1%
