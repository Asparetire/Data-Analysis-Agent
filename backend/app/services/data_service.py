from __future__ import annotations

import re
from collections.abc import Mapping
from pathlib import Path
from typing import Any

import pandas as pd
from fastapi import UploadFile
from sqlalchemy import text

from ..config import settings
from ..services import metadata_service, query_cache
from ..utils.database import SQLITE_DIR, dispose_engine, get_engine
from ..utils.logger import get_logger
from ..utils.pii_mask import mask_dataframe, mask_value

logger = get_logger(__name__)

# Default table name for single-sheet uploads (CSV, JSON, single-sheet Excel).
# Multi-sheet Excel uses sanitized sheet names -- this constant is only the
# single-table fallback.
DEFAULT_TABLE = "uploaded_data"
MAX_FILE_BYTES = 50 * 1024 * 1024

# Indexing heuristics -- see _auto_create_indexes.
INDEX_UNIQUE_RATIO = 0.95  # unique-value ratio above which a column gets a unique index
INDEX_MAX_INDEXES = 5  # cap auto-indexes per table to avoid bloat

# Type inference -- see infer_column_type.
CURRENCY_PATTERN = re.compile(r"^\s*[\-\+]?[\(]?[$¥€£￥]?\s*[\d,]+(\.\d+)?[\)]?\s*[$¥€£]?\s*$")
ISO_DATE_PATTERN = re.compile(r"^\d{4}[-/]\d{1,2}[-/]\d{1,2}")


def _data_dir() -> Path:
    p = Path(settings.DATA_DIR)
    p.mkdir(parents=True, exist_ok=True)
    return p


def _uploads_dir() -> Path:
    p = _data_dir() / "uploads"
    p.mkdir(parents=True, exist_ok=True)
    return p


def _read_sheets(path: Path) -> dict[str, pd.DataFrame]:
    """Parse a file into a {table_name: DataFrame} dict.

    - CSV/JSON: a single table ``DEFAULT_TABLE``.
    - XLSX/XLS single-sheet: a single table ``DEFAULT_TABLE`` (preserves the
      pre-3A single-table contract).
    - XLSX/XLS multi-sheet: one table per sheet, named by the (sanitized)
      sheet name.
    """
    suffix = path.suffix.lower()
    if suffix == ".csv":
        return {DEFAULT_TABLE: pd.read_csv(path)}
    if suffix == ".xlsx":
        sheets = pd.read_excel(path, engine="openpyxl", sheet_name=None)
    elif suffix == ".xls":
        sheets = pd.read_excel(path, engine="xlrd", sheet_name=None)
    elif suffix == ".json":
        return {DEFAULT_TABLE: pd.read_json(path)}
    else:
        raise ValueError(f"Unsupported file type: {suffix}")
    if len(sheets) == 1:
        # Backward-compat: pre-3A code (and the LLM's prompts) assume the
        # table is called ``uploaded_data`` for single-sheet files.
        only_df = next(iter(sheets.values()))
        return {DEFAULT_TABLE: only_df}
    return _resolve_sheet_names(sheets)


_SHEET_SANITIZE = re.compile(r"[^\w]", re.UNICODE)


def _sanitize_sheet_name(name: str) -> str:
    """SQLite-friendly sheet name: word chars (incl. Unicode letters) + underscore.

    Stripped of whitespace, dashes, parens, etc. Non-letter-leading names
    get a ``sheet_`` prefix so the resulting identifier is still legal.
    """
    cleaned = _SHEET_SANITIZE.sub("_", name.strip())
    cleaned = re.sub(r"_+", "_", cleaned).strip("_") or "sheet"
    if not (cleaned[0].isalpha() or cleaned[0] == "_"):
        cleaned = "sheet_" + cleaned
    return cleaned[:60]


def _resolve_sheet_names(sheets: Mapping[str, pd.DataFrame]) -> dict[str, pd.DataFrame]:
    """Rename sheets to valid SQL identifiers, deduplicating on collision."""
    out: dict[str, pd.DataFrame] = {}
    seen: dict[str, int] = {}
    for raw_name, df in sheets.items():
        name = _sanitize_sheet_name(str(raw_name))
        if name in seen:
            seen[name] += 1
            name = f"{name}_{seen[name]}"
        else:
            seen[name] = 0
        out[name] = df
    return out


