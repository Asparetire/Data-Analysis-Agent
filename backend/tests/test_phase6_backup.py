"""Phase 6: backup.py unit tests.

We can't easily exercise the full main() (it needs MinIO + Redis + a real
SQLite), but we can cover the pure helpers: _dump_sqlite (VACUUM INTO),
_prune_old (S3 mock), and the missing-config branch of main().
"""

from __future__ import annotations

import time
from pathlib import Path

import pytest


@pytest.fixture
def backup_module(monkeypatch, tmp_data_dir):
    """Import scripts/backup.py with the test settings applied.

    The module lives outside the ``app`` package, so we add scripts/ to
    sys.path and import it under a known name. Settings are patched onto
    ``app.config.settings`` so the module's ``from app.config import settings``
    picks them up.
    """
    scripts_dir = Path(__file__).resolve().parent.parent / "scripts"
    monkeypatch.syspath_prepend(str(scripts_dir))
    import importlib

    mod = importlib.import_module("backup")
    return mod


def test_main_returns_2_when_config_missing(backup_module, monkeypatch):
    # Force every MinIO setting to empty — main should exit 2 without
    # touching the network.
    monkeypatch.setattr(backup_module.settings, "MINIO_ENDPOINT", "")
    monkeypatch.setattr(backup_module.settings, "MINIO_ACCESS_KEY", "")
    monkeypatch.setattr(backup_module.settings, "MINIO_SECRET_KEY", "")
    monkeypatch.setattr(backup_module.settings, "MINIO_BUCKET", "")
    assert backup_module.main() == 2


def test_dump_sqlite_writes_snapshot(backup_module, tmp_data_dir, monkeypatch):
    """VACUUM INTO produces a readable snapshot file."""
    from app.config import settings
    from app.utils import database
    from sqlalchemy import create_engine, text

    # Point main DB at a fresh path inside the tmp_data_dir and seed it.
    main_path = tmp_data_dir / "main.db"
    monkeypatch.setattr(settings, "DATABASE_URL", f"sqlite:///{main_path}")
    database.dispose_engine(None)
    eng = create_engine(f"sqlite:///{main_path}", future=True)
    with eng.begin() as conn:
        conn.execute(text("CREATE TABLE t (x INTEGER)"))
        conn.execute(text("INSERT INTO t VALUES (1), (2), (3)"))
    eng.dispose()

    dest = tmp_data_dir / "snap.db"
    backup_module._dump_sqlite(f"sqlite:///{main_path}", dest)
    assert dest.exists() and dest.stat().st_size > 0

    # Verify the snapshot contains the rows we wrote.
    snap_eng = create_engine(f"sqlite:///{dest}", future=True)
    with snap_eng.connect() as conn:
        rows = conn.execute(text("SELECT x FROM t ORDER BY x")).fetchall()
    snap_eng.dispose()
    assert [r[0] for r in rows] == [1, 2, 3]


def test_prune_old_deletes_objects_past_retention(backup_module):
    """_prune_old uses LastModified to decide what to delete."""
    deleted_keys: list[str] = []

    class _FakeS3:
        def get_paginator(self, _name):
            class _P:
                def paginate(self, **_kw):
                    now = time.time()
                    yield {
                        "Contents": [
                            {"Key": "backups/old.db", "LastModified": _ts(now - 30 * 86400)},
                            {"Key": "backups/new.db", "LastModified": _ts(now - 1 * 86400)},
                        ]
                    }

            return _P()

        def delete_object(self, *, Bucket, Key):  # noqa: N803
            deleted_keys.append(Key)

    def _ts(epoch: float):
        from datetime import UTC, datetime

        return datetime.fromtimestamp(epoch, tz=UTC)

    backup_module._prune_old(_FakeS3(), "bucket", "backups/", retention_days=7)
    assert deleted_keys == ["backups/old.db"]
