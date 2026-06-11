from typing import TypedDict, List, Optional, Annotated
from langgraph.graph.message import add_messages
from langchain_core.messages import BaseMessage

class AgentState(TypedDict):
    """LangGraph Agent状态定义"""
    messages: Annotated[List[BaseMessage], add_messages]
    session_id: str
    data_source_id: Optional[str]
    sql_query: Optional[str]
    analysis_result: Optional[str]
    chart_data: Optional[dict]
    error: Optional[str]