def _normalize_columns(df: pd.DataFrame) -> pd.DataFrame:
    """Sanitize column names: strip, dedupe blanks, disambiguate collisions."""
    seen: dict[str, int] = {}
    new_cols = []
    for col in df.columns:
        name = str(col).strip() or "column"
        if name in seen:
            seen[name] += 1
            new_cols.append(f"{name}_{seen[name]}")
        else:
            seen[name] = 0
            new_cols.append(name)
    df = df.copy()
    df.columns = new_cols
    return df


def infer_column_type(series: pd.Series, sample_size: int = 200) -> str:
    """Classify a pandas Series into a coarse logical type.

    Returns one of: ``datetime``, ``currency``, ``boolean``, ``integer``,
    ``number``, ``string``. ``pd.api.types.infer_dtype`` is used to
    distinguish datetime/number, then a regex sweep over a sample catches
    currency-shaped strings (e.g. ``"$1,234.50"``, ``"¥99"``).
    """
    non_null = series.dropna()
    if non_null.empty:
        return "string"
    sample = non_null.head(sample_size)
    kind = pd.api.types.infer_dtype(sample, skipna=True)
    if kind in ("datetime64", "datetime"):
        return "datetime"
    if kind in ("integer",):
        return "integer"
    if kind in ("floating",):
        return "number"
    if kind in ("boolean",):
        return "boolean"
    if kind in ("string", "unicode", "mixed"):
        str_values = sample.astype(str).str.strip()
        if str_values.str.match(CURRENCY_PATTERN).all():
            return "currency"
    return "string"


def _sample_values(series: pd.Series, n: int = 3) -> list:
    """Pick a few diverse non-null values for prompt injection."""
    non_null = series.dropna()
    if non_null.empty:
        return []
    sample = non_null.head(n).tolist()
    return [v.isoformat() if hasattr(v, "isoformat") else v for v in sample]


def _auto_create_indexes(engine, table: str, df: pd.DataFrame) -> list[str]:
    """Create indexes for high-cardinality and date-like columns.

    Heuristic: a column gets an index if
      - pandas classifies it as datetime, OR
      - our ``infer_column_type`` says currency, OR
      - its unique-value ratio is >= ``INDEX_UNIQUE_RATIO`` (typically IDs).

    We cap at ``INDEX_MAX_INDEXES`` per table to avoid bloating the file.
    """
    indexed: list[str] = []
    n = len(df)
    if n == 0:
        return indexed
    with engine.begin() as conn:
        for col in df.columns:
            if len(indexed) >= INDEX_MAX_INDEXES:
                break
            series = df[col]
            col_type = infer_column_type(series)
            unique_ratio = series.nunique(dropna=True) / n
            should_index = col_type in {"datetime", "currency"} or (
                col_type in {"integer", "string"} and unique_ratio >= INDEX_UNIQUE_RATIO
            )
            if not should_index:
                continue
            try:
                conn.execute(
                    text(f'CREATE INDEX IF NOT EXISTS "idx_{table}_{col}" ON "{table}" ("{col}")')
                )
                indexed.append(col)
            except Exception as e:  # noqa: BLE001
                logger.warning("failed to create index on %s.%s: %s", table, col, e)
    return indexed


