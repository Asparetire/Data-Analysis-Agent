from __future__ import annotations

import json
import os
from collections.abc import Sequence
from typing import Any

import pandas as pd
from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, SystemMessage
from langchain_openai import ChatOpenAI
from langgraph.graph import END, StateGraph
from langgraph.prebuilt import ToolNode

from .state import AgentState
from .tools import build_tools

SYSTEM_PROMPT = """你是一个专业的数据分析助手。用户会先上传 CSV/Excel 等数据文件，然后用自然语言向你提问。
工作流程：
1. 第一次回答前，先调用 get_table_schema 了解数据结构（列名、类型、行数）。
2. 通过 query_database 执行只读 SQL 查询获取数据（最多 100 行）。
3. 用中文清晰回答用户问题，必要时给出关键数字与解读。
4. 如果需要可视化，调用 create_chart 工具，把结构化数据传进去；系统会自动渲染为 ECharts 图表。
5. 遇到错误时用友好语言告诉用户可能的原因，并给出修复建议。
注意事项：
- 只能生成 SELECT / WITH 开头的查询，禁止任何写操作（INSERT/UPDATE/DELETE/DROP 等）。
- 列名含空格或特殊字符时用双引号包裹，例如 "Order ID"。
- create_chart 工具的 chart_type 必须是 'bar'、'line'、'pie' 之一。
- 不要重复解释自己的工具调用过程，除非对用户有帮助。"""


def _build_llm(temperature: float = 0):
    return ChatOpenAI(
        model=os.getenv("OPENAI_MODEL", "gpt-4o"),
        temperature=temperature,
        streaming=True,
    )


def _find_create_chart_args(messages) -> dict | None:
    for msg in reversed(messages):
        tool_calls = getattr(msg, "tool_calls", None)
        if not tool_calls:
            continue
        for tc in tool_calls:
            if tc.get("name") == "create_chart":
                return tc.get("args") or {}
    return None


def _find_sql_query(messages) -> str | None:
    for msg in reversed(messages):
        tool_calls = getattr(msg, "tool_calls", None)
        if not tool_calls:
            continue
        for tc in tool_calls:
            if tc.get("name") == "query_database":
                args = tc.get("args") or {}
                sql = args.get("sql_query")
                if sql:
                    return sql
    return None


def _last_query_result(messages) -> dict | None:
    """Return the most recent query_database tool result as a dict, or None."""
    for msg in reversed(messages):
        # ToolMessage exposes .name and .content (JSON string from the tool).
        name = getattr(msg, "name", None)
        if name != "query_database":
            continue
        content = getattr(msg, "content", None)
        if not isinstance(content, str):
            continue
        try:
            return json.loads(content)
        except json.JSONDecodeError:
            continue
    return None


def _last_user_query(messages) -> str:
    for msg in reversed(messages):
        if isinstance(msg, HumanMessage):
            return msg.content or ""
    return ""


def post_process(state: AgentState) -> dict:
    """Run the visualization pipeline against the last query result.

    Pipeline:
      1. Pull the last query_database tool result and the LLM's create_chart args.
      2. Convert the result rows to a DataFrame.
      3. build_chart_spec(...) — auto-recommend or follow the LLM suggestion,
         then apply any user overrides (改类型 / 字段用颜色 / 添加标题).
      4. echarts_from_spec(spec) — translate to the ECharts option dict the
         frontend renders. None for TABLE (the frontend renders its own table).

    We always return the spec dump alongside the ECharts option so the front-
    end / future tooling can inspect the original intent.
    """
    # Local import: app.services.visualization transitively imports this
    # module via chat_service / streaming, so a top-level import would cycle.
    from ..services.visualization import build_chart_spec, echarts_from_spec

    messages = state["messages"]
    args = _find_create_chart_args(messages)
    sql_query = _find_sql_query(messages)
    analysis_result = _last_query_result(messages)

    chart_data: dict[str, Any] | None = None
    chart_spec_dump: dict[str, Any] | None = None

    if isinstance(analysis_result, dict):
        rows = analysis_result.get("rows") or []
        if rows:
            # query_database already returns rows as a list of dicts with
            # column-name keys, so pd.DataFrame(rows) works directly.
            df = pd.DataFrame(rows)
            spec = build_chart_spec(
                df=df,
                llm_suggestion=args,
                user_query=_last_user_query(messages),
            )
            chart_data = echarts_from_spec(spec)
            chart_spec_dump = spec.model_dump()

    return {
        "chart_data": chart_data,
        "chart_spec": chart_spec_dump,
        "sql_query": sql_query,
        "analysis_result": analysis_result,
    }


def history_to_messages(history: Sequence[dict]) -> list[BaseMessage]:
    """Convert persisted chat_history (plain dicts) back into BaseMessages."""
    out: list[BaseMessage] = []
    for item in history or []:
        role = item.get("role")
        content = item.get("content") or ""
        if role == "user":
            out.append(HumanMessage(content=content))
        elif role == "assistant":
            out.append(AIMessage(content=content))
    return out


def build_graph(
    data_source_id: str | None = None,
    chat_history: Sequence[dict] | None = None,
):
    """Build a LangGraph compiled graph for a given data source.

    `chat_history` is prepended to the running messages so the LLM sees the
    full conversation. The current turn's HumanMessage is supplied separately
    via `initial_state`.
    """
    tools = build_tools(data_source_id)
    llm = _build_llm().bind_tools(tools)
    tool_node = ToolNode(tools)
    prior_messages = history_to_messages(chat_history or [])

    def call_model(state: AgentState):
        messages = [SystemMessage(content=SYSTEM_PROMPT), *prior_messages, *state["messages"]]
        response = llm.invoke(messages)
        return {"messages": [response]}

    def should_continue(state: AgentState):
        last = state["messages"][-1]
        if hasattr(last, "tool_calls") and last.tool_calls:
            return "tools"
        return "post_process"

    workflow = StateGraph(AgentState)
    workflow.add_node("model", call_model)
    workflow.add_node("tools", tool_node)
    workflow.add_node("post_process", post_process)
    workflow.set_entry_point("model")
    workflow.add_conditional_edges(
        "model",
        should_continue,
        {"tools": "tools", "post_process": "post_process"},
    )
    workflow.add_edge("tools", "model")
    workflow.add_edge("post_process", END)
    return workflow.compile()


def initial_state(session_id: str, message: str, data_source_id: str | None) -> dict:
    return {
        "messages": [HumanMessage(content=message)],
        "session_id": session_id,
        "data_source_id": data_source_id,
        "sql_query": None,
        "analysis_result": None,
        "chart_data": None,
        "chart_spec": None,
        "error": None,
    }
