"""Convenience wrappers for writing lineage records from the agent tools.

The agent's tools run synchronously inside an LLM tool call. We don't
need async here (lineage writes are short sidecar file writes), but we
do want a stable shape for the record and a helper that always fills
``ts`` and ``ok``/``error`` so call sites can't forget.
"""

from __future__ import annotations

import re
import time
from typing import Any

from ..utils.logger import get_logger
from . import metadata_service

logger = get_logger(__name__)


_TABLE_REF = re.compile(
    r"\b(?:from|join|update|into)\s+((?:\w+\.)?(?:\"[^\"]+\"|`[^`]+`|\[[^\]]+\]|[A-Za-z_][\w]*))",
    re.IGNORECASE,
)


def extract_table_refs(sql: str) -> list[str]:
    """Pull table names (with optional alias) out of a SQL string.

    Best-effort: skips comments and string literals and returns whatever
    identifiers appear after FROM / JOIN. Used only to tag the lineage
    record, not to validate the query (the safety check lives elsewhere).
    """
    if not sql:
        return []
    # Drop /* ... */ and -- line comments before scanning.
    cleaned = re.sub(r"/\*.*?\*/", " ", sql, flags=re.DOTALL)
    cleaned = re.sub(r"--[^\n]*", " ", cleaned)
    # Strip single-quoted string literals so we don't pick up a column
    # name from inside a string constant.
    cleaned = re.sub(r"'(?:''|[^'])*'", "''", cleaned)
    seen: set[str] = set()
    out: list[str] = []
    for m in _TABLE_REF.finditer(cleaned):
        name = m.group(1).strip().strip('"`[]')
        if not name or name.lower() in {"select", "where"}:
            continue
        if name not in seen:
            seen.add(name)
            out.append(name)
    return out


def record_query(
    *,
    source_ids: list[str],
    sql: str,
    ok: bool,
    row_count: int = 0,
    duration_ms: float = 0.0,
    cache_hit: bool = False,
    error: str | None = None,
    tables: list[str] | None = None,
    user_id: str | None = None,
) -> None:
    """Append a single lineage entry to every bound data source.

    Lineage is per-data-source (not per-session), so we mirror the same
    record across each binding. If a binding is missing the record is
    silently dropped -- lineage is best-effort, it must not block the
    query path.

    Phase 4A/4B: ``user_id`` is recorded so audits can attribute queries
    to users. Pre-Phase-4 records (and tests that don't pass it) leave the
    field absent, which the LineageEntry schema tolerates.
    """
    record: dict[str, Any] = {
        "ts": time.time(),
        "sql": sql,
        "source_ids": list(source_ids),
        "tables": list(tables) if tables is not None else extract_table_refs(sql),
        "row_count": int(row_count),
        "duration_ms": round(float(duration_ms), 2),
        "ok": bool(ok),
        "cache_hit": bool(cache_hit),
    }
    if user_id:
        record["user_id"] = user_id
    if error:
        record["error"] = str(error)[:500]
    for sid in source_ids:
        try:
            metadata_service.append_lineage(sid, record)
        except Exception as e:  # noqa: BLE001
            logger.warning("lineage write failed for %s: %s", sid, e)
