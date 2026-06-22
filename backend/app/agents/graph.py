from __future__ import annotations

import json
import time
from collections.abc import Callable, Sequence
from typing import Any

import pandas as pd
from langchain_core.messages import (
    AIMessage,
    AIMessageChunk,
    BaseMessage,
    HumanMessage,
    SystemMessage,
)
from langchain_openai import ChatOpenAI
from langgraph.graph import END, StateGraph
from langgraph.prebuilt import ToolNode

from ..config import settings
from ..utils.metrics import (
    LLM_CALL_DURATION_SECONDS,
    LLM_CALLS_TOTAL,
    LLM_TOKENS_USED_TOTAL,
)
from .state import AgentState
from .tools import build_tools

SYSTEM_PROMPT = """你是一个专业的数据分析助手。用户会先上传 CSV/Excel 等数据文件（可能含多 Sheet），然后用自然语言向你提问。
工作流程：
1. 第一次回答前，先调用 list_tables 了解当前会话绑定了哪些数据源、各源有哪些表、各表行数与列类型。list_tables 返回的 alias 标识了源：主源 alias="main"（表名裸写），其他附加源 alias="ds_1" / "ds_2"（表名须带前缀如 ds_1.orders）。
2. 对感兴趣的表调用 get_table_schema(table_name=...) 获取列的详细类型、用户自定义描述、单位、示例值。主源表写裸名；附加源表写 ds_N.<table>。get_sample_rows 可在不确定语义时拿几行看看。
3. 通过 query_database 执行只读 SQL 查询获取数据（最多 100 行）。多源场景可写 JOIN,主源表裸名、附加源表用 ds_N.<table>。
4. 用中文清晰回答用户问题，必要时给出关键数字与解读。涉及到单位(金额/日期等)时,使用 schema 给定的 unit。
5. 如果需要可视化，调用 create_chart 工具，把结构化数据传进去；系统会自动渲染为 ECharts 图表。
6. 遇到错误时用友好语言告诉用户可能的原因，并给出修复建议。
注意事项：
- 只能生成 SELECT / WITH 开头的查询，禁止任何写操作（INSERT/UPDATE/DELETE/DROP 等），也不要写 ATTACH / DETACH（系统在工具内部处理）。
- 列名含空格或特殊字符时用双引号包裹，例如 "Order ID"。
- 表名也是标识符，多 Sheet Excel 的表名是 sanitize 后的 sheet 名（可能与原始名不同）；以 list_tables 返回值为准。
- create_chart 工具的 chart_type 必须是 'bar'、'line'、'pie'、'scatter' 之一。
- 不要重复解释自己的工具调用过程，除非对用户有帮助。
- 每次回答必须在 content 中输出文字 —— 即使没有数据需要分析,也要用一句话告诉用户结论或提示(例如 "请先上传数据文件"、"未匹配到记录" 等)。调用工具后必须接着输出一段文字总结。"""


def _build_llm(temperature: float = 0):
    # Phase 4E: when LLM_MOCK is on, return a deterministic stub so E2E
    # tests can run without an OpenAI key. The stub implements the same
    # surface the agent graph uses (``bind_tools`` + ``astream``).
    if settings.LLM_MOCK:
        from .mock_llm import MockChatModel

        return MockChatModel()

    provider = (settings.LLM_PROVIDER or "openai").lower()
    if provider == "anthropic":
        return _build_anthropic_llm(temperature)
    return _build_openai_llm(temperature)


def _build_openai_llm(temperature: float) -> ChatOpenAI:
    # Pull the key / base URL from pydantic settings (which reads .env),
    # not from os.getenv -- pydantic-loaded values never make it into
    # the process env, so ChatOpenAI would otherwise see no credentials.
    kwargs: dict[str, Any] = {
        "model": settings.OPENAI_MODEL,
        "temperature": temperature,
        "streaming": True,
        # Phase 6: hard wall-clock timeout per call. Without this a stalled
        # provider keeps the request (and the SSE connection) open until the
        # client gives up, burning an event-loop slot.
        "timeout": settings.LLM_REQUEST_TIMEOUT_S,
        # Reasoning models (e.g. minimax-m3 on Ark) emit a long chain of
        # thinking tokens before any visible content. Without an explicit
        # cap, the server's default max_tokens can be small enough that the
        # reasoning phase eats the whole budget and content stays empty —
        # which surfaces to the user as "(empty response)". 4k is enough
        # headroom for our schema-driven SQL agent while staying well under
        # any per-call quota.
        "max_tokens": 4096,
    }
    if settings.OPENAI_API_KEY:
        kwargs["api_key"] = settings.OPENAI_API_KEY
    if settings.OPENAI_BASE_URL:
        kwargs["base_url"] = settings.OPENAI_BASE_URL
    return ChatOpenAI(**kwargs)


