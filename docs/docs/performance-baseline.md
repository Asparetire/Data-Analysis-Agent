# 性能基线

本页由 Phase 5E 的 k6 脚本 `perf/k6-baseline.js` 跑出。基线数会随代码和依赖变化漂移，重大改动后应重跑更新本页。

## 测试环境

| 项 | 值 |
|----|----|
| 硬件 | （待 5E 执行后填：CPU / RAM / SSD） |
| OS | Windows 11 |
| Docker | Docker Desktop（版本待填） |
| k6 | v0.50+ |
| Backend | LLM_MOCK=1（不调真实 LLM） |
| 负载 | 20 VU 恒定，30s |

## 端点基线

| 端点 | p50 | p95 | p99 | RPS | 说明 |
|------|-----|-----|-----|-----|------|
| `/api/v1/health/ready` | 待填 | | | | 无鉴权，纯依赖检查 |
| `/api/v1/auth/login` | 待填 | | | | bcrypt 哈希主导 |
| `/api/v1/datasources` | 待填 | | | | 文件系统 + sidecar JSON |
| `/api/v1/datasources/{id}/preview` | 待填 | | | | SQLite SELECT |
| `/api/v1/datasources/{id}/rows` | 待填 | | | | 分页 + PII 脱敏 |

## 跑基线

```bash
# 1. 起 backend（LLM_MOCK 模式，不依赖真实 LLM）
cd backend
LLM_MOCK=1 JWT_SECRET=test-only-32-bytes-long-aaaaaaaaaa \
  uvicorn app.main:app --port 8000 &

# 2. 注册一个测试用户 + 上传一个测试 CSV（脚本里硬编码或手动）
# ... 见 perf/k6-baseline.js 顶部注释

# 3. 跑 k6
k6 run perf/k6-baseline.js
```

## 不测的端点

- `/api/v1/chat`、`/api/v1/chat/stream` — LLM 调用延迟由 provider 决定，基线无意义
- `/api/v1/upload` — 涉及文件解析 + SQLite 入库，I/O 主导，不适合恒定 VU 模型

## Threshold

脚本内置 threshold，跑失败会非 0 退出，方便 CI 卡：

- p95 < 500ms（除 `/auth/login` 因 bcrypt 允许 p95 < 1500ms）
- http_req_failed < 1%
