# 性能基线 (k6)

`k6-baseline.js` 跑非 LLM 端点的延迟基线，结果填到 `docs/docs/performance-baseline.md`。

## 安装 k6

| 平台 | 命令 |
|------|------|
| Windows | `winget install grafana.k6` |
| macOS | `brew install k6` |
| Linux | 见 https://k6.io/docs/get-started/installation/ |
| Docker | `docker run --rm -i grafana/k6 run - < perf/k6-baseline.js` |

## 准备

```bash
# 1. 起 backend（mock 模式，不依赖真实 LLM）
cd backend
mkdir -p data-perf
LLM_MOCK=1 \
  JWT_SECRET=test-only-32-bytes-long-aaaaaaaaaa \
  DATABASE_URL=sqlite:///./data-perf/main.db \
  DATA_DIR=./data-perf \
  uvicorn app.main:app --port 8000 &

# 2. 注册测试用户 + 上传测试 CSV
curl -X POST http://localhost:8000/api/v1/auth/register \
  -H 'Content-Type: application/json' \
  -d '{"email":"perf@example.com","password":"perf-password-123"}'
# 拿 access_token，再 upload 一个 CSV，记下返回的 file_id
TOKEN=$(curl -sX POST http://localhost:8000/api/v1/auth/login \
  -H 'Content-Type: application/json' \
  -d '{"email":"perf@example.com","password":"perf-password-123"}' | python -c "import sys,json;print(json.load(sys.stdin)['access_token'])")
DS_ID=$(curl -sX POST http://localhost:8000/api/v1/upload \
  -H "Authorization: Bearer $TOKEN" \
  -F 'file=@some-test.csv;type=text/csv' | python -c "import sys,json;print(json.load(sys.stdin)['file_id'])")
echo "DS_ID=$DS_ID"
```

## 跑基线

```bash
TEST_DATASOURCE_ID=$DS_ID k6 run perf/k6-baseline.js
```

## 输出

k6 默认输出每个 endpoint 的 p50/p90/p95/p99 + RPS + 失败率。把数字填到 `docs/docs/performance-baseline.md` 的表格里。

## Threshold

脚本内置 threshold，失败会非 0 退出：

- `http_req_duration{endpoint:health}` p95 < 200ms
- `http_req_duration{endpoint:login}` p95 < 1500ms（bcrypt 故意慢）
- `http_req_duration{endpoint:datasources|preview|rows}` p95 < 500ms
- `http_req_failed` rate < 1%

CI 可以用 `k6 run --out cloud` 或 `k6 run --out json=results.json` 收集历史趋势。

## 不测的端点

- `/api/v1/chat`、`/api/v1/chat/stream` — LLM 延迟由 provider 决定，基线无意义
- `/api/v1/upload` — I/O 主导（文件解析 + SQLite 入库），不适合恒定 VU 模型
