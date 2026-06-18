"""Lightweight sidecar store for per-data-source metadata.

We keep human-editable bits in a JSON file under DATA_DIR, keyed by the data
source id. The file is read on every call, so a rename is immediately visible
to the next /datasources request.

Why a sidecar instead of renaming the file:
- The original filename is what we use as the upload id stem and what links
  to the per-source SQLite file in data/sqlite/{id}.db. Renaming the file
  would require a cascade update across multiple paths.
- A separate name avoids breaking the on-disk layout when names contain
  spaces, non-ASCII, or weird characters.
- Users don't expect "my renamed file" to also rename the SQLite db.

Entry shape (Phase 3+):
    {
        "display_name": str,
        "source_type": "sqlite" | "postgres" | "mysql" | "unknown",
        "tables": {
            "<table>": {
                "columns": {
                    "<col>": {"type": str, "description": str, "unit": str | null, "sample": list}
                },
                "indexes": [str, ...]
            }
        },
        "lineage": [{sql, source_id, tables, row_count, duration_ms, ts, ok}, ...]
    }

Legacy entries (Phase 2) only carried ``display_name``; on read we lazily
fill in empty ``source_type``/``tables``/``lineage`` so callers don't have
to special-case.
"""

from __future__ import annotations

import json
import threading
from pathlib import Path
from typing import Any

from ..config import settings
from ..utils.logger import get_logger

logger = get_logger(__name__)

DISPLAY_NAME_FIELD = "display_name"
SOURCE_TYPE_FIELD = "source_type"
TABLES_FIELD = "tables"
LINEAGE_FIELD = "lineage"
OWNER_ID_FIELD = "owner_id"  # Phase 4A: ACL on data sources

MAX_LINEAGE_ENTRIES = 200
LINEAGE_TRIM_TO = 100  # When we hit the cap, drop to this size to avoid thrashing.

_lock = threading.Lock()


def _meta_path() -> Path:
    p = Path(settings.DATA_DIR)
    p.mkdir(parents=True, exist_ok=True)
    return p / "datasources.json"


def _normalize_entry(entry: dict[str, Any] | None) -> dict[str, Any]:
    """Bring a loaded entry up to the current schema, filling defaults."""
    if not isinstance(entry, dict):
        entry = {}
    entry.setdefault(DISPLAY_NAME_FIELD, None)
    entry.setdefault(SOURCE_TYPE_FIELD, "unknown")
    entry.setdefault(TABLES_FIELD, {})
    entry.setdefault(LINEAGE_FIELD, [])
    entry.setdefault(OWNER_ID_FIELD, None)
    return entry


def _load() -> dict[str, dict[str, Any]]:
    path = _meta_path()
    if not path.exists():
        return {}
    try:
        with path.open("r", encoding="utf-8") as f:
            data = json.load(f)
        if not isinstance(data, dict):
            logger.warning("datasources.json is not a dict; ignoring")
            return {}
        for ds_id in list(data.keys()):
            data[ds_id] = _normalize_entry(data.get(ds_id))
        return data
    except (json.JSONDecodeError, OSError) as e:
        logger.warning("failed to read %s: %s", path, e)
        return {}