async def save_uploaded_file(file: UploadFile, file_id: str) -> str:
    """Save the upload, parse it (multi-sheet aware), and load into SQLite.

    Returns the absolute path to the data source's SQLite file.
    """
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
        sheets = _read_sheets(upload_path)
    except Exception as e:
        upload_path.unlink(missing_ok=True)
        raise ValueError(f"Failed to parse file: {e}") from e

    clean_sheets: dict[str, pd.DataFrame] = {}
    for table_name, df in sheets.items():
        if df is None or df.empty:
            continue
        normalized = _normalize_columns(df)
        # Phase 4C layer 1: scrub PII before it hits SQLite. The raw value
        # never lands on disk; downstream queries see the mask. This is a
        # one-way transform — the original cannot be recovered.
        clean_sheets[table_name] = mask_dataframe(normalized)

    if not clean_sheets:
        upload_path.unlink(missing_ok=True)
        raise ValueError("File contains no rows")

    engine = get_engine(file_id)
    # Phase 6: full cleanup on any failure during to_sql / index creation.
    # Previously a mid-load crash left a half-written SQLite file, the
    # cached engine, and the upload_path on disk — leaking space and
    # confusing later list_datasources calls. We dispose the engine and
    # delete both files so the file_id is truly rolled back.
    loaded_tables: list[str] = []
    try:
        indexed_summary: dict[str, list[str]] = {}
        for table_name, df in clean_sheets.items():
            df.to_sql(table_name, engine, if_exists="replace", index=False)
            loaded_tables.append(table_name)
            indexed_summary[table_name] = _auto_create_indexes(engine, table_name, df)
    except Exception as e:
        # Best-effort cleanup; surface the original error.
        for _t in loaded_tables:
            try:
                with engine.connect() as conn:
                    conn.execute(text(f'DROP TABLE IF EXISTS "{_t}"'))
            except Exception:
                pass
        dispose_engine(file_id)
        sqlite_path = SQLITE_DIR / f"{file_id}.db"
        sqlite_path.unlink(missing_ok=True)
        upload_path.unlink(missing_ok=True)
        raise ValueError(f"Failed to load data into SQLite: {e}") from e
    logger.info(
        "Loaded %s (%d tables: %s) into %s",
        filename,
        len(clean_sheets),
        ", ".join(clean_sheets.keys()),
        SQLITE_DIR / f"{file_id}.db",
    )

    # Persist per-table column metadata + indexes + row_count to the sidecar
    # so the agent's later ``list_tables`` / ``get_table_schema`` calls can
    # show descriptions and units in the prompt and skip COUNT(*) on every
    # schema/list call. Samples are masked too so the LLM prompt (layer 4)
    # never carries raw PII even if the column wasn't caught by the upload scrub.
    try:
        metadata_service.set_source_type(file_id, "sqlite")
        for table_name, df in clean_sheets.items():
            columns_meta = {
                col: {
                    "type": infer_column_type(df[col]),
                    "description": "",
                    "unit": None,
                    "sample": [mask_value(v) for v in _sample_values(df[col])],
                }
                for col in df.columns
            }
            metadata_service.set_table_metadata(
                file_id,
                table_name,
                columns=columns_meta,
                indexes=indexed_summary.get(table_name, []),
                replace_columns=True,
                row_count=len(df),
            )
    except Exception as e:  # noqa: BLE001
        # Metadata is best-effort: a failure here must not block the upload.
        logger.warning("failed to persist column metadata for %s: %s", file_id, e)

    return str(SQLITE_DIR / f"{file_id}.db")


def get_primary_table(data_source_id: str) -> str:
    """Return the first table name for a data source.

    For multi-sheet uploads this is the first sheet (preserves the old
    single-table mental model). For single-sheet files it is
    ``DEFAULT_TABLE``. Returns ``DEFAULT_TABLE`` when the db is empty.
    """
    tables = list_tables(data_source_id)
    if not tables:
        return DEFAULT_TABLE
    return tables[0]


def list_tables(data_source_id: str) -> list[str]:
    """List all user-data table names in a data source's SQLite file.

    Excludes ``sqlite_*`` system tables. Returns an empty list if the file
    is missing or unreadable.
    """
    engine = get_engine(data_source_id)
    try:
        with engine.connect() as conn:
            rows = conn.execute(
                text("SELECT name FROM sqlite_master WHERE type='table'")
            ).fetchall()
    except Exception:
        return []
    names = [r[0] for r in rows if not str(r[0]).startswith("sqlite_")]
    return names


def get_sample_rows(data_source_id: str, limit: int = 5, table: str | None = None):
    """Fetch a small sample from a table; default to the primary table."""
    target = table or get_primary_table(data_source_id)
    engine = get_engine(data_source_id)
    try:
        with engine.connect() as conn:
            result = conn.execute(
                text(f'SELECT * FROM "{target}" LIMIT :n'),
                {"n": limit},
            )
            cols = result.keys()
            return [dict(zip(cols, row, strict=False)) for row in result.fetchall()]
    except Exception:
        return None


