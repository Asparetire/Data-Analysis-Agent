from __future__ import annotations

from typing import Any

from langchain_core.messages import AIMessage

from ..models.schemas import ChatResponse
from ..utils.logger import get_logger
from . import metadata_service, session_service

logger = get_logger(__name__)


class SessionBindingError(Exception):
    """Raised when a single-id request conflicts with the session's primary.

    Phase 3C: sessions can hold multiple bindings, so the only remaining
    conflict case is ``data_source_id`` (singular) disagreeing with the
    primary. Multi-id requests are merged in instead.
    """

    def __init__(self, bound_to: str, requested: str):
        self.bound_to = bound_to
        self.requested = requested
        super().__init__(f"Session is bound to data source {bound_to}; cannot use {requested}")


class SessionNotFound(Exception):
    """Raised when the session id does not exist (404 in the route layer)."""


class SessionForbidden(Exception):
    """Raised when the session or a bound data source is not owned by the
    requesting user. Collapsed to 404 by the route layer so ids are not
    enumerable."""


def _extract_final_text(messages) -> str:
    """Return the content of the most recent AI message, or ''."""
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


async def _resolve_session(
    session_id: str,
    data_source_id: str | None,
    data_source_ids: list[str] | None,
    *,
    owner_id: str | None = None,
) -> dict:
    """Get (or create) the session and merge in the requested bindings.

    Phase 4A: when ``owner_id`` is supplied, every existing session must
    already be owned by that user — otherwise we raise ``SessionForbidden``
    (the route layer turns this into 404). Newly created sessions are
    stamped with ``owner_id`` so future requests can re-assert ownership.

    Rules:
    - Missing session: create one (owned by ``owner_id``) and bind the
      requested ids (deduped).
    - Single-id request that disagrees with the primary: still an error.
    - Multi-id request: merged into the session's binding list.
    - Single-id request with no primary yet: bind it.
    """
    session = await session_service.get_session(session_id)
    if session is None:
        if owner_id is None:
            # Refuse to create ownerless sessions via the authed path.
            raise SessionNotFound(session_id)
        new_id = await session_service.create_session(owner_id=owner_id)
        merged: list[str] = []
        if data_source_id and data_source_id not in merged:
            merged.append(data_source_id)
        if data_source_ids:
            for i in data_source_ids:
                if i and i not in merged:
                    merged.append(i)
        if merged:
            session = await session_service.set_data_source_ids(new_id, merged)
        else:
            session = await session_service.get_session(new_id)
        return session  # type: ignore[return-value]

    if owner_id is not None:
        session_owner = session.get("owner_id")
        if not session_owner or session_owner != owner_id:
            raise SessionForbidden(session_id)

    bound_primary = session.get("data_source_id")
    if data_source_id and bound_primary and data_source_id != bound_primary and not data_source_ids:
        raise SessionBindingError(bound_to=bound_primary, requested=data_source_id)

    if data_source_ids:
        existing = list(session.get("data_source_ids") or [])
        if data_source_id and data_source_id not in existing:
            existing.append(data_source_id)
        for i in data_source_ids:
            if i and i not in existing:
                existing.append(i)
        session = await session_service.set_data_source_ids(session_id, existing)  # type: ignore[assignment]
    elif data_source_id and not bound_primary:
        session = await session_service.bind_data_source(session_id, data_source_id)
    return session  # type: ignore[return-value]


def _assert_owns_data_sources(owner_id: str, ds_ids: list[str]) -> None:
    """Raise SessionForbidden if any of ``ds_ids`` is not owned by ``owner_id``."""
    for ds_id in ds_ids:
        owner = metadata_service.get_owner(ds_id)
        if not owner or owner != owner_id:
            raise SessionForbidden(ds_id)


async def run_chat(
    session_id: str,
    message: str,
    data_source_id: str | None = None,
    data_source_ids: list[str] | None = None,
    *,
    owner_id: str | None = None,
) -> ChatResponse:
    """Run one chat turn with Redis-backed session memory."""
    try:
        session = await _resolve_session(
            session_id, data_source_id, data_source_ids, owner_id=owner_id
        )
    except SessionBindingError as e:
        return ChatResponse(
            session_id=session_id,
            message="",
            error=str(e),
        )
    except (SessionNotFound, SessionForbidden):
        # Bubble up to the route layer so it returns the right HTTP status.
        raise
    except Exception as e:
        logger.exception("session resolution failed")
        return ChatResponse(
            session_id=session_id,
            message="",
            error=f"Session error: {e}",
        )

    # Phase 4A: every bound data source must be owned by the same user.
    active_session_id = session["session_id"]
    active_data_source_id = session.get("data_source_id")
    all_ids = list(session.get("data_source_ids") or [])
    if active_data_source_id and active_data_source_id not in all_ids:
        all_ids.insert(0, active_data_source_id)
    if owner_id is not None and all_ids:
        _assert_owns_data_sources(owner_id, all_ids)

    history = session.get("chat_history") or []

    # Local import: app.agents.graph transitively imports app.services
    # modules, so a top-level import would cycle.
    from ..agents.graph import build_graph, initial_state

    graph = build_graph(
        data_source_id=active_data_source_id,
        data_source_ids=all_ids,
        chat_history=history,
        owner_id=owner_id,
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
