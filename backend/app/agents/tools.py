from __future__ import annotations

import json
import re
import time

from langchain_core.tools import tool
from sqlalchemy.exc import SQLAlchemyError

from ..services import data_service, lineage, query_cache
from ..utils.database import _sqlite_path, get_engine
from ..utils.logger import get_logger

logger = get_logger(__name__)

_MAX_ROWS = 100
_LARGE_TABLE_ROW_THRESHOLD = 50_000  # above this, default samples shrink
_LARGE_TABLE_SAMPLE_SIZE = 5

_FORBIDDEN_KEYWORDS = re.compile(
    r"\b(insert|update|delete|drop|alter|create|replace|attach|detach|"
    r"pragma|vacuum|reindex|begin|commit|rollback|grant|revoke)\b",
    re.IGNORECASE,
)
_FORBIDDEN_CHARS = re.compile(r"[;`]")


def _is_safe_select(sql: str):
    s = sql.strip().rstrip(";")
    if _FORBIDDEN_KEYWORDS.search(s):
        return "Query contains forbidden keywords."
    if _FORBIDDEN_CHARS.search(s):
        return "Query contains forbidden characters (; ` )."
    if not s.lower().startswith("select") and not s.lower().startswith("with"):
        return "Only SELECT/WITH queries are allowed."
    return None


def _wrap_with_limit(sql: str, limit: int) -> str:
    s = sql.strip().rstrip(";")
    if re.search(r"\blimit\s+\d+", s, re.IGNORECASE):
        return s
    return f"SELECT * FROM ({s}) AS _sub LIMIT {limit}"


def _format_schema_for_prompt(info: dict, *, with_samples: bool = True) -> str:
    """Render a get_table_info dict as a human-readable block for LLM prompts."""
    table = info.get("table", "?")
    row_count = info.get("row_count", 0)
    columns = info.get("columns") or []
    lines = [f"Table: {table} (rows: {row_count})"]
    for col in columns:
        name = col.get("name", "?")
        col_type = col.get("type", "string")
        nullable = "null" if col.get("nullable", True) else "not null"
        desc = col.get("description") or ""
        unit = col.get("unit")
        unit_part = f", unit: {unit}" if unit else ""
        desc_part = f" -- {desc}" if desc else ""
        sample_part = ""
        if with_samples:
            sample = col.get("sample") or []
            if sample:
                shown = ", ".join(repr(s) for s in sample[:3])
                sample_part = f" [examples: {shown}]"
        lines.append(f"  - {name} ({col_type}, {nullable}{unit_part}){desc_part}{sample_part}")
    return "\n".join(lines)


def _resolve_source_table(
    table_name: str, primary: str | None, aux: list[tuple[str, str]]
) -> tuple[str, str] | None:
    """Decide which data source a table reference belongs to.

    Accepts either a bare name (resolves to the primary), ``main.<table>``
    (also primary), or ``ds_N.<table>`` for N >= 1 (auxiliary source).
    Returns (ds_id, table) or None when the prefix is unknown.
    """
    if not table_name:
        return None
    if "." in table_name:
        prefix, _, real = table_name.partition(".")
        if prefix in ("ds_0", "main"):
            if primary is None:
                return None
            return primary, real
        for i, (_alias, ds_id) in enumerate(aux, start=1):
            if prefix == f"ds_{i}":
                return ds_id, real
        return None
    if primary is None:
        return None
    return primary, table_name