def get_table_info(data_source_id: str, table: str | None = None) -> dict[str, Any] | None:
    """Return ``{table, row_count, columns: [{name, type, ...}]}`` for a table.

    ``type`` is taken from the sidecar when available (more precise --
    distinguishes ``currency`` / ``datetime``) and falls back to
    ``PRAGMA table_info`` otherwise.
    """
    target = table or get_primary_table(data_source_id)
    engine = get_engine(data_source_id)
    try:
        with engine.connect() as conn:
            rows = conn.execute(text(f'PRAGMA table_info("{target}")')).fetchall()
    except Exception:
        return None
    # PRAGMA table_info returns zero rows for a missing table — without this
    # check, the COUNT(*) fallback below would set count=0 and we'd return a
    # dict with empty columns instead of None, breaking the "table not found"
    # contract that get_table_schema relies on to emit its error payload.
    if not rows:
        return None
    side_meta = metadata_service.get_table_metadata(data_source_id, target) or {}
    side_cols = side_meta.get("columns") or {}
    # Phase 6: prefer the row_count cached at upload time (in the sidecar)
    # so list_tables / get_table_info don't fire COUNT(*) on every call —
    # matters on 100k+ row tables where COUNT(*) is a full scan. The
    # sidecar value is set once at upload; if the table was modified
    # out-of-band (not via this app), the count will be stale, but that's
    # an acceptable trade-off since the only write path is re-upload.
    count = side_meta.get("row_count")
    if count is None:
        try:
            with engine.connect() as conn:
                count = conn.execute(text(f'SELECT COUNT(*) FROM "{target}"')).scalar()
        except Exception:
            count = 0
    columns = []
    for r in rows:
        name = r[1]
        meta = side_cols.get(name) or {}
        columns.append(
            {
                "name": name,
                "type": meta.get("type") or r[2] or "string",
                "nullable": not r[3],
                "description": meta.get("description") or "",
                "unit": meta.get("unit"),
                "sample": meta.get("sample") or [],
            }
        )
    return {"table": target, "row_count": count, "columns": columns}


def fetch_rows(
    data_source_id: str,
    *,
    table: str,
    offset: int = 0,
    limit: int = 20,
    sort: str | None = None,
    direction: str = "asc",
) -> dict | None:
    """Phase 4D: server-side paginated read from one table.

    Returns ``{table, rows, columns, total, offset, limit}`` or None when
    the table doesn't exist. ``sort`` must be a real column of the table;
    ``direction`` must be ``asc`` or ``desc``. Both are validated against
    the table schema before being interpolated into SQL — the table name
    and column names are quoted with double quotes, but we still refuse
    anything that doesn't appear in PRAGMA table_info so a malicious
    payload like ``"col"; DROP TABLE x; --`` can't slip through.
    """
    if direction.lower() not in ("asc", "desc"):
        direction = "asc"
    offset = max(0, int(offset))
    limit = max(1, min(int(limit), 200))
    engine = get_engine(data_source_id)
    try:
        with engine.connect() as conn:
            # Validate table + columns against PRAGMA before any interpolation.
            info = conn.execute(text(f'PRAGMA table_info("{table}")')).fetchall()
            if not info:
                return None
            valid_cols = {r[1] for r in info}
            count = conn.execute(text(f'SELECT COUNT(*) FROM "{table}"')).scalar()
            # Build ORDER BY. Stable secondary sort on rowid so pages don't
            # shuffle rows with equal sort keys.
            if sort and sort in valid_cols:
                order_clause = f' ORDER BY "{sort}" {direction.upper()}, rowid'
            else:
                order_clause = " ORDER BY rowid"
            result = conn.execute(
                text(f'SELECT * FROM "{table}"{order_clause} LIMIT :lim OFFSET :off'),
                {"lim": limit, "off": offset},
            )
            cols = list(result.keys())
            rows = [{c: row[i] for i, c in enumerate(cols)} for row in result.fetchall()]
    except Exception:
        return None
    return {
        "table": table,
        "rows": rows,
        "columns": cols,
        "total": int(count or 0),
        "offset": offset,
        "limit": limit,
    }


def delete_data_source(data_source_id: str) -> bool:
    """Remove the uploaded file, the SQLite database, and drop the cached engine.

    Also invalidates any in-process query cache entries that referenced this
    data source in their binding set, so a re-upload with the same id never
    serves stale rows.
    """
    try:
        query_cache.get_cache().invalidate_containing(data_source_id)
    except Exception:  # noqa: BLE001
        # Cache eviction is best-effort; never block deletion on it.
        logger.warning("cache invalidation failed for %s", data_source_id, exc_info=True)
    dispose_engine(data_source_id)

    deleted = False
    uploads_dir = _uploads_dir()
    for path in uploads_dir.iterdir():
        if path.stem == data_source_id and path.suffix.lower() in {
            ".csv",
            ".xlsx",
            ".xls",
            ".json",
        }:
            path.unlink(missing_ok=True)
            deleted = True
            break

    sqlite_path = SQLITE_DIR / f"{data_source_id}.db"
    if sqlite_path.exists():
        sqlite_path.unlink()
        deleted = True

    return deleted
