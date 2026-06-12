from __future__ import annotations

import json
import re

from langchain_core.tools import tool
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError

from ..services.data_service import UPLOAD_TABLE
from ..utils.database import get_engine
from ..utils.logger import get_logger

logger = get_logger(__name__)

_MAX_ROWS = 100
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
    def get_table_schema() -> str:
        """Return the schema of the current uploaded_data table (columns, types, row count)."""
        try:
            with engine.connect() as conn:
                rows = conn.execute(text(f'PRAGMA table_info("{UPLOAD_TABLE}")')).fetchall()
                schema = [{"name": row[1], "type": row[2], "nullable": not row[3]} for row in rows]
                count = conn.execute(text(f'SELECT COUNT(*) FROM "{UPLOAD_TABLE}"')).scalar()
            return json.dumps(
                {"table": UPLOAD_TABLE, "row_count": count, "columns": schema},
                ensure_ascii=False,
            )
        except SQLAlchemyError as e:
            return json.dumps({"error": f"SQL error: {e}"}, ensure_ascii=False)

    @tool
    def create_chart(
        chart_type: str,
        title: str,
        x_data: list[str],
        series: list[dict],
    ) -> str:
        """Register a chart to render alongside the assistant's reply.

        Args:
            chart_type: One of 'bar', 'line', 'pie'.
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

    tools_list = [query_database, get_table_schema, create_chart]
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
