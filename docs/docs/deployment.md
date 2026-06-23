# 部署

本项目提供两种 compose 形态：

- **`docker-compose.yml`** — 从源码 build，适合开发 / 临时部署
- **`docker-compose.prod.yml`** — 拉 ghcr.io 预构建镜像，适合生产

## 生产部署（推荐：预构建镜像）

### 前置

- Docker 24+ / Docker Compose v2
- 一个 Prometheus scraper 的 IP/CIDR（用于 `/metrics` allowlist）
- LLM provider 凭证（OpenAI 或 Anthropic 兼容）
- TLS 终结（云 LB / Cloudflare / Caddy 等都行，nginx 只听 80）

### 1. 拉文件

```bash
mkdir data-analysis-agent && cd data-analysis-agent
curl -LO https://raw.githubusercontent.com/AAsparetire/data-analysis-agent/main/docker-compose.prod.yml
curl -LO https://raw.githubusercontent.com/AAsparetire/data-analysis-agent/main/.env.example
cp .env.example .env
```

### 2. 编辑 .env

```bash
# 必填（启动校验会拒绝占位符）
JWT_SECRET=$(openssl rand -hex 32)
MIGRATION_ADMIN_PASSWORD=$(openssl rand -base64 24)
MINIO_ROOT_PASSWORD=$(openssl rand -base64 24)

# LLM
LLM_PROVIDER=openai
OPENAI_API_KEY=sk-...

# Phase 6: 必填或确认默认
METRICS_ALLOW_CIDR=10.0.0.5/32     # 你的 Prometheus IP
TRUSTED_PROXIES=10.0.0.0/8,172.16.0.0/12,192.168.0.0/16
MAX_CONCURRENT_SSE_PER_USER=5
LLM_REQUEST_TIMEOUT_S=60.0
```

### 3. 启动

```bash
docker compose -f docker-compose.prod.yml up -d
```

服务清单：

| 服务 | 暴露 | 说明 |
|------|------|------|
| nginx | 80 | 入口，serve 前端静态 + 反代 /api + 限制 /metrics |
| backend | 仅 compose 网络内 | FastAPI |
| redis | 仅 compose 网络内 | 会话 + 限流 + 查询缓存 + SSE 计数 |
| minio | 仅 compose 网络内 | 备份目标（控制台不对外）|

### 4. 验证

```bash
curl http://localhost/api/v1/health/ready
# {"status":"ok","redis":true,"db":true,"version":"0.2.0","uptime_seconds":...}
```

## 开发部署（从源码 build）

```bash
git clone https://github.com/AAsparetire/data-analysis-agent.git
cd data-analysis-agent
cp .env.example .env  # 编辑必填项
docker compose up --build
```

## 启动校验

后端启动时会 fatal 退出（process dies → orchestrator restart → operator sees failure）的情况：

| 条件 | 错误信息 |
|------|---------|
| `JWT_SECRET` 是占位符或 <32 字节 | `JWT_SECRET is the committed placeholder or shorter than 32 bytes.` |
| `MIGRATION_ADMIN_PASSWORD` 是占位符或 <12 字节 | `MIGRATION_ADMIN_PASSWORD is the committed placeholder or shorter than 12 bytes.` |

豁免：`LLM_MOCK=1`（E2E）或 `JWT_SECRET_DEV_OK=1`（本地 dev）。

## 备份与恢复

### 手动触发一次备份

```bash
docker compose -f docker-compose.prod.yml run --rm backup
```

会在 MinIO 的 `data-analysis-backups` bucket 下生成 `backups/YYYY-MM-DD/main-HHMMSS.db` 和 `dump-HHMMSS.rdb`。

### 定时备份

compose 不内置 cron。推荐两种方式：

**K8s CronJob**：

```yaml
apiVersion: batch/v1
kind: CronJob
metadata:
  name: data-analysis-backup
spec:
  schedule: "0 2 * * *"  # 每天 02:00
  jobTemplate:
    spec:
      template:
        spec:
          containers:
            - name: backup
              image: ghcr.io/aasparetire/data-analysis-agent-backend:latest
              command: ["python", "scripts/backup.py"]
              envFrom:
                - secretRef:
                    name: data-analysis-secrets
          restartPolicy: OnFailure
```

**systemd timer**：