def _build_anthropic_llm(temperature: float):
    # Lazy import so the anthropic SDK is only required when actually
    # selected — installations that don't use Anthropic can skip
    # ``langchain-anthropic`` entirely.
    from langchain_anthropic import ChatAnthropic

    kwargs: dict[str, Any] = {
        "model": settings.ANTHROPIC_MODEL,
        "temperature": temperature,
        "streaming": True,
        "max_tokens": 4096,
        "timeout": settings.LLM_REQUEST_TIMEOUT_S,
    }
    if settings.ANTHROPIC_API_KEY:
        kwargs["api_key"] = settings.ANTHROPIC_API_KEY
    if settings.ANTHROPIC_BASE_URL:
        kwargs["base_url"] = settings.ANTHROPIC_BASE_URL
    return ChatAnthropic(**kwargs)


TokenCallback = Callable[[str], None]
"""Callback receiving each incremental text chunk from the *final* AI message.

Intermediate "thinking" tokens (before a tool call) are NOT routed here --
we only surface the answer the user actually sees. The callback is sync
because langchain's astream_chunks delivers chunks synchronously to the
consumer after the await resumes. Sink functions should be cheap.
"""


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
    data_source_ids: Sequence[str] | None = None,
    chat_history: Sequence[dict] | None = None,
    token_cb: TokenCallback | None = None,
    *,
    owner_id: str | None = None,
):
    """Build a LangGraph compiled graph for a given data source.

    `data_source_ids` lists every source the session can query (the primary
    first). When more than one is present, ``query_database`` ATTACHes the
    non-primary sources so a single SQL can JOIN across them. The LLM
    references them as ``ds_0.<table>`` (primary) and ``ds_1.<table>``,
    ``ds_2.<table>`` etc.

    `chat_history` is prepended to the running messages so the LLM sees the
    full conversation. The current turn's HumanMessage is supplied separately
    via `initial_state`.

    `token_cb` is an optional sink that receives incremental text chunks from
    the *final* AI message (the one that is NOT followed by a tool call).
    Intermediate reasoning tokens -- those streamed before the model decides
    to call a tool -- are swallowed because users would find them noisy and
    they're frequently reformulated anyway.

    Phase 4B: ``owner_id`` is forwarded to ``build_tools`` so each
    ``query_database`` invocation can stamp the user on its lineage record.
    """
    all_ids: list[str] = []
    if data_source_id:
        all_ids.append(data_source_id)
    for i in data_source_ids or []:
        if i and i not in all_ids:
            all_ids.append(i)
    tools = build_tools(all_ids, owner_id=owner_id)
    llm = _build_llm().bind_tools(tools)
    tool_node = ToolNode(tools)
    prior_messages = history_to_messages(chat_history or [])

    async def call_model(state: AgentState):
        messages = [SystemMessage(content=SYSTEM_PROMPT), *prior_messages, *state["messages"]]
        # The model may be invoked multiple times (model -> tools -> model).
        # Only the *last* invocation -- the one whose response has no tool
        # calls -- should fan tokens out to the consumer. We detect that by
        # looking at the next node: `should_continue` decides between
        # `tools` and `post_process` based on the AI message's tool_calls.
        # We replicate the same check here so the consumer can suppress
        # intermediate streaming safely.
        full: AIMessage | None = None
        provider_label = (settings.LLM_PROVIDER or "openai").lower()
        if settings.LLM_MOCK:
            provider_label = "mock"
        t0 = time.perf_counter()
        try:
            async for chunk in llm.astream(messages):
                if not isinstance(chunk, AIMessageChunk):
                    continue
                # Accumulate by summing chunks; langchain's merge semantics for
                # AIMessageChunk add tool_calls and content piecewise.
                full = chunk if full is None else full + chunk  # type: ignore[operator]
        except Exception:
            LLM_CALLS_TOTAL.labels(provider=provider_label, status="error").inc()
            raise
        elapsed = time.perf_counter() - t0
        LLM_CALL_DURATION_SECONDS.labels(provider=provider_label).observe(elapsed)
        LLM_CALLS_TOTAL.labels(provider=provider_label, status="ok").inc()
        # Token usage: langchain surfaces usage_metadata on the final
        # AIMessage when the provider reports it. Best-effort — missing
        # on some providers (Ark, etc.) where we just skip the counter.
        usage = getattr(full, "usage_metadata", None) if full else None
        if isinstance(usage, dict):
            prompt = usage.get("input_tokens") or usage.get("prompt_tokens") or 0
            completion = usage.get("output_tokens") or usage.get("completion_tokens") or 0
            if prompt:
                LLM_TOKENS_USED_TOTAL.labels(provider=provider_label, kind="prompt").inc(prompt)
            if completion:
                LLM_TOKENS_USED_TOTAL.labels(provider=provider_label, kind="completion").inc(
                    completion
                )
        assert full is not None
        # If this response will trigger another tool call, the user shouldn't
        # see the half-baked thinking -- keep it for the agent but don't emit.
        if not (getattr(full, "tool_calls", None) and full.tool_calls):
            content = full.content
            text = (
                content
                if isinstance(content, str)
                else "".join(
                    block.get("text", "") if isinstance(block, dict) else str(block)
                    for block in (content or [])
                )
            )
            if text and token_cb is not None:
                token_cb(text)
        return {"messages": [full]}

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
