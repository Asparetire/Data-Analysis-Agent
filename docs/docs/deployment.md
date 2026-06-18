# 部署

## Docker Compose（推荐）

### 1. 配置环境变量

项目根目录 `.env`（从 `.env.example` 复制）：

```bash
# 必填
JWT_SECRET=please-override-with-a-long-random-string-32-bytes-min
MIGRATION_ADMIN_PASSWORD=change-me-now
MINIO_ROOT_PASSWORD=please-change-min-8-chars

# LLM（按 provider 填）
LLM_PROVIDER=openai  # 或 anthropic
OPENAI_API_KEY=sk-...
OPENAI_MODEL=gpt-4o

# 可选（有默认）
ACCESS_TOKEN_TTL_MINUTES=15
REFRESH_TOKEN_TTL_DAYS=7
RATE_LIMIT_PER_USER_PER_MINUTE=60
RATE_LIMIT_PER_IP_PER_MINUTE=20
LOG_FORMAT=json
```

!!! warning "JWT_SECRET 必须 ≥32 字节"
    启动校验会拒绝占位符或短 secret。`LLM_MOCK=1` 或 `JWT_SECRET_DEV_OK=1` 可豁免（仅本地开发）。

### 2. 启动

```bash
docker compose up --build
```

服务清单：

| 服务 | 端口 | 说明 |
|------|------|------|
| nginx | 80 | 入口，serve 前端静态 + 反代 /api |
| backend | 8000（仅 compose 网络内） | FastAPI |
| redis | 6379 | 会话 + 限流 |
| minio | 9000 / 9001 | 备份目标 / 控制台 |

### 3. 访问

- 应用：http://localhost
- MinIO 控制台：http://localhost:9001（用 `MINIO_ROOT_USER` / `MINIO_ROOT_PASSWORD` 登录）
- Swagger：http://localhost/api/v1/docs
- Prometheus metrics：http://localhost/metrics

## 备份与恢复

### 手动触发一次备份

```bash
docker compose run --rm backup
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
              image: <your-backend-image>
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
ExecStart=/usr/bin/docker compose -f /path/to/docker-compose.yml run --rm backup
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
docker compose stop backend redis
docker compose run --rm backup python scripts/restore.py --yes
docker compose start redis backend
```

## 升级流程

1. `git pull`
2. 看 `CHANGELOG` 或 commit log 是否有破坏性变更
3. 如果 `requirements.txt` 变了：`docker compose build backend`
4. 如果 `DATABASE_URL` schema 变了：先备份再升级
5. `docker compose up -d`

## 监控接入 Prometheus

`/metrics` 已暴露标准 HTTP 指标。在 Prometheus 的 `scrape_configs` 加：

```yaml
scrape_configs:
  - job_name: data-analysis-agent
    metrics_path: /metrics
    static_configs:
      - targets: ["<host>"]
```

!!! note "生产限制 /metrics 访问"
    `/metrics` 不鉴权。生产应在 nginx 层限制只允许 Prometheus scraper IP：

    ```nginx
    location /metrics {
        allow 10.0.0.5;  # Prometheus IP
        deny all;
        proxy_pass http://backend:8000;
    }
    ```

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
