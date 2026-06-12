"""Shared pytest fixtures.

Goals:
- Tests never touch the real Redis (fakeredis substitute).
- Tests never touch the real upload / sqlite directories (tmp_path override).
- Each test gets a fresh fakeredis instance so state never leaks across tests.
"""

from __future__ import annotations

import io
from collections.abc import AsyncIterator
from typing import Any

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
def make_upload():
    """Return a factory that builds a _FakeUploadFile from a filename + bytes."""
    return _FakeUploadFile