def build_tools(
    data_source_ids: str | list[str] | None,
    *,
    owner_id: str | None = None,
) -> list:
    """Build the agent's tool list.

    Accepts a single id (backward-compat) or a list of ids. The first id
    is the primary; any others are ATTACHed to the same connection for
    cross-source JOINs. The LLM refers to them as ``ds_0`` (primary) and
    ``ds_1``/``ds_2``/... (auxiliary, in the order they were passed).

    Phase 4B: ``owner_id`` is captured by the ``query_database`` closure
    so every lineage record attributes the query to the user who ran it.
    """
    if data_source_ids is None:
        ids: list[str] = []
    elif isinstance(data_source_ids, str):
        ids = [data_source_ids]
    else:
        ids = list(data_source_ids)
    primary = ids[0] if ids else None
    engine = get_engine(primary)
    aux: list[tuple[str, str]] = []  # (alias, ds_id)
    for i, ds_id in enumerate(ids[1:], start=1):
        aux.append((f"ds_{i}", ds_id))

    def _attach_sql() -> tuple[str, str]:
        """Build ATTACH + DETACH statements for the aux sources."""
        if not aux or primary is None:
            return "", ""
        attach = "; ".join(
            f"ATTACH DATABASE '{_sqlite_path(ds_id)}' AS {alias}" for alias, ds_id in aux
        )
        detach = "; ".join(f"DETACH DATABASE {alias}" for alias, _ in aux)
        return attach, detach

    binding_ids: list[str] = list(ids)

    @tool
    def query_database(sql_query: str) -> str:
        """Execute a read-only SQL query and return JSON. Max 100 rows.

        For multi-source sessions, reference the primary's tables with a
        bare name and aux sources via ``ds_N.<table>``. Use ``list_tables``
        first to discover which tables are available in which source.

        Results are cached in-process for 60s keyed by (sql, binding set);
        repeated identical queries within the window skip the SQLite
        round-trip. Every execution (cache hit or miss) is recorded in
        per-data-source lineage for audit.
        """
        err = _is_safe_select(sql_query)
        if err:
            return json.dumps({"error": err}, ensure_ascii=False)
        cache = query_cache.get_cache()
        cache_key = query_cache.QueryCache.make_key(sql_query, binding_ids)
        t_start = time.perf_counter()
        cached = cache.get(cache_key)
        if cached is not None:
            elapsed_ms = (time.perf_counter() - t_start) * 1000.0
            lineage.record_query(
                source_ids=binding_ids,
                sql=sql_query,
                ok=True,
                row_count=cached.get("row_count", 0),
                duration_ms=elapsed_ms,
                cache_hit=True,
                user_id=owner_id,
            )
            payload = {
                "row_count": cached.get("row_count", 0),
                "columns": cached.get("columns", []),
                "rows": cached.get("rows", []),
                "cache_hit": True,
            }
            return json.dumps(payload, ensure_ascii=False)
        try:
            limited = _wrap_with_limit(sql_query, _MAX_ROWS)
            attach, detach = _attach_sql()
            # We need multiple statements (ATTACH + SELECT + DETACH) on the
            # same connection. SQLAlchemy's `text()` rejects multi-statement
            # strings, so we drop down to the raw DBAPI cursor and run all
            # statements through it -- this guarantees ATTACH and SELECT
            # see the same database connection.
            with engine.connect() as conn:
                dbapi_conn = conn.connection.dbapi_connection
                cur = dbapi_conn.cursor()
                if attach:
                    for stmt in attach.split(";"):
                        s = stmt.strip()
                        if s:
                            cur.execute(s)
                cur.execute(limited)
                cols = [c[0] for c in (cur.description or [])]
                rows = cur.fetchall()
                if detach:
                    for stmt in detach.split(";"):
                        s = stmt.strip()
                        if s:
                            cur.execute(s)
                cur.close()
            data = [
                dict(zip(cols, [str(c) if c is not None else None for c in row], strict=False))
                for row in rows
            ]
            cache.set_with_bindings(
                cache_key,
                {"columns": cols, "rows": data, "row_count": len(data)},
                binding_ids,
            )
            elapsed_ms = (time.perf_counter() - t_start) * 1000.0
            lineage.record_query(
                source_ids=binding_ids,
                sql=sql_query,
                ok=True,
                row_count=len(data),
                duration_ms=elapsed_ms,
                cache_hit=False,
                user_id=owner_id,
            )
            return json.dumps(
                {
                    "row_count": len(data),
                    "columns": cols,
                    "rows": data,
                    "cache_hit": False,
                },
                ensure_ascii=False,
            )
        except SQLAlchemyError as e:
            elapsed_ms = (time.perf_counter() - t_start) * 1000.0
            lineage.record_query(
                source_ids=binding_ids,
                sql=sql_query,
                ok=False,
                duration_ms=elapsed_ms,
                cache_hit=False,
                user_id=owner_id,
                error=str(e),
            )
            return json.dumps({"error": f"SQL error: {e}"}, ensure_ascii=False)
        except Exception as e:
            elapsed_ms = (time.perf_counter() - t_start) * 1000.0
            lineage.record_query(
                source_ids=binding_ids,
                sql=sql_query,
                ok=False,
                duration_ms=elapsed_ms,
                cache_hit=False,
                user_id=owner_id,
                error=str(e),
            )
            logger.exception("query_database failed")
            return json.dumps({"error": str(e)}, ensure_ascii=False)

    @tool
    def list_tables() -> str:
        """List all tables across every attached data source.

        The primary source's tables are tagged with alias ``main``; auxiliary
        sources are ``ds_1``, ``ds_2``, ... in attachment order. In SQL,
        the primary's tables are referenced with a bare name (or
        ``main.<table>``); aux tables use ``ds_N.<table>``.
        """
        if primary is None and not aux:
            return json.dumps({"tables": [], "error": "No data source loaded."}, ensure_ascii=False)
        out: list[dict] = []
        if primary is not None:
            for t in data_service.list_tables(primary):
                info = data_service.get_table_info(primary, t)
                if info is None:
                    continue
                out.append(
                    {
                        "alias": "main",
                        "table": t,
                        "row_count": info.get("row_count", 0),
                        "columns": [
                            {"name": c.get("name"), "type": c.get("type", "string")}
                            for c in (info.get("columns") or [])
                        ],
                    }
                )
        for i, (_alias, ds_id) in enumerate(aux, start=1):
            for t in data_service.list_tables(ds_id):
                info = data_service.get_table_info(ds_id, t)
                if info is None:
                    continue
                out.append(
                    {
                        "alias": f"ds_{i}",
                        "table": t,
                        "row_count": info.get("row_count", 0),
                        "columns": [
                            {"name": c.get("name"), "type": c.get("type", "string")}
                            for c in (info.get("columns") or [])
                        ],
                    }
                )
        return json.dumps({"tables": out}, ensure_ascii=False)

    @tool
    def get_table_schema(table_name: str | None = None) -> str:
        """Return the full schema for one table: columns, types, descriptions, units, samples.

        Args:
            table_name: The table to describe. Use a bare name for the
                primary's tables, or ``ds_N.<table>`` for an aux source.
                If omitted, returns the primary's first table.
        """
        if primary is None and not aux:
            return json.dumps({"error": "No data source loaded."}, ensure_ascii=False)
        if table_name is None:
            target = data_service.get_primary_table(primary or aux[0][1])  # type: ignore[arg-type]
            ds_for_target = primary
        else:
            resolved = _resolve_source_table(table_name, primary, aux)
            if resolved is None:
                return json.dumps(
                    {"error": f"Unknown source prefix in '{table_name}'."},
                    ensure_ascii=False,
                )
            ds_for_target, target = resolved
        info = data_service.get_table_info(ds_for_target, target)
        if info is None:
            return json.dumps({"error": f"Table '{target}' not found."}, ensure_ascii=False)
        info = {**info, "source_id": ds_for_target}
        return json.dumps(info, ensure_ascii=False)

    @tool
    def get_sample_rows(table_name: str | None = None, limit: int = 5) -> str:
        """Fetch a small sample of rows from a table. Big tables (>50k rows) cap at 5.

        Args:
            table_name: The table to sample. Bare name for the primary,
                ``ds_N.<table>`` for an aux source. Defaults to the
                primary's first table.
            limit: Max rows to return. Capped at 100.
        """
        if primary is None and not aux:
            return json.dumps({"error": "No data source loaded."}, ensure_ascii=False)
        if table_name is None:
            target = data_service.get_primary_table(primary or aux[0][1])  # type: ignore[arg-type]
            ds_for_target = primary
        else:
            resolved = _resolve_source_table(table_name, primary, aux)
            if resolved is None:
                return json.dumps(
                    {"error": f"Unknown source prefix in '{table_name}'."},
                    ensure_ascii=False,
                )
            ds_for_target, target = resolved
        limit = max(1, min(limit, 100))
        info = data_service.get_table_info(ds_for_target, target)
        if info is not None and info.get("row_count", 0) > _LARGE_TABLE_ROW_THRESHOLD:
            limit = min(limit, _LARGE_TABLE_SAMPLE_SIZE)
        rows = data_service.get_sample_rows(ds_for_target, limit=limit, table=target)
        if rows is None:
            return json.dumps(
                {"error": f"Table '{target}' not found or empty."}, ensure_ascii=False
            )
        return json.dumps(
            {"source_id": ds_for_target, "table": target, "row_count": len(rows), "rows": rows},
            ensure_ascii=False,
        )

    @tool
    def create_chart(
        chart_type: str,
        title: str,
        x_data: list[str],
        series: list[dict],
    ) -> str:
        """Register a chart to render alongside the assistant's reply.

        Args:
            chart_type: One of 'bar', 'line', 'pie', 'scatter'.
            title: Chart title shown above the plot.
            x_data: Category labels for the x-axis (or slice names for pie).
            series: List of series, each like {"name": "Sales", "data": [120, 95, 71]}.
        """
        return json.dumps(
            {"status": "ok", "chart_type": chart_type, "series_count": len(series)},
            ensure_ascii=False,
        )

    tools_list = [
        query_database,
        list_tables,
        get_table_schema,
        get_sample_rows,
        create_chart,
    ]
    if primary is None and not aux:

        @tool
        def no_data_loaded() -> str:
            """Indicate that no data source has been uploaded yet."""
            return json.dumps(
                {"error": "No data source loaded. Ask the user to upload a CSV/Excel file first."},
                ensure_ascii=False,
            )

        tools_list.append(no_data_loaded)
    return tools_list
