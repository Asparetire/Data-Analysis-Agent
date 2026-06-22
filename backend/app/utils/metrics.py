"""Phase 6: business-level Prometheus metrics.

The default Instrumentator only exposes HTTP-level counters (latency /
request volume / in-progress). The metrics here cover what operators
actually want to alert on:

  - ``llm_calls_total{provider, status}`` — counter of LLM invocations by
    provider (openai / anthropic / mock) and status (ok / error / timeout).
  - ``llm_call_duration_seconds`` — histogram of wall-clock time per call.
  - ``llm_tokens_used_total{provider, kind}`` — counter of tokens
    (prompt / completion) if the provider reports them.
  - ``query_cache_hits_total`` / ``query_cache_misses_total`` — cache
    effectiveness so we can tune the TTL.
  - ``sse_active_streams`` — gauge of in-flight /chat/stream connections.
  - ``sse_rejected_total`` — counter of over-cap 429s.

All metrics are no-ops if prometheus_client isn't installed (it is —
prometheus-fastapi-instrumentator depends on it).
"""

from __future__ import annotations

from prometheus_client import Counter, Gauge, Histogram

LLM_CALLS_TOTAL = Counter(
    "llm_calls_total",
    "LLM invocations by provider and outcome.",
    ["provider", "status"],
)

LLM_CALL_DURATION_SECONDS = Histogram(
    "llm_call_duration_seconds",
    "Wall-clock duration of a single LLM call.",
    ["provider"],
    buckets=(0.1, 0.5, 1.0, 2.5, 5.0, 10.0, 30.0, 60.0, 120.0),
)

LLM_TOKENS_USED_TOTAL = Counter(
    "llm_tokens_used_total",
    "Tokens reported by the provider, labelled by kind.",
    ["provider", "kind"],  # kind: prompt | completion
)

QUERY_CACHE_HITS_TOTAL = Counter(
    "query_cache_hits_total",
    "query_database cache hits (skipped SQLite round-trip).",
)

QUERY_CACHE_MISSES_TOTAL = Counter(
    "query_cache_misses_total",
    "query_database cache misses (ran the SQL against SQLite).",
)

SSE_ACTIVE_STREAMS = Gauge(
    "sse_active_streams",
    "In-flight /chat/stream SSE connections (across all users).",
)

SSE_REJECTED_TOTAL = Counter(
    "sse_rejected_total",
    "/chat/stream requests refused because the per-user concurrency cap was hit.",
)