```ini
# /etc/systemd/system/data-analysis-backup.service
[Service]
Type=oneshot
ExecStart=/usr/bin/docker compose -f /path/to/docker-compose.prod.yml run --rm backup
WorkingDirectory=/path/to/data-analysis-agent

# /etc/systemd/system/data-analysis-backup.timer
[Timer]
OnCalendar=*-*-* 02:00:00
Persistent=true

[Install]
WantedBy=timers.target
```

### 恢复

!!! danger "恢复会覆盖 live data"
    恢复前必须停 backend + redis，否则 SQLite 文件被占用导致损坏。

```bash
docker compose -f docker-compose.prod.yml stop backend redis
docker compose -f docker-compose.prod.yml run --rm restore -- --yes
docker compose -f docker-compose.prod.yml start redis backend
```

## 升级流程

### 预构建镜像形态

```bash
docker compose -f docker-compose.prod.yml pull
docker compose -f docker-compose.prod.yml up -d
```

如果想锁版本：在 `.env` 设 `IMAGE_TAG=v0.2.0`，再 pull + up。

### 源码形态

1. `git pull`
2. 看 commit log 是否有破坏性变更
3. 如果 `requirements.txt` 变了：`docker compose build backend`
4. 如果 `DATABASE_URL` schema 变了：先备份再升级
5. `docker compose up -d`

## 发版（ maintainer 用 ）

`v*` tag 推到 GitHub 自动触发 release workflow：

```bash
git tag v0.2.0
git push origin v0.2.0
```

workflow 构建 backend / nginx 镜像，推到 `ghcr.io/aasparetire/data-analysis-agent-backend:<version>` 和 `:latest`，同时建 GitHub Release（auto-generated changelog）。

## 监控接入 Prometheus

`/metrics` 已暴露 HTTP 指标 + 业务指标（LLM 调用 / cache / SSE）。在 Prometheus 的 `scrape_configs` 加：

```yaml
scrape_configs:
  - job_name: data-analysis-agent
    metrics_path: /metrics
    static_configs:
      - targets: ["<host>"]
```

业务指标（Phase 6 新增）：

| 指标 | 类型 | 含义 |
|------|------|------|
| `llm_calls_total{provider,status}` | counter | LLM 调用次数 |
| `llm_call_duration_seconds` | histogram | LLM 调用 wall-clock |
| `llm_tokens_used_total{provider,kind}` | counter | prompt / completion token |
| `query_cache_hits_total` / `query_cache_misses_total` | counter | 缓存命中率 |
| `sse_active_streams` | gauge | 活跃 SSE 连接 |
| `sse_rejected_total` | counter | 并发上限被拒次数 |

!!! note "`/metrics` 默认仅 loopback"
    `METRICS_ALLOW_CIDR` 默认 `127.0.0.1/32`。生产部署必须在 `.env` 显式设为
    Prometheus scraper 的 CIDR，否则抓不到指标。配置由 nginx envsubst 在
    容器启动时注入 `frontend.conf` 的 `allow` 指令。

## 健康检查

K8s / LB probe 配置：

```yaml
livenessProbe:
  httpGet:
    path: /api/v1/health/live
  initialDelaySeconds: 20

readinessProbe:
  httpGet:
    path: /api/v1/health/ready
  initialDelaySeconds: 5
```

- `/health/live` 恒 200，进程活着就 OK
- `/health/ready` 查 Redis + DB，任一失败 503，LB 应摘流量

## 生产 checklist

部署前过一遍：

- [ ] `JWT_SECRET` ≥32 字节且非占位符（启动校验会拒绝）
- [ ] `MIGRATION_ADMIN_PASSWORD` ≥12 字节、非占位符、含字母+数字（register() 复杂度策略）
- [ ] `MINIO_ROOT_PASSWORD` ≥8 字符
- [ ] `LLM_PROVIDER` + 对应 API key 配置正确
- [ ] `METRICS_ALLOW_CIDR` 设为 Prometheus 的 CIDR（默认仅 loopback）
- [ ] `TRUSTED_PROXIES` 包含反代容器 IP/CIDR（否则 per-IP 限流失效）
- [ ] `MAX_CONCURRENT_SSE_PER_USER` 按机器负载调（默认 5）
- [ ] `LLM_REQUEST_TIMEOUT_S` 按 provider 典型延迟调（默认 60s）
- [ ] TLS 在上游（云 LB / Cloudflare），nginx 只听 80
- [ ] 首次启动后立即改 admin 密码（用 `MIGRATION_ADMIN_EMAIL` 登录）
- [ ] 配置定时备份（见上）
- [ ] 配置 Prometheus 抓 `/metrics`

