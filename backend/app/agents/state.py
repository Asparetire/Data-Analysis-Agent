from __future__ import annotations

from typing import Annotated, Any, TypedDict

from langchain_core.messages import BaseMessage
from langgraph.graph.message import add_messages


class AgentState(TypedDict):
    """LangGraph agent state.

    `messages` is the canonical conversation log; the rest are scratch fields
    the agent fills in along the way.
    """

    messages: Annotated[list[BaseMessage], add_messages]
    session_id: str
    data_source_id: str | None
    sql_query: str | None
    analysis_result: Any | None
    chart_data: dict | None
    chart_args: dict | None
    error: str | None
