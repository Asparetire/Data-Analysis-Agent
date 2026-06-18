"""Phase 4E: mock LLM used by Playwright E2E tests.

Validates that ``MockChatModel`` plugs into ``build_graph`` the same way
``ChatOpenAI`` does, and that a chat run with ``LLM_MOCK=1`` produces a
predictable streamed answer without touching the network.

These tests don't go through the FastAPI layer; they exercise the agent
graph directly with the mock swapped in via the same env flag the E2E
stack uses.
"""

from __future__ import annotations

import pandas as pd
import pytest
from app.agents import graph
from app.agents.mock_llm import MockChatModel
from app.config import settings
from app.services import data_service
from langchain_core.messages import AIMessage, HumanMessage


@pytest.mark.asyncio
async def test_mock_llm_streams_three_chunks(monkeypatch):
    """The mock yields exactly its canned chunks as ``AIMessageChunk``s."""
    monkeypatch.setattr(settings, "LLM_MOCK", True)
    llm = graph._build_llm()
    assert isinstance(llm, MockChatModel)

    chunks = []
    async for chunk in llm.astream([HumanMessage(content="hi")]):
        chunks.append(chunk)
    assert len(chunks) == 3
    assembled = "".join(c.content for c in chunks if isinstance(c.content, str))
    assert assembled == "这是 mock 模型的回复。数据看起来没问题。"


@pytest.mark.asyncio
async def test_build_graph_with_mock_llm_produces_canned_answer(
    monkeypatch, tmp_data_dir, make_upload, fake_redis
):
    """End-to-end (graph-level) smoke: with ``LLM_MOCK=1`` the agent returns
    the mock's canned text and never reaches for an OpenAI credential."""
    monkeypatch.setattr(settings, "LLM_MOCK", True)

    # Seed a tiny data source so build_tools has something to bind to. We
    # don't actually exercise any tool calls (the mock emits none), but the
    # graph still needs a real binding to construct.
    df = pd.DataFrame([{"a": 1, "b": "x"}, {"a": 2, "b": "y"}])
    upload = make_upload("d.csv", df.to_csv(index=False).encode("utf-8"))
    await data_service.save_uploaded_file(upload, "ds-mock")

    compiled = graph.build_graph(data_source_id="ds-mock", chat_history=[])
    state = graph.initial_state("s1", "总结一下", "ds-mock")
    result = await compiled.ainvoke(state)
    msgs = result.get("messages") or []
    assert msgs, "graph should return at least one message"
    final = msgs[-1]
    assert isinstance(final, AIMessage)
    text = (
        final.content
        if isinstance(final.content, str)
        else "".join(
            block.get("text", "") if isinstance(block, dict) else str(block)
            for block in (final.content or [])
        )
    )
    assert "mock" in text
    # No tool_calls should have been emitted by the mock.
    assert not getattr(final, "tool_calls", None)


def test_mock_llm_bind_tools_returns_self():
    """``bind_tools`` returns the same instance so the agent's
    ``llm.bind_tools(tools).astream(...)`` call chain stays intact."""
    mock = MockChatModel()
    assert mock.bind_tools([]) is mock
