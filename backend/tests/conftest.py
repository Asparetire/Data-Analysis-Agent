"""Shared pytest fixtures.

Goals:
- Tests never touch the real Redis (fakeredis substitute).
- Tests never touch the real upload / sqlite directories (tmp_path override).
- Each test gets a fresh fakeredis instance so state never leaks across tests.
- JWT_SECRET is set to a test-only value before app.config is imported, so
  the startup validator doesn't abort collection.
"""

from __future__ import annotations

import io
import os
from collections.abc import AsyncIterator
from typing import Any

# Must run before `app.config` is imported anywhere. pytest loads conftest
# first, so setting it at module top does the job.
os.environ.setdefault("JWT_SECRET", "test-only-jwt-secret-32-bytes-long-aaaa")
# Skip both startup validators (JWT_SECRET + MIGRATION_ADMIN_PASSWORD) in tests.
os.environ.setdefault("JWT_SECRET_DEV_OK", "1")
# Phase 6: the default MIGRATION_ADMIN_PASSWORD ("change-me-now") has no
# digits and would be rejected by the new complexity policy when the
# startup migration tries to register the admin. Override to a value that
# passes the policy so TestClient startup doesn't blow up.
os.environ.setdefault("MIGRATION_ADMIN_PASSWORD", "migration-admin-pwd-001")

import fakeredis.aioredis
import pytest
import pytest_asyncio
from app.services import session_service


class _FakeUploadFile:
    """Minimal UploadFile stand-in for save_uploaded_file tests.

    The real FastAPI UploadFile exposes .filename and an async .read(chunk)
    iterator; that's all data_service.save_uploaded_file uses.
    """

    def __init__(self, filename: str, content: bytes):
        self.filename = filename
        self._buf = io.BytesIO(content)

    async def read(self, size: int = -1) -> bytes:
        return self._buf.read(size)


@pytest_asyncio.fixture
async def fake_redis(
    monkeypatch,
) -> AsyncIterator[fakeredis.aioredis.FakeRedis]:
    """Replace the module-level Redis with a fakeredis instance."""
    server = fakeredis.aioredis.FakeServer()
    fake = fakeredis.aioredis.FakeRedis(server=server, decode_responses=True)
    monkeypatch.setattr(session_service, "_redis", fake)

    def _patched() -> Any:
        return fake

    monkeypatch.setattr(session_service, "_get_redis", _patched)
    yield fake
    await fake.aclose()


@pytest.fixture
def tmp_data_dir(monkeypatch, tmp_path):
    """Redirect DATA_DIR to a temp directory; clean up engines afterward."""
    from pathlib import Path

    from app.config import settings
    from app.services import data_service
    from app.utils import database

    sqlite_subdir = tmp_path / "sqlite"
    sqlite_subdir.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(settings, "DATA_DIR", str(tmp_path))
    # SQLITE_DIR is captured at import time, so patch the symbol in every
    # module that imported it.
    monkeypatch.setattr(database, "SQLITE_DIR", sqlite_subdir)
    monkeypatch.setattr(database, "DATA_DIR", Path(tmp_path))
    monkeypatch.setattr(data_service, "SQLITE_DIR", sqlite_subdir)
    yield tmp_path
    for engine in list(database._engines.values()):  # noqa: SLF001
        engine.dispose()
    database._engines.clear()  # noqa: SLF001


@pytest.fixture
def users_db(tmp_data_dir, monkeypatch):
    """Point main.db at a temp file and (re)create the users table.

    The default ``DATABASE_URL`` lands in ``backend/data/main.db`` which is
    shared across tests; without this fixture, registered users leak between
    tests and the second ``register(alice@example.com, ...)`` 409s.
    """
    from app.config import settings
    from app.services import auth_service
    from app.utils import database

    main_path = tmp_data_dir / "main.db"
    monkeypatch.setattr(settings, "DATABASE_URL", f"sqlite:///{main_path}")
    database.dispose_engine(None)
    auth_service.init_users_table()
    yield
    database.dispose_engine(None)


@pytest.fixture
def make_upload():
    """Return a factory that builds a _FakeUploadFile from a filename + bytes."""
    return _FakeUploadFile
