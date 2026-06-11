from __future__ import annotations

import os
from pathlib import Path
from threading import Lock
from typing import Optional

from sqlalchemy import Engine, create_engine
from sqlalchemy.pool import StaticPool

from ..config import settings

_engines: dict[str, Engine] = {}
_lock = Lock()

DATA_DIR = Path(settings.DATA_DIR)
SQLITE_DIR = DATA_DIR / "sqlite"
SQLITE_DIR.mkdir(parents=True, exist_ok=True)


def _sqlite_path(data_source_id: str) -> str:
    safe = "".join(c for c in data_source_id if c.isalnum() or c in ("-", "_"))
    return str(SQLITE_DIR / f"{safe}.db")


def get_engine(data_source_id: Optional[str] = None) -> Engine:
    """根据 data_source_id 返回一个 SQLAlchemy engine。

    - 没传 id：返回主库 engine（用于会话/元数据等）
    - 传了 id：返回指向 data/sqlite/{id}.db 的独立 SQLite 文件
    """
    key = data_source_id or "_default"
    if key in _engines:
        return _engines[key]

    with _lock:
        if key in _engines:
            return _engines[key]

        if data_source_id is None:
            engine = create_engine(
                settings.DATABASE_URL,
                pool_pre_ping=True,
                future=True,
            )
        else:
            path = _sqlite_path(data_source_id)
            engine = create_engine(
                f"sqlite:///{path}",
                connect_args={"check_same_thread": False},
                poolclass=StaticPool,
                future=True,
            )
        _engines[key] = engine
        return engine


def dispose_all() -> None:
    with _lock:
        for engine in _engines.values():
            engine.dispose()
        _engines.clear()
