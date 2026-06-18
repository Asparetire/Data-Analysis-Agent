"""Phase 4B: Redis sliding window rate limiter.

The window is fixed at 60s; each request adds a ZSET member keyed by a
unique timestamp and prunes entries older than the window. The ZSET size
after the prune is the current window's request count.

This stays simple on purpose: per-user + per-IP keys, no burst tokens, no
hierarchical limits. If we need those later we can swap implementations
without touching call sites (the public surface is one async function).
"""

from __future__ import annotations

import time
import uuid

WINDOW_S = 60  # 1 minute sliding window


async def check_rate_limit(
    redis,
    key: str,
    max_count: int,
    window_s: int = WINDOW_S,
) -> tuple[bool, int]:
    """Apply the limiter. Returns (allowed, current_count_after_insert).

    On any Redis error we fail open (allowed=True) so a Redis blip doesn't
    lock the entire API — better to lose the limit briefly than break the
    app. The error is logged by the caller.
    """
    now = time.time()
    # Each request gets a unique member so concurrent requests in the same
    # millisecond don't collapse into a single ZSET entry.
    member = f"{now:.6f}:{uuid.uuid4().hex[:8]}"
    try:
        pipe = redis.pipeline()
        pipe.zremrangebyscore(key, 0, now - window_s)
        pipe.zadd(key, {member: now})
        pipe.zcard(key)
        pipe.expire(key, window_s + 5)  # small slack so the key survives pruning
        _, _, count, _ = await pipe.execute()
    except Exception:
        # Fail open: log + allow.
        return True, 0
    return count <= max_count, count
