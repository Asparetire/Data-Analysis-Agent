from __future__ import annotations

from typing import Any

from langchain_core.messages import AIMessage

from ..models.schemas import ChatResponse
from ..utils.logger import get_logger
from . import session_service

logger = get_logger(__name__)


class SessionBindingError(Exception):
    """Raised when a request's data_source_id conflicts with the session's binding."""

    def __init__(self, bound_to: str, requested: str):
        self.bound_to = bound_to
        self.requested = requested
        super().__init__(f"Session is bound to data source {bound_to}; cannot use {requested}")


def _extract_final_text(messages) -> str:
    """Return the content of the most recent AI message, or ""."""
    for msg in reversed(messages):
        if isinstance(msg, AIMessage):
            content = msg.content
            if isinstance(content, str):
                return content
            if isinstance(content, list):
                return "".join(
                    block.get("text", "") if isinstance(block, dict) else str(block)
                    for block in content
                )
    return ""


async def _resolve_session(session_id: str, data_source_id: str | None) -> dict:
    """Get (or create) the session and apply the data_source_id binding rules.

    Rules:
    - If the session is missing, create one. Bind the requested data_source_id.
    - If the session already has a data_source_id, the request must match it.
    - If the session has no data_source_id yet, bind the requested one.
    """
    session = await session_service.get_session(session_id)
    if session is None:
        new_id = await session_service.create_session()
        if data_source_id:
            session = await session_service.bind_data_source(new_id, data_source_id)
        else:
            session = await session_service.get_session(new_id)
        return session  # type: ignore[return-value]

    bound = session.get("data_source_id")
    if bound and data_source_id and bound != data_source_id:
        raise SessionBindingError(bound_to=bound, requested=data_source_id)

    if not bound and data_source_id:
        session = await session_service.bind_data_source(session_id, data_source_id)
    return session  # type: ignore[return-value]


async def run_chat(
    session_id: str,
    message: str,
    data_source_id: str | None = None,
) -> ChatResponse:
    """Run one chat turn with Redis-backed session memory."""
    try:
        session = await _resolve_session(session_id, data_source_id)
    except SessionBindingError as e:
        return ChatResponse(
            session_id=session_id,
            message="",
            error=str(e),
        )
    except Exception as e:
        logger.exception("session resolution failed")
        return ChatResponse(
            session_id=session_id,
            message="",
            error=f"Session error: {e}",
        )

    active_session_id = session["session_id"]
    active_data_source_id = session.get("data_source_id")
    history = session.get("chat_history") or []

    # Local import: app.agents.graph transitively imports app.services
    # modules, so a top-level import would cycle.
    from ..agents.graph import build_graph, initial_state

    graph = build_graph(
        data_source_id=active_data_source_id,
        chat_history=history,
    )
    state = initial_state(
        session_id=active_session_id,
        message=message,
        data_source_id=active_data_source_id,
    )

    try:
        result = await graph.ainvoke(state)
    except Exception as e:
        logger.exception("chat run failed")
        return ChatResponse(
            session_id=active_session_id,
            message="",
            error=f"Chat failed: {e}",
        )

    messages = result.get("messages") or []
    final_text = _extract_final_text(messages)
    chart_data: Any = result.get("chart_data")
    sql_query = result.get("sql_query")
    query_result = result.get("analysis_result")

    # Safety net: if the model left the AIMessage content empty (common with
    # code-completion-leaning models after a tool call), fall back to a
    # human-readable note so the UI never shows a blank message.
    if not final_text.strip():
        if isinstance(query_result, dict) and query_result.get("error"):
            final_text = f"查询出错：{query_result['error']}"
        elif sql_query:
            final_text = "查询已执行,见上方 SQL 与图表。"
        else:
            final_text = "(模型未返回文字说明)"

    # Persist the new turn. We append the user message verbatim, then the
    # assistant message along with the chart/sql snapshot so the next reload
    # can rehydrate everything.
    await session_service.append_chat(active_session_id, "user", message)
    await session_service.append_chat(
        active_session_id,
        "assistant",
        final_text,
        chart_data=chart_data,
        sql_query=sql_query,
    )

    # Only overwrite the snapshot when the run actually produced a query result.
    if isinstance(query_result, dict) and "error" not in query_result:
        await session_service.set_intermediate(
            active_session_id,
            query_result,
            last_query=sql_query,
        )
    elif sql_query:
        await session_service.set_intermediate(active_session_id, None, last_query=sql_query)

    return ChatResponse(
        session_id=active_session_id,
        message=final_text,
        chart_data=chart_data,
        sql_query=sql_query,
        error=result.get("error"),
    )
