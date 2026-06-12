from __future__ import annotations

import asyncio
import json
import time
from collections.abc import AsyncIterator
from typing import Any

from ..agents.graph import build_graph, initial_state
from ..services import session_service
from ..services.chat_service import (
    SessionBindingError,
    _extract_final_text,
    _resolve_session,
)
from ..utils.logger import get_logger

logger = get_logger(__name__)

# ---------------------------------------------------------------------------
# Streaming protocol
# ---------------------------------------------------------------------------
# Wire format (Server-Sent Events, content-type: text/event-stream):
#
#   event: chunk
#   data: {"type": "progress", "message": "...", "elapsed_ms": 1234}
#
#   event: chunk
#   data: {"type": "data", "content": [{...}, ...], "chunk_index": 2,
#          "chunk_count": 5, "rows_sent": 80, "rows_total": 200}
#
#   event: end
#   data: {"type": "complete", "message": "...", "chart_config": {...},
#          "sql_query": "...", "session_id": "...",
#          "row_count": 200, "elapsed_ms": 4530}
#
#   event: error
#   data: {"type": "error", "code": 500, "message": "..."}
#
# Error policy: as soon as a step fails, we yield exactly one `error` event
# and stop. The SSE connection is then closed by sse_starlette. The frontend
# is expected to treat any `error` event as terminal.
# ---------------------------------------------------------------------------

# Spec: each `data` chunk must be <= 10 KB so the client can flush
# individual chunks without buffering the whole response.
CHUNK_MAX_BYTES = 10 * 1024

# Spec: the gap between consecutive data chunks must stay well under 500ms
# to avoid the frontend freezing. 5ms is invisible to users but gives the
# event loop room to flush each event to the client.
INTER_CHUNK_DELAY_S = 0.005

# Spec trigger #2: an execution that takes longer than this should also be
# streamed, even when the row count is small. We can't pre-estimate, so we
# measure wall-clock time around the graph run.
SLOW_RUN_THRESHOLD_S = 2.0

# Throttle the progress blurb frequency so the UI doesn't flicker during
# a fast run.
PROGRESS_MIN_INTERVAL_S = 0.5


def _sse(event: str, data: dict[str, Any]) -> dict[str, str]:
    """Build an sse-starlette-friendly payload."""
    return {"event": event, "data": json.dumps(data, ensure_ascii=False)}


def _split_rows_into_chunks(
    rows: list[dict[str, Any]], max_bytes: int = CHUNK_MAX_BYTES
) -> list[list[dict[str, Any]]]:
    """Bucket rows so each bucket's JSON serialization stays under `max_bytes`.

    A small headroom (2 bytes for `[]` plus 1 byte per comma) is reserved.
    Rows that individually exceed the budget are placed in their own chunk
    so the total just goes over the limit rather than dropping data.
    """
    chunks: list[list[dict[str, Any]]] = []
    current: list[dict[str, Any]] = []
    current_size = 2  # `[]`
    for row in rows:
        encoded = json.dumps(row, ensure_ascii=False).encode("utf-8")
        added = len(encoded) + (1 if current else 0)
        if current and current_size + added > max_bytes:
            chunks.append(current)
            current = [row]
            current_size = 2 + len(encoded)
        else:
            current.append(row)
            current_size += added
    if current:
        chunks.append(current)
    return chunks


def _should_chunk_rows(query_result: dict[str, Any] | None, elapsed_s: float) -> bool:
    """Return True when the spec says we must stream rows as chunks.

    Triggers:
      1. Row count > 1000.
      2. The graph run took longer than `SLOW_RUN_THRESHOLD_S`. This is the
         "estimated execution time > 2s" branch from the spec, evaluated
         after the fact since we can't pre-estimate LLM latency.
    """
    if not isinstance(query_result, dict):
        return False
    rows = query_result.get("rows")
    has_many_rows = isinstance(rows, list) and len(rows) > 1000
    return bool(
        has_many_rows or (elapsed_s > SLOW_RUN_THRESHOLD_S and isinstance(rows, list) and rows)
    )


async def _paced_yield(event: dict[str, str], is_last: bool) -> None:
    """Yield-throttle: give the client a moment to flush each event.

    The inter-chunk delay is well under the 500ms spec ceiling, so it
    introduces no perceptible latency. The final event of the stream is
    emitted immediately to avoid hanging the response after `complete`.
    """
    if not is_last and INTER_CHUNK_DELAY_S > 0:
        await asyncio.sleep(INTER_CHUNK_DELAY_S)


