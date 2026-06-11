from __future__ import annotations

import os
from typing import Optional

from langchain_core.messages import HumanMessage, SystemMessage
from langchain_openai import ChatOpenAI
from langgraph.graph import END, StateGraph
from langgraph.prebuilt import ToolNode

from .state import AgentState
from .tools import build_tools

SYSTEM_PROMPT = """你是一个专业的数据分析助手。用户会上传 CSV/Excel 等数据文件并提出分析问题。

工作流程：
1. 第一次回答前，先调用 get_table_schema 了解数据结构（列名、类型）。
2. 用 query_database 执行只读 SQL 查询获取数据（最多 100 行）。
3. 基于结果用中文清晰回答用户问题。
4. 如果需要图表，把数据组织成 ECharts 兼容的 JSON 放在 chart_data 字段里。
5. 遇到错误时友好告知用户可能的原因。

注意：
- 只能生成 SELECT / WITH 开头的查询，不要尝试修改数据。
- 列名含空格或特殊字符时用双引号包裹。
"""


def _build_llm(temperature: float = 0):
    return ChatOpenAI(
        model=os.getenv("OPENAI_MODEL", "gpt-4o"),
        temperature=temperature,
        streaming=True,
    )


def build_graph(data_source_id: Optional[str] = None):
    """根据 data_source_id 构造一个 LangGraph 编译图。"""
    tools = build_tools(data_source_id)
    llm = _build_llm().bind_tools(tools)
    tool_node = ToolNode(tools)

    def call_model(state: AgentState):
        messages = [SystemMessage(content=SYSTEM_PROMPT), *state["messages"]]
        response = llm.invoke(messages)
        return {"messages": [response]}

    def should_continue(state: AgentState):
        last = state["messages"][-1]
        if hasattr(last, "tool_calls") and last.tool_calls:
            return "tools"
        return END

    workflow = StateGraph(AgentState)
    workflow.add_node("model", call_model)
    workflow.add_node("tools", tool_node)
    workflow.set_entry_point("model")
    workflow.add_conditional_edges(
        "model",
        should_continue,
        {"tools": "tools", END: END},
    )
    workflow.add_edge("tools", "model")
    return workflow.compile()


def initial_state(session_id: str, message: str, data_source_id: Optional[str]) -> dict:
    return {
        "messages": [HumanMessage(content=message)],
        "session_id": session_id,
        "data_source_id": data_source_id,
        "sql_query": None,
        "analysis_result": None,
        "chart_data": None,
        "error": None,
    }
