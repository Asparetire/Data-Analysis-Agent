"""Deterministic mock LLM for E2E tests.

When ``settings.LLM_MOCK`` is true, ``_build_llm`` returns this instead of
``ChatOpenAI``. The mock streams a fixed answer in three chunks so the SSE
pipeline (token events + assembled end event) is exercised the same way a
real model would exercise it. No tool calls are emitted -- the E2E tests
assert the chat surface, not the agent's tool-routing logic, which is
already covered by the pytest suite.

Shape parity with ``ChatOpenAI``:
  - ``bind_tools(tools)`` returns ``self`` (tools are accepted but ignored).
  - ``astream(messages)`` is an async generator yielding ``AIMessageChunk``.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Sequence
from typing import Any

from langchain_core.messages import AIMessageChunk, BaseMessage

# The canned answer is split into three chunks so the streaming layer has
# something to interleave. Tests assert on the assembled text, not the chunk
# boundaries.
_CHUNKS: tuple[str, ...] = ("这是 mock 模型的", "回复。", "数据看起来没问题。")


class MockChatModel:
    """Stand-in for ``ChatOpenAI`` used when ``LLM_MOCK=1``.

    Implements the two methods the agent graph calls (``bind_tools`` +
    ``astream``); everything else is intentionally absent so we don't paper
    over a future API change.
    """

    def __init__(self, *, chunks: Sequence[str] | None = None) -> None:
        self._chunks: tuple[str, ...] = tuple(chunks) if chunks is not None else _CHUNKS

    def bind_tools(self, tools: list[Any], **_: Any) -> MockChatModel:
        # The bound-tools variant is just self -- the mock never emits tool
        # calls. Returning self keeps the call site identical to the real
        # ChatOpenAI path: ``llm.bind_tools(tools).astream(messages)``.
        return self

    async def astream(
        self, messages: Sequence[BaseMessage], **_: Any
    ) -> AsyncIterator[AIMessageChunk]:
        # `messages` is accepted but ignored; the mock's job is to be
        # deterministic, not context-aware.
        del messages
        for piece in self._chunks:
            yield AIMessageChunk(content=piece)


__all__ = ["MockChatModel"]