def _save(data: dict[str, dict[str, Any]]) -> None:
    path = _meta_path()
    tmp = path.with_suffix(".json.tmp")
    with tmp.open("w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    tmp.replace(path)


def _get_entry(data: dict[str, dict[str, Any]], data_source_id: str) -> dict[str, Any]:
    entry = data.get(data_source_id)
    if not isinstance(entry, dict):
        entry = {}
    return _normalize_entry(entry)


# ---------------------------------------------------------------------------
# Display name (Phase 2 API, preserved for backward compat)
# ---------------------------------------------------------------------------


def get_display_name(data_source_id: str) -> str | None:
    """Return the custom display name, or None if no override is set."""
    with _lock:
        data = _load()
    entry = data.get(data_source_id)
    if not isinstance(entry, dict):
        return None
    name = entry.get(DISPLAY_NAME_FIELD)
    if not isinstance(name, str) or not name.strip():
        return None
    return name.strip()


def set_display_name(data_source_id: str, name: str) -> str:
    """Set the display name. Returns the normalized value (trimmed, non-empty)."""
    normalized = name.strip()
    if not normalized:
        raise ValueError("display_name must be a non-empty string")
    with _lock:
        data = _load()
        entry = _get_entry(data, data_source_id)
        entry[DISPLAY_NAME_FIELD] = normalized
        data[data_source_id] = entry
        _save(data)
    return normalized


# ---------------------------------------------------------------------------
# Table / column metadata (Phase 3A)
# ---------------------------------------------------------------------------


def set_table_metadata(
    data_source_id: str,
    table: str,
    *,
    columns: dict[str, dict[str, Any]] | None = None,
    indexes: list[str] | None = None,
    replace_columns: bool = False,
) -> None:
    """Upsert metadata for one table.

    Args:
        columns: {col: {type, description, unit, sample}} -- merged in.
            If ``replace_columns`` is True, the previous columns dict is
            dropped; otherwise we merge per-column (later writes win).
        indexes: list of column names that have an index. Replaces previous list.
    """
    if not table or not isinstance(table, str):
        raise ValueError("table must be a non-empty string")
    with _lock:
        data = _load()
        entry = _get_entry(data, data_source_id)
        tables = entry.setdefault(TABLES_FIELD, {})
        existing = tables.get(table) or {"columns": {}, "indexes": []}
        if not isinstance(existing, dict):
            existing = {"columns": {}, "indexes": []}
        if columns is not None:
            cols = existing.get("columns") or {}
            if not isinstance(cols, dict):
                cols = {}
            if replace_columns:
                cols = {}
            for col, meta in columns.items():
                if not isinstance(meta, dict):
                    continue
                prev = cols.get(col) or {}
                prev.update(meta)
                cols[col] = prev
            existing["columns"] = cols
        if indexes is not None:
            existing["indexes"] = list(indexes)
        tables[table] = existing
        data[data_source_id] = entry
        _save(data)


def get_table_metadata(data_source_id: str, table: str) -> dict[str, Any] | None:
    """Return {columns: {...}, indexes: [...]} for a table, or None if unknown."""
    with _lock:
        data = _load()
    entry = data.get(data_source_id)
    if not isinstance(entry, dict):
        return None
    tables = entry.get(TABLES_FIELD) or {}
    if not isinstance(tables, dict):
        return None
    meta = tables.get(table)
    return meta if isinstance(meta, dict) else None


def get_all_tables(data_source_id: str) -> dict[str, dict[str, Any]]:
    """Return {table: {columns, indexes}} -- empty dict if no metadata yet."""
    with _lock:
        data = _load()
    entry = data.get(data_source_id)
    if not isinstance(entry, dict):
        return {}
    tables = entry.get(TABLES_FIELD) or {}
    if not isinstance(tables, dict):
        return {}
    return {t: meta for t, meta in tables.items() if isinstance(meta, dict)}


def set_source_type(data_source_id: str, source_type: str) -> None:
    """Stamp the storage backend kind (sqlite / postgres / mysql)."""
    with _lock:
        data = _load()
        entry = _get_entry(data, data_source_id)
        entry[SOURCE_TYPE_FIELD] = source_type
        data[data_source_id] = entry
        _save(data)


def get_source_type(data_source_id: str) -> str:
    with _lock:
        data = _load()
    entry = data.get(data_source_id)
    if not isinstance(entry, dict):
        return "unknown"
    return str(entry.get(SOURCE_TYPE_FIELD) or "unknown")


# ---------------------------------------------------------------------------
# Lineage (Phase 3E -- defined here so the schema is in one place)
# ---------------------------------------------------------------------------


def append_lineage(data_source_id: str, record: dict[str, Any]) -> None:
    """Append a lineage record; trim oldest when over the cap.

    Records are stored newest-last; when the array exceeds
    ``MAX_LINEAGE_ENTRIES`` it is trimmed down to ``LINEAGE_TRIM_TO`` from
    the front so we don't re-trim on every write.
    """
    with _lock:
        data = _load()
        entry = _get_entry(data, data_source_id)
        lineage = entry.setdefault(LINEAGE_FIELD, [])
        if not isinstance(lineage, list):
            lineage = []
        lineage.append(record)
        if len(lineage) > MAX_LINEAGE_ENTRIES:
            del lineage[:-LINEAGE_TRIM_TO]
        entry[LINEAGE_FIELD] = lineage
        data[data_source_id] = entry
        _save(data)


def get_lineage(data_source_id: str, limit: int | None = None) -> list[dict[str, Any]]:
    """Return lineage newest-first; ``limit`` caps the result length."""
    with _lock:
        data = _load()
    entry = data.get(data_source_id)
    if not isinstance(entry, dict):
        return []
    lineage = entry.get(LINEAGE_FIELD) or []
    if not isinstance(lineage, list):
        return []
    out = [r for r in lineage if isinstance(r, dict)]
    out.reverse()
    if limit is not None and limit >= 0:
        return out[:limit]
    return out


# ---------------------------------------------------------------------------
# Lifecycle
# ---------------------------------------------------------------------------


def delete_entry(data_source_id: str) -> None:
    """Forget any metadata for a data source. Idempotent."""
    with _lock:
        data = _load()
        if data_source_id in data:
            del data[data_source_id]
            _save(data)


# ---------------------------------------------------------------------------
# Owner (Phase 4A ACL)
# ---------------------------------------------------------------------------


def set_owner(data_source_id: str, owner_id: str) -> None:
    """Stamp the owning user id on a data source entry."""
    with _lock:
        data = _load()
        entry = _get_entry(data, data_source_id)
        entry[OWNER_ID_FIELD] = owner_id
        data[data_source_id] = entry
        _save(data)


def get_owner(data_source_id: str) -> str | None:
    """Return the owner id, or None when the entry is missing or unowned."""
    with _lock:
        data = _load()
    entry = data.get(data_source_id)
    if not isinstance(entry, dict):
        return None
    owner = entry.get(OWNER_ID_FIELD)
    return owner if isinstance(owner, str) and owner else None


def list_ids_for_owner(owner_id: str) -> list[str]:
    """Return all data source ids owned by ``owner_id``."""
    with _lock:
        data = _load()
    out: list[str] = []
    for ds_id, entry in data.items():
        if not isinstance(entry, dict):
            continue
        if entry.get(OWNER_ID_FIELD) == owner_id:
            out.append(ds_id)
    return out


def assign_owner_to_ownerless(owner_id: str) -> int:
    """Stamp ``owner_id`` on every sidecar entry that currently has no owner.

    Used once at startup to migrate pre-Phase-4 data. Returns the count of
    re-stamped entries.
    """
    with _lock:
        data = _load()
        stamped = 0
        for entry in data.values():
            if not isinstance(entry, dict):
                continue
            existing = entry.get(OWNER_ID_FIELD)
            if not (isinstance(existing, str) and existing):
                entry[OWNER_ID_FIELD] = owner_id
                stamped += 1
        if stamped:
            _save(data)
    return stamped