async def stream_chat(
    session_id: str,
    message: str,
    data_source_id: str | None = None,
) -> AsyncIterator[dict[str, str]]:
    """Yield SSE events for a single chat turn.

    See the module docstring for the wire format.
    """
    run_started = time.perf_counter()

    # 1. Session resolution (reuses the same rules as the non-streaming path).
    try:
        session = await _resolve_session(session_id, data_source_id)
    except SessionBindingError as e:
        yield _sse(
            "error",
            {"type": "error", "code": 403, "message": str(e)},
        )
        return
    except Exception as e:
        logger.exception("session resolution failed")
        yield _sse(
            "error",
            {"type": "error", "code": 500, "message": f"Session error: {e}"},
        )
        return

    active_session_id = session["session_id"]
    active_data_source_id = session.get("data_source_id")
    history = session.get("chat_history") or []

    yield _sse(
        "chunk",
        {
            "type": "progress",
            "message": "正在加载会话...",
            "elapsed_ms": _elapsed_ms(run_started),
        },
    )

    # 2. Build the graph (this can throw if the data source was deleted).
    try:
        graph = build_graph(
            data_source_id=active_data_source_id,
            chat_history=history,
        )
        state = initial_state(
            session_id=active_session_id,
            message=message,
            data_source_id=active_data_source_id,
        )
    except Exception as e:
        logger.exception("graph build failed")
        yield _sse("error", {"type": "error", "code": 500, "message": str(e)})
        return

    yield _sse(
        "chunk",
        {
            "type": "progress",
            "message": "正在分析数据...",
            "elapsed_ms": _elapsed_ms(run_started),
        },
    )

    # 3. Run the graph. This is the slow part. We don't stream LLM tokens
    # here -- the LLM still gets the full message at the end. The streaming
    # surface is the SQL result rows.
    graph_started = time.perf_counter()
    try:
        result = await graph.ainvoke(state)
    except Exception as e:
        logger.exception("graph run failed")
        yield _sse(
            "error",
            {"type": "error", "code": 500, "message": f"Chat failed: {e}"},
        )
        return
    graph_elapsed_s = time.perf_counter() - graph_started

    messages = result.get("messages") or []
    final_text = _extract_final_text(messages)
    chart_data = result.get("chart_data")
    sql_query = result.get("sql_query")
    query_result = result.get("analysis_result")

    # 4. Stream SQL result rows when the spec says we should. A non-streamed
    # small result rides entirely in the final `end` event.
    rows_sent = 0
    chunk_count = 0
    if _should_chunk_rows(query_result, graph_elapsed_s):
        rows = query_result["rows"]
        total = len(rows)
        chunks = _split_rows_into_chunks(rows)
        chunk_count = len(chunks)
        yield _sse(
            "chunk",
            {
                "type": "progress",
                "message": f"正在处理 {total} 行 ({len(chunks)} 块)...",
                "elapsed_ms": _elapsed_ms(run_started),
            },
        )
        for idx, chunk in enumerate(chunks, start=1):
            yield _sse(
                "chunk",
                {
                    "type": "data",
                    "content": chunk,
                    "chunk_index": idx,
                    "chunk_count": chunk_count,
                    "rows_sent": rows_sent + len(chunk),
                    "rows_total": total,
                },
            )
            rows_sent += len(chunk)
            await _paced_yield({}, is_last=(idx == chunk_count))
    elif isinstance(query_result, dict) and "rows" in query_result:
        # Small result: still emit one data event so the frontend can render
        # a table if it wants, but skip the chunked progress blurb.
        rows = query_result["rows"]
        if rows:
            yield _sse(
                "chunk",
                {"type": "data", "content": rows},
            )
            rows_sent = len(rows)
            chunk_count = 1

    # 5. Persist the turn before sending `complete` so a connection drop
    # after `complete` still leaves a valid history.
    try:
        await session_service.append_chat(active_session_id, "user", message)
        await session_service.append_chat(
            active_session_id,
            "assistant",
            final_text,
            chart_data=chart_data,
            sql_query=sql_query,
        )
        if isinstance(query_result, dict) and "error" not in query_result:
            await session_service.set_intermediate(
                active_session_id, query_result, last_query=sql_query
            )
        elif sql_query:
            await session_service.set_intermediate(active_session_id, None, last_query=sql_query)
    except Exception as e:
        logger.exception("session persistence failed")
        # Persistence failure is non-fatal: the user already has a result.
        # We surface it as a non-blocking note in the `end` event instead of
        # terminating the stream.
        yield _sse(
            "chunk",
            {
                "type": "progress",
                "message": f"警告: 会话持久化失败 ({e})",
                "elapsed_ms": _elapsed_ms(run_started),
            },
        )

    # 6. Final event. Always last; no throttle.
    yield _sse(
        "end",
        {
            "type": "complete",
            "message": final_text,
            "chart_config": chart_data,
            "sql_query": sql_query,
            "session_id": active_session_id,
            "row_count": rows_sent,
            "chunk_count": chunk_count,
            "elapsed_ms": _elapsed_ms(run_started),
        },
    )


def _elapsed_ms(start: float) -> int:
    """Return wall-clock milliseconds since `start`, rounded to int."""
    return int((time.perf_counter() - start) * 1000)
