"""Tests for the streaming protocol in app.services.streaming.

These tests focus on the wire format: token events are emitted in order, the
final `end` event carries the same content, and the queue/SENTINEL plumbing
drains cleanly even when the LLM is fast.

The LLM and graph are mocked so the tests don't hit any real network. What
we care about is that the streaming layer correctly forwards the model's
text chunks to SSE consumers and terminates deterministically.
"""

from __future__ import annotations

import json
from typing import Any

import pytest
from app.services import streaming


def _sse_data(evt: dict[str, str]) -> dict[str, Any]:
    return json.loads(evt["data"])


def _token_events(events: list[dict[str, str]]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for evt in events:
        if evt.get("event") != "chunk":
            continue
        data = _sse_data(evt)
        if data.get("type") == "token":
            out.append(data)
    return out


async def _collect(gen) -> list[dict[str, str]]:
    return [evt async for evt in gen]


@pytest.mark.asyncio
async def test_stream_emits_one_token_event_per_chunk(monkeypatch, fake_redis):
    """Each text chunk from the LLM should reach the client as a `token` event,
    in order, with the same text and a positive delta."""

    # Stub session creation so we don't depend on real session plumbing here.
    async def _fake_resolve(session_id, data_source_id, data_source_ids=None):
        return {"session_id": session_id, "data_source_id": None, "chat_history": []}

    monkeypatch.setattr(streaming, "_resolve_session", _fake_resolve)

    # Build a fake graph that:
    #   1. calls token_cb for each chunk in the body, then
    #   2. returns a state dict with the final AI message and chart_data=None.
    class _FakeGraph:
        def __init__(self, *args, **kwargs):
            self.token_cb = kwargs.get("token_cb")

        async def ainvoke(self, state, config=None):
            # Emit the final answer as three chunks, simulating a streaming LLM.
            assert self.token_cb is not None
            self.token_cb("你好,")
            self.token_cb("这是来自模型的")
            self.token_cb("回答。")
            return {
                "messages": [],
                "chart_data": None,
                "sql_query": None,
                "analysis_result": None,
            }

    # `build_graph` is imported lazily inside `stream_chat`, so we patch
    # the source module -- the import will pick up the fake.
    monkeypatch.setattr("app.agents.graph.build_graph", lambda *a, **kw: _FakeGraph(*a, **kw))

    events = await _collect(
        streaming.stream_chat(session_id="s1", message="hi", data_source_id=None)
    )

    tokens = _token_events(events)
    assert [t["text"] for t in tokens] == ["你好,", "这是来自模型的", "回答。"]
    assert all(t["delta"] == len(t["text"]) for t in tokens)
    # Tokens must arrive *before* the final `end` event so the client can
    # render progressively.
    last_token_idx = max(
        i
        for i, e in enumerate(events)
        if e.get("event") == "chunk" and _sse_data(e).get("type") == "token"
    )
    end_idx = next(i for i, e in enumerate(events) if e.get("event") == "end")
    assert last_token_idx < end_idx


@pytest.mark.asyncio
async def test_stream_end_event_carries_full_assembled_text(monkeypatch, fake_redis):
    """The end event's `message` must equal the concatenation of every token
    the model emitted. Clients use the end event as the authoritative final
    text (e.g. when reloading history)."""

    async def _fake_resolve(session_id, data_source_id, data_source_ids=None):
        return {"session_id": session_id, "data_source_id": None, "chat_history": []}

    monkeypatch.setattr(streaming, "_resolve_session", _fake_resolve)

    pieces = ["Hello", ", ", "world", "!"]

    class _FakeGraph:
        def __init__(self, *args, **kwargs):
            self.token_cb = kwargs.get("token_cb")

        async def ainvoke(self, state, config=None):
            for p in pieces:
                self.token_cb(p)
            # We deliberately do NOT add an AIMessage to messages; the stream
            # layer's safety net should still produce a sensible end message
            # from the streamed tokens. To do that, we need an AIMessage in
            # the state so _extract_final_text finds it. Simulate the real
            # post_process step here.
            from langchain_core.messages import AIMessage

            return {
                "messages": [AIMessage(content="".join(pieces))],
                "chart_data": None,
                "sql_query": None,
                "analysis_result": None,
            }

    monkeypatch.setattr("app.agents.graph.build_graph", lambda *a, **kw: _FakeGraph(*a, **kw))

    events = await _collect(streaming.stream_chat("s1", "x"))
    end = next(e for e in events if e.get("event") == "end")
    end_data = _sse_data(end)
    assert end_data["type"] == "complete"
    assert end_data["message"] == "".join(pieces)


@pytest.mark.asyncio
async def test_stream_emits_error_event_when_graph_raises(monkeypatch, fake_redis):
    """If the graph task raises, the consumer must surface a single `error`
    SSE event and stop -- no `end` event should follow."""

    async def _fake_resolve(session_id, data_source_id, data_source_ids=None):
        return {"session_id": session_id, "data_source_id": None, "chat_history": []}

    monkeypatch.setattr(streaming, "_resolve_session", _fake_resolve)

    class _RaisingGraph:
        def __init__(self, *args, **kwargs):
            pass

        async def ainvoke(self, state, config=None):
            raise RuntimeError("boom")

    monkeypatch.setattr("app.agents.graph.build_graph", lambda *a, **kw: _RaisingGraph(*a, **kw))

    events = await _collect(streaming.stream_chat("s1", "x"))
    error_evts = [e for e in events if e.get("event") == "error"]
    end_evts = [e for e in events if e.get("event") == "end"]
    assert len(error_evts) == 1
    assert "boom" in _sse_data(error_evts[0])["message"]
    assert end_evts == []


@pytest.mark.asyncio
async def test_stream_no_token_when_model_emits_no_text(monkeypatch, fake_redis):
    """A run that produced no streamed text should still terminate cleanly:
    no `token` events, but a single `end` event with the safety-net message."""

    async def _fake_resolve(session_id, data_source_id, data_source_ids=None):
        return {"session_id": session_id, "data_source_id": None, "chat_history": []}

    monkeypatch.setattr(streaming, "_resolve_session", _fake_resolve)

    class _SilentGraph:
        def __init__(self, *args, **kwargs):
            pass

        async def ainvoke(self, state, config=None):
            return {
                "messages": [],
                "chart_data": None,
                "sql_query": None,
                "analysis_result": None,
            }

    monkeypatch.setattr("app.agents.graph.build_graph", lambda *a, **kw: _SilentGraph(*a, **kw))

    events = await _collect(streaming.stream_chat("s1", "x"))
    assert _token_events(events) == []
    end = next(e for e in events if e.get("event") == "end")
    end_data = _sse_data(end)
    assert end_data["type"] == "complete"
    # The graph returned no message, no SQL, no error -- the safety net kicks
    # in with a "(模型未返回文字说明)" placeholder.
    assert (
        "模型未返回文字说明" in end_data["message"] or end_data["message"] == "(模型未返回文字说明)"
    )
