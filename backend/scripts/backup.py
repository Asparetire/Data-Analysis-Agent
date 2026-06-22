"""Phase 5C: dump main DB + Redis to MinIO.

Runs as a one-shot command (docker compose run --rm backup python scripts/backup.py).
Scheduling is external — cron / K8s CronJob / systemd timer. The script is
idempotent and safe to re-run: each backup lands under a date-prefixed key,
and old backups past BACKUP_RETENTION_DAYS are pruned at the end.

Config comes from app.config.settings (same pydantic Settings as the backend),
so the same env vars + .env.example apply. Required for a successful run:
MINIO_ENDPOINT / MINIO_ACCESS_KEY / MINIO_SECRET_KEY / MINIO_BUCKET.

Exit code: 0 on success, non-zero on any failure (so the scheduler can
detect + alert). All steps log to stdout in JSON so they land in the
same pipeline as backend logs.
"""

from __future__ import annotations

import json
import shutil
import sys
import tempfile
import time
from datetime import UTC, datetime
from pathlib import Path

import boto3
import redis
from botocore.client import Config as BotoConfig
from sqlalchemy import create_engine, text

# Import the shared Settings so backup.py and the backend agree on env
# var names + defaults. The script runs inside the backend container
# (see docker-compose.yml `backup` service), so the import path is the
# same as in-app code.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from app.config import settings  # noqa: E402


def _log(msg: str, level: str = "info", **kw) -> None:
    # JSON line matching the backend's schema (timestamp/level/logger/message)
    # so the same Loki/ELK pipeline ingests backup logs without special-casing.
    payload = {
        "timestamp": datetime.now(UTC).isoformat(),
        "level": level,
        "logger": "backup",
        "message": msg,
    }
    payload.update(kw)
    print(json.dumps(payload, ensure_ascii=False), flush=True)


def _s3_client():
    return boto3.client(
        "s3",
        endpoint_url=settings.MINIO_ENDPOINT,
        aws_access_key_id=settings.MINIO_ACCESS_KEY,
        aws_secret_access_key=settings.MINIO_SECRET_KEY,
        # MinIO uses path-style addressing, not virtual-host-style.
        config=BotoConfig(signature_version="s3v4", s3={"addressing_style": "path"}),
        region_name="us-east-1",  # required by boto3 but ignored by MinIO
    )


def _ensure_bucket(s3, bucket: str) -> None:
    try:
        s3.head_bucket(Bucket=bucket)
    except Exception:  # noqa: BLE001
        s3.create_bucket(Bucket=bucket)
        _log("created bucket", bucket=bucket)


def _dump_sqlite(db_url: str, dest: Path) -> None:
    """VACUUM INTO a snapshot file — atomic, doesn't block writers."""
    engine = create_engine(db_url, future=True)
    with engine.connect() as conn:
        # `VACUUM INTO` writes a consistent snapshot to a new file.
        # The dest path must not exist (SQLite refuses to overwrite).
        conn.execute(text(f"VACUUM INTO '{dest.as_posix()}'"))
    engine.dispose()
    _log("dumped sqlite", path=str(dest), size=dest.stat().st_size)


def _dump_redis(redis_url: str, dest: Path) -> None:
    """BGSAVE + wait for LASTSAVE to advance, then copy dump.rdb.

    The dump.rdb path is resolved in order: REDIS_DUMP_PATH (explicit
    override for hardened Redis that renames CONFIG), then CONFIG GET
    dir+dbfilename. Failing both, fall back to the Docker-default
    /data/dump.rdb (redis:7-alpine writes there).
    """
    r = redis.Redis.from_url(redis_url, decode_responses=True)
    before = int(r.lastsave())
    r.bgsave()
    # Wait up to 60s for BGSAVE to finish. LASTSAVE advances when done.
    for _ in range(60):
        time.sleep(1)
        after = int(r.lastsave())
        if after > before:
            break
    else:
        raise RuntimeError("redis BGSAVE did not complete within 60s")

    if settings.REDIS_DUMP_PATH:
        src = Path(settings.REDIS_DUMP_PATH)
    else:
        try:
            d = r.config_get("dir").get("dir", "/data")
            fname = r.config_get("dbfilename").get("dbfilename", "dump.rdb")
            src = Path(d) / fname
        except redis.exceptions.ResponseError:
            # CONFIG renamed/disabled (common production hardening) and no
            # REDIS_DUMP_PATH set — last resort: the redis:7-alpine default.
            src = Path("/data/dump.rdb")
    shutil.copy2(src, dest)
    _log("dumped redis", path=str(dest), size=dest.stat().st_size)


def _upload(s3, bucket: str, key: str, path: Path) -> None:
    s3.upload_file(str(path), bucket, key)
    _log("uploaded", bucket=bucket, key=key, size=path.stat().st_size)


def _prune_old(s3, bucket: str, prefix: str, retention_days: int) -> None:
    """Delete backup objects older than retention_days under prefix."""
    cutoff = time.time() - retention_days * 86400
    paginator = s3.get_paginator("list_objects_v2")
    deleted = 0
    for page in paginator.paginate(Bucket=bucket, Prefix=prefix):
        for obj in page.get("Contents", []):
            if obj["LastModified"].timestamp() < cutoff:
                s3.delete_object(Bucket=bucket, Key=obj["Key"])
                deleted += 1
    if deleted:
        _log("pruned old backups", deleted=deleted, retention_days=retention_days)


def main() -> int:
    missing = [
        k
        for k, v in {
            "MINIO_ENDPOINT": settings.MINIO_ENDPOINT,
            "MINIO_ACCESS_KEY": settings.MINIO_ACCESS_KEY,
            "MINIO_SECRET_KEY": settings.MINIO_SECRET_KEY,
            "MINIO_BUCKET": settings.MINIO_BUCKET,
        }.items()
        if not v
    ]
    if missing:
        _log("missing required config", level="error", keys=missing)
        return 2

    s3 = _s3_client()
    _ensure_bucket(s3, settings.MINIO_BUCKET)

    date_str = datetime.now(UTC).strftime("%Y-%m-%d")
    ts_str = datetime.now(UTC).strftime("%H%M%S")
    prefix = f"backups/{date_str}/"

    with tempfile.TemporaryDirectory() as tmpdir:
        tmp = Path(tmpdir)
        db_dest = tmp / f"main-{ts_str}.db"
        rdb_dest = tmp / f"dump-{ts_str}.rdb"

        try:
            _dump_sqlite(settings.DATABASE_URL, db_dest)
            _dump_redis(settings.REDIS_URL, rdb_dest)
        except Exception as e:  # noqa: BLE001
            _log("dump failed", level="error", error=str(e))
            return 1

        _upload(s3, settings.MINIO_BUCKET, f"{prefix}main-{ts_str}.db", db_dest)
        _upload(s3, settings.MINIO_BUCKET, f"{prefix}dump-{ts_str}.rdb", rdb_dest)

    _prune_old(s3, settings.MINIO_BUCKET, "backups/", settings.BACKUP_RETENTION_DAYS)
    _log("backup complete", bucket=settings.MINIO_BUCKET, prefix=prefix)
    return 0


if __name__ == "__main__":
    sys.exit(main())
