"""Phase 5C: restore main DB + Redis from the latest MinIO backup.

Interactive by default — prompts before overwriting live data. Pass
``--yes`` to skip the prompt (K8s init-container / scripted restore).

This does NOT stop the backend for you — running it while the backend
holds the SQLite file open will leave the DB in a torn state. Stop
the backend first, run restore, restart.

Env: same as backup.py (MINIO_*, DATABASE_URL, REDIS_URL).
"""

from __future__ import annotations

import argparse
import os
import shutil
import sys
import tempfile
from pathlib import Path

import boto3
from botocore.client import Config as BotoConfig
from sqlalchemy.engine.url import make_url


def _log(msg: str, **kw) -> None:
    import json

    payload = {"logger": "restore", "message": msg}
    payload.update(kw)
    print(json.dumps(payload, ensure_ascii=False), flush=True)


def _s3_client():
    return boto3.client(
        "s3",
        endpoint_url=os.environ["MINIO_ENDPOINT"],
        aws_access_key_id=os.environ["MINIO_ACCESS_KEY"],
        aws_secret_access_key=os.environ["MINIO_SECRET_KEY"],
        config=BotoConfig(signature_version="s3v4", s3={"addressing_style": "path"}),
        region_name="us-east-1",
    )


def _latest_keys(s3, bucket: str) -> tuple[str, str]:
    """Return (db_key, rdb_key) for the newest backup under backups/."""
    paginator = s3.get_paginator("list_objects_v2")
    all_objs = []
    for page in paginator.paginate(Bucket=bucket, Prefix="backups/"):
        all_objs.extend(page.get("Contents", []))
    if not all_objs:
        raise RuntimeError("no backups found")
    all_objs.sort(key=lambda o: o["LastModified"], reverse=True)
    db_key = next((o["Key"] for o in all_objs if o["Key"].endswith(".db")), None)
    rdb_key = next((o["Key"] for o in all_objs if o["Key"].endswith(".rdb")), None)
    if not db_key or not rdb_key:
        raise RuntimeError(f"latest backup incomplete: db={db_key} rdb={rdb_key}")
    return db_key, rdb_key


def _main_db_path(db_url: str) -> Path:
    url = make_url(db_url)
    if not url.database:
        raise RuntimeError("in-memory DB cannot be restored")
    p = Path(url.database)
    return p if p.is_absolute() else Path.cwd() / p


def _redis_dump_path(redis_url: str) -> Path:
    """Default redis dump path. Caller can override via REDIS_DUMP_PATH env."""
    env_path = os.environ.get("REDIS_DUMP_PATH")
    if env_path:
        return Path(env_path)
    # Standard redis: /data/dump.rdb (matches the official redis:7-alpine
    # container and our docker-compose redis_data volume mount).
    return Path("/data/dump.rdb")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--yes", action="store_true", help="skip confirmation prompt")
    args = parser.parse_args()

    required = ["MINIO_ENDPOINT", "MINIO_ACCESS_KEY", "MINIO_SECRET_KEY", "MINIO_BUCKET"]
    missing = [k for k in required if not os.environ.get(k)]
    if missing:
        _log("missing required env", keys=missing)
        return 2

    db_url = os.environ.get("DATABASE_URL", "sqlite:///./data/main.db")
    redis_url = os.environ.get("REDIS_URL", "redis://localhost:6379/0")
    bucket = os.environ["MINIO_BUCKET"]

    s3 = _s3_client()
    db_key, rdb_key = _latest_keys(s3, bucket)
    _log("latest backup", db_key=db_key, rdb_key=rdb_key)

    if not args.yes:
        print(
            f"\n  About to OVERWRITE:\n"
            f"    {_main_db_path(db_url)}  <-  {db_key}\n"
            f"    {_redis_dump_path(redis_url)}  <-  {rdb_key}\n"
            f"\n  Stop the backend + redis first. Continue? [y/N] ",
            file=sys.stderr,
            flush=True,
        )
        if input().strip().lower() not in ("y", "yes"):
            _log("aborted")
            return 1

    with tempfile.TemporaryDirectory() as tmpdir:
        tmp = Path(tmpdir)
        db_local = tmp / "main.db"
        rdb_local = tmp / "dump.rdb"
        s3.download_file(bucket, db_key, str(db_local))
        s3.download_file(bucket, rdb_key, str(rdb_local))

        db_target = _main_db_path(db_url)
        db_target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(db_local, db_target)
        _log("restored db", target=str(db_target))

        rdb_target = _redis_dump_path(redis_url)
        rdb_target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(rdb_local, rdb_target)
        _log("restored redis dump", target=str(rdb_target))

    _log("restore complete — restart redis + backend to pick up the new files")
    return 0


if __name__ == "__main__":
    sys.exit(main())
