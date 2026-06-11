from __future__ import annotations

from pathlib import Path

import pandas as pd
from fastapi import UploadFile

from ..config import settings
from ..utils.database import SQLITE_DIR, get_engine
from ..utils.logger import get_logger

logger = get_logger(__name__)

UPLOAD_TABLE = "uploaded_data"
MAX_FILE_BYTES = 50 * 1024 * 1024


def _data_dir() -> Path:
    p = Path(settings.DATA_DIR)
    p.mkdir(parents=True, exist_ok=True)
    return p


def _uploads_dir() -> Path:
    p = _data_dir() / "uploads"
    p.mkdir(parents=True, exist_ok=True)
    return p


def _read_dataframe(path: Path) -> pd.DataFrame:
    suffix = path.suffix.lower()
    if suffix == ".csv":
        return pd.read_csv(path)
    if suffix == ".xlsx":
        return pd.read_excel(path, engine="openpyxl")
    if suffix == ".xls":
        return pd.read_excel(path, engine="xlrd")
    if suffix == ".json":
        return pd.read_json(path)
    raise ValueError(f"Unsupported file type: {suffix}")


async def save_uploaded_file(file: UploadFile, file_id: str) -> str:
    """保存上传的文件，并把内容加载到 data/sqlite/{file_id}.db 的 uploaded_data 表中。"""
    filename = file.filename or ""
    suffix = Path(filename).suffix.lower()
    if suffix not in {".csv", ".xlsx", ".xls", ".json"}:
        raise ValueError(f"Unsupported file type: {suffix}")

    upload_path = _uploads_dir() / f"{file_id}{suffix}"

    size = 0
    with open(upload_path, "wb") as f:
        while chunk := await file.read(1024 * 1024):
            size += len(chunk)
            if size > MAX_FILE_BYTES:
                f.close()
                upload_path.unlink(missing_ok=True)
                raise ValueError(f"File too large: > {MAX_FILE_BYTES // 1024 // 1024}MB")
            f.write(chunk)

    try:
        df = _read_dataframe(upload_path)
    except Exception as e:
        upload_path.unlink(missing_ok=True)
        raise ValueError(f"Failed to parse file: {e}") from e

    if df.empty:
        upload_path.unlink(missing_ok=True)
        raise ValueError("File contains no rows")

    engine = get_engine(file_id)
    df.to_sql(UPLOAD_TABLE, engine, if_exists="replace", index=False)
    logger.info(
        "Loaded %d rows x %d cols from %s into %s",
        len(df),
        len(df.columns),
        filename,
        SQLITE_DIR / f"{file_id}.db",
    )

    return str(SQLITE_DIR / f"{file_id}.db")


def get_sample_rows(data_source_id: str, limit: int = 5):
    from sqlalchemy import text

    engine = get_engine(data_source_id)
    try:
        with engine.connect() as conn:
            result = conn.execute(
                text(f'SELECT * FROM "{UPLOAD_TABLE}" LIMIT :n'),
                {"n": limit},
            )
            cols = result.keys()
            return [dict(zip(cols, row)) for row in result.fetchall()]
    except Exception:
        return None
