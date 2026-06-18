"""Phase 5C: dump main DB + Redis to MinIO.

Runs as a one-shot command (docker compose run --rm backup python scripts/backup.py).
Scheduling is external — cron / K8s CronJob / systemd timer. The script is
idempotent and safe to re-run: each backup lands under a date-prefixed key,
and old backups past BACKUP_RETENTION_DAYS are pruned at the end.

Env:
  - DATABASE_URL        — SQLAlchemy URL of the main DB (sqlite by default)
  - REDIS_URL           — Redis URL (we BGSAVE + copy dump.rdb)
  - MINIO_ENDPOINT      — e.g. http://minio:9000
  - MINIO_ACCESS_KEY    — S3 access key
  - MINIO_SECRET_KEY    — S3 secret key
  - MINIO_BUCKET        — bucket name (created if missing)
  - BACKUP_RETENTION_DAYS — delete backups older than this (default 7)

Exit code: 0 on success, non-zero on any failure (so the scheduler can
detect + alert). All steps log to stdout in JSON so they land in the
same pipeline as backend logs.
"""

from __future__ import annotations

import os
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

_RETENTION_DEFAULT = 7


def _log(msg: str, **kw) -> None:
    # Minimal JSON line — matches the backend's log schema so the same
    # pipeline (Loki / ELK) can ingest backup logs too.
    import json

    payload = {"timestamp": datetime.now(UTC).isoformat(), "logger": "backup", "message": msg}
    payload.update(kw)
    print(json.dumps(payload, ensure_ascii=False), flush=True)


def _s3_client():
    return boto3.client(
        "s3",
        endpoint_url=os.environ["MINIO_ENDPOINT"],
        aws_access_key_id=os.environ["MINIO_ACCESS_KEY"],
        aws_secret_access_key=os.environ["MINIO_SECRET_KEY"],
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

    The Redis dump.rdb path is exposed via CONFIG GET dir + dbfilename.
    If Redis is configured to disallow CONFIG (renamed commands), this
    fails — caller should set REDIS_DUMP_PATH env as a fallback.
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

    dump_path_env = os.environ.get("REDIS_DUMP_PATH")
    if dump_path_env:
        src = Path(dump_path_env)
    else:
        d = r.config_get("dir").get("dir", "/data")
        fname = r.config_get("dbfilename").get("dbfilename", "dump.rdb")
        src = Path(d) / fname
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
    required = ["MINIO_ENDPOINT", "MINIO_ACCESS_KEY", "MINIO_SECRET_KEY", "MINIO_BUCKET"]
    missing = [k for k in required if not os.environ.get(k)]
    if missing:
        _log("missing required env", keys=missing)
        return 2

    db_url = os.environ.get("DATABASE_URL", "sqlite:///./data/main.db")
    redis_url = os.environ.get("REDIS_URL", "redis://localhost:6379/0")
    bucket = os.environ["MINIO_BUCKET"]
    retention = int(os.environ.get("BACKUP_RETENTION_DAYS", _RETENTION_DEFAULT))

    s3 = _s3_client()
    _ensure_bucket(s3, bucket)

    date_str = datetime.now(UTC).strftime("%Y-%m-%d")
    ts_str = datetime.now(UTC).strftime("%H%M%S")
    prefix = f"backups/{date_str}/"

    with tempfile.TemporaryDirectory() as tmpdir:
        tmp = Path(tmpdir)
        db_dest = tmp / f"main-{ts_str}.db"
        rdb_dest = tmp / f"dump-{ts_str}.rdb"

        try:
            _dump_sqlite(db_url, db_dest)
            _dump_redis(redis_url, rdb_dest)
        except Exception as e:  # noqa: BLE001
            _log("dump failed", error=str(e))
            return 1

        _upload(s3, bucket, f"{prefix}main-{ts_str}.db", db_dest)
        _upload(s3, bucket, f"{prefix}dump-{ts_str}.rdb", rdb_dest)

    _prune_old(s3, bucket, "backups/", retention)
    _log("backup complete", bucket=bucket, prefix=prefix)
    return 0


if __name__ == "__main__":
    sys.exit(main())
