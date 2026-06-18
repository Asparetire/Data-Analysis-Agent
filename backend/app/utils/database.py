from __future__ import annotations

from pathlib import Path
from threading import Lock

from sqlalchemy import Engine, create_engine
from sqlalchemy.engine.url import make_url
from sqlalchemy.pool import StaticPool

from ..config import settings

_engines: dict[str, Engine] = {}
_lock = Lock()

DATA_DIR = Path(settings.DATA_DIR)
SQLITE_DIR = DATA_DIR / "sqlite"
SQLITE_DIR.mkdir(parents=True, exist_ok=True)


def _ensure_main_db_dir() -> None:
    """Create the parent directory of the main SQLite DB if missing.

    ``DATABASE_URL`` defaults to ``sqlite:///./data/main.db`` (relative to
    CWD). On a fresh checkout -- CI runners, new dev clones -- that
    directory doesn't exist, so the first ``create_engine`` + connect
    fails with ``unable to open database file``. SQLITE_DIR is already
    mkdir'd above; this extends the same guarantee to the main DB.
    """
    url = make_url(settings.DATABASE_URL)
    if not url.drivername.startswith("sqlite"):
        return
    db_path = url.database
    if not db_path:
        return  # in-memory DB, nothing to create
    p = Path(db_path)
    if not p.is_absolute():
        p = Path.cwd() / p
    p.parent.mkdir(parents=True, exist_ok=True)


_ensure_main_db_dir()


def _sqlite_path(data_source_id: str) -> str:
    safe = "".join(c for c in data_source_id if c.isalnum() or c in ("-", "_"))
    return str(SQLITE_DIR / f"{safe}.db")


def get_engine(data_source_id: str | None = None) -> Engine:
    """根据 data_source_id 返回一个 SQLAlchemy engine。
    - 不传 id：返回主库 engine（用于会话元数据等）。
    - 传了 id：返回指向 data/sqlite/{id}.db 的独立 SQLite 文件。
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


def dispose_engine(data_source_id: str | None) -> None:
    """Dispose and forget the cached engine for a single data source."""
    key = data_source_id or "_default"
    with _lock:
        engine = _engines.pop(key, None)
    if engine is not None:
        engine.dispose()


def dispose_all() -> None:
    with _lock:
        for engine in _engines.values():
            engine.dispose()
        _engines.clear()
