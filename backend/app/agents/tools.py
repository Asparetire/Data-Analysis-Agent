from __future__ import annotations

import json
import re

from langchain_core.tools import tool
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError

from ..services import data_service
from ..utils.database import get_engine
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
    """Render a get_table_info dict as a human-readable block for LLM prompts.

    Includes column descriptions, units, and a small sample slice when
    available -- this is the difference between "vague" SQL and SQL the
    model gets right on the first try.
    """
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


def build_tools(data_source_id: str | None) -> list:
    engine = get_engine(data_source_id)

    @tool
    def query_database(sql_query: str) -> str:
        """Execute a read-only SQL query and return JSON. Max 100 rows."""
        err = _is_safe_select(sql_query)
        if err:
            return json.dumps({"error": err}, ensure_ascii=False)
        try:
            limited = _wrap_with_limit(sql_query, _MAX_ROWS)
            with engine.connect() as conn:
                result = conn.execute(text(limited))
                rows = result.fetchall()
                cols = list(result.keys())
            data = [
                dict(zip(cols, [str(c) if c is not None else None for c in row], strict=False))
                for row in rows
            ]
            return json.dumps(
                {"row_count": len(data), "columns": cols, "rows": data},
                ensure_ascii=False,
            )
        except SQLAlchemyError as e:
            return json.dumps({"error": f"SQL error: {e}"}, ensure_ascii=False)
        except Exception as e:
            logger.exception("query_database failed")
            return json.dumps({"error": str(e)}, ensure_ascii=False)

    @tool
    def list_tables() -> str:
        """List all tables in the current data source with row counts and a brief column list.

        Call this first to understand the data structure. For deeper detail
        (descriptions, units, samples) on one table, follow up with
        ``get_table_schema(table_name=...)``.
        """
        if data_source_id is None:
            return json.dumps({"tables": [], "error": "No data source loaded."}, ensure_ascii=False)
        tables = data_service.list_tables(data_source_id)
        if not tables:
            return json.dumps({"tables": [], "error": "No tables found."}, ensure_ascii=False)
        summaries = []
        for t in tables:
            info = data_service.get_table_info(data_source_id, t)
            if info is None:
                continue
            summaries.append(
                {
                    "table": t,
                    "row_count": info.get("row_count", 0),
                    "columns": [
                        {"name": c.get("name"), "type": c.get("type", "string")}
                        for c in (info.get("columns") or [])
                    ],
                }
            )
        return json.dumps({"tables": summaries}, ensure_ascii=False)

    @tool
    def get_table_schema(table_name: str | None = None) -> str:
        """Return the full schema for one table: columns, types, descriptions, units, samples.

        Args:
            table_name: The table to describe. If omitted, returns the primary
                (first) table. Use ``list_tables`` first if you're not sure
                which tables exist.
        """
        if data_source_id is None:
            return json.dumps({"error": "No data source loaded."}, ensure_ascii=False)
        target = table_name or data_service.get_primary_table(data_source_id)
        info = data_service.get_table_info(data_source_id, target)
        if info is None:
            return json.dumps({"error": f"Table '{target}' not found."}, ensure_ascii=False)
        return json.dumps(info, ensure_ascii=False)

    @tool
    def get_sample_rows(table_name: str | None = None, limit: int = 5) -> str:
        """Fetch a small sample of rows from a table. For large tables (>50k rows) the default sample shrinks to 5.

        Args:
            table_name: The table to sample. Defaults to the primary table.
            limit: Maximum rows to return. Capped at 100.
        """
        if data_source_id is None:
            return json.dumps({"error": "No data source loaded."}, ensure_ascii=False)
        target = table_name or data_service.get_primary_table(data_source_id)
        limit = max(1, min(limit, 100))
        info = data_service.get_table_info(data_source_id, target)
        if info is not None and info.get("row_count", 0) > _LARGE_TABLE_ROW_THRESHOLD:
            limit = min(limit, _LARGE_TABLE_SAMPLE_SIZE)
        rows = data_service.get_sample_rows(data_source_id, limit=limit, table=target)
        if rows is None:
            return json.dumps(
                {"error": f"Table '{target}' not found or empty."}, ensure_ascii=False
            )
        return json.dumps(
            {"table": target, "row_count": len(rows), "rows": rows},
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
        # The post_process node picks these args up out of tool_calls and
        # converts them into an ECharts option dict. Returning a small ack
        # is enough for the LLM to know the call succeeded.
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
    if data_source_id is None:

        @tool
        def no_data_loaded() -> str:
            """Indicate that no data source has been uploaded yet."""
            return json.dumps(
                {"error": "No data source loaded. Ask the user to upload a CSV/Excel file first."},
                ensure_ascii=False,
            )

        tools_list.append(no_data_loaded)
    return tools_list
