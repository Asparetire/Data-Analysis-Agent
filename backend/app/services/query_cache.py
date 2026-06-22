"""TTL cache for query_database results, backed by Redis (sync client).

Phase 6: moved from an in-process dict to Redis so the cache is shared
across workers (the previous implementation only benefited the worker
that happened to serve the first request). The trade-off is a Redis
round-trip on every lookup, but query_database is already going to
hit SQLite on a miss, so the relative cost is small.

The interface is synchronous because ``query_database`` is a sync tool
(run inside a LangGraph ToolNode that can dispatch either sync or async
tools — keeping this one sync avoids wrapping every SQLite call in
``asyncio.to_thread``). A sync ``redis.Redis`` client is used here; the
async client (``redis.asyncio``) is what ``session_service`` uses.

Key shape: ``query_cache:{sha1(sql, sorted binding ids)}``.
Value: JSON-serialized ``{columns, rows, row_count, _binding_ids}``.
TTL: 60s (DEFAULT_TTL_SECONDS).

Skip-cache threshold: payloads larger than ``MAX_PAYLOAD_BYTES`` (default
256 KB) are not cached — Redis isn't a bulk store, and a single 100-row
result with wide rows can easily hit that.

An ``InProcessQueryCache`` fallback is kept for tests that don't want
Redis — see ``set_cache`` / ``get_cache``.
"""

from __future__ import annotations

import contextlib
import hashlib
import json
import threading
import time
from collections import OrderedDict
from collections.abc import Iterable

import redis as sync_redis

from ..config import settings
from ..utils.logger import get_logger

logger = get_logger(__name__)

DEFAULT_TTL_SECONDS = 60.0
DEFAULT_MAX_ENTRIES = 256  # only used by the in-process fallback
MAX_PAYLOAD_BYTES = 256 * 1024


def _sync_client() -> sync_redis.Redis:
    """Return a sync Redis client configured the same as the async one."""
    return sync_redis.Redis.from_url(
        settings.REDIS_URL,
        decode_responses=True,
        socket_connect_timeout=2,
        socket_timeout=2,
    )


class QueryCache:
    """Redis-backed cache. Falls back to a miss when Redis is unavailable."""

    def __init__(self, ttl_seconds: float = DEFAULT_TTL_SECONDS):
        self._ttl = float(ttl_seconds)

    @staticmethod
    def make_key(sql: str, binding_ids: Iterable[str] | None) -> str:
        norm = (sql or "").strip().rstrip(";").strip()
        ids = sorted(binding_ids or [])
        payload = json.dumps({"sql": norm, "ids": ids}, ensure_ascii=False, sort_keys=True)
        digest = hashlib.sha1(payload.encode("utf-8")).hexdigest()
        return f"query_cache:{digest}"

    def get(self, key: str) -> dict | None:
        from ..utils.metrics import QUERY_CACHE_HITS_TOTAL, QUERY_CACHE_MISSES_TOTAL

        try:
            client = _sync_client()
            raw = client.get(key)
        except Exception:
            logger.exception("redis get failed; cache miss")
            QUERY_CACHE_MISSES_TOTAL.inc()
            return None
        if raw is None:
            QUERY_CACHE_MISSES_TOTAL.inc()
            return None
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError:
            logger.warning("malformed cache payload at %s; dropping", key)
            with contextlib.suppress(Exception):
                client.delete(key)
            QUERY_CACHE_MISSES_TOTAL.inc()
            return None
        QUERY_CACHE_HITS_TOTAL.inc()
        return payload

    def set_with_bindings(self, key: str, payload: dict, binding_ids: Iterable[str]) -> None:
        stamped = dict(payload)
        stamped["_binding_ids"] = list(binding_ids or [])
        try:
            encoded = json.dumps(stamped, ensure_ascii=False).encode("utf-8")
        except (TypeError, ValueError) as e:
            logger.warning("failed to serialize cache payload: %s", e)
            return
        if len(encoded) > MAX_PAYLOAD_BYTES:
            return
        try:
            _sync_client().setex(key, int(self._ttl), encoded.decode("utf-8"))
        except Exception:
            logger.exception("redis set failed; cache write skipped")

    def invalidate_containing(self, data_source_id: str) -> int:
        """Drop every cache entry whose binding list includes ``data_source_id``.

        Used when a data source is deleted so future queries don't return
        stale rows from a now-dropped table. O(N) over cache keys; deletes
        are rare so this is fine.
        """
        try:
            client = _sync_client()
            dropped = 0
            for key in client.scan_iter(match="query_cache:*"):
                raw = client.get(key)
                if not raw:
                    continue
                try:
                    payload = json.loads(raw)
                except json.JSONDecodeError:
                    continue
                if data_source_id in (payload.get("_binding_ids") or []):
                    client.delete(key)
                    dropped += 1
            return dropped
        except Exception:
            logger.exception("invalidate_containing failed")
            return 0

    def clear(self) -> None:
        try:
            client = _sync_client()
            for key in client.scan_iter(match="query_cache:*"):
                client.delete(key)
        except Exception:
            logger.exception("clear failed")

    def stats(self) -> dict:
        return {"ttl_seconds": self._ttl, "max_payload_bytes": MAX_PAYLOAD_BYTES}


class InProcessQueryCache:
    """Fallback for tests that don't want Redis. Same surface, sync."""

    def __init__(
        self, ttl_seconds: float = DEFAULT_TTL_SECONDS, max_entries: int = DEFAULT_MAX_ENTRIES
    ):
        self._ttl = float(ttl_seconds)
        self._max = int(max_entries)
        self._lock = threading.Lock()
        self._entries: OrderedDict[str, tuple[float, dict]] = OrderedDict()

    @staticmethod
    def make_key(sql: str, binding_ids: Iterable[str] | None) -> str:
        return QueryCache.make_key(sql, binding_ids)

    def get(self, key: str) -> dict | None:
        with self._lock:
            entry = self._entries.get(key)
            if entry is None:
                return None
            expires_at, payload = entry
            if expires_at < time.monotonic():
                del self._entries[key]
                return None
            self._entries.move_to_end(key)
            return payload

    def set_with_bindings(self, key: str, payload: dict, binding_ids: Iterable[str]) -> None:
        with self._lock:
            stamped = dict(payload)
            stamped["_binding_ids"] = list(binding_ids or [])
            if key in self._entries:
                del self._entries[key]
            self._entries[key] = (time.monotonic() + self._ttl, stamped)
            self._entries.move_to_end(key)
            while len(self._entries) > self._max:
                self._entries.popitem(last=False)

    def invalidate_containing(self, data_source_id: str) -> int:
        with self._lock:
            to_drop = [
                k
                for k, (_, payload) in self._entries.items()
                if data_source_id in (payload.get("_binding_ids") or [])
            ]
            for k in to_drop:
                del self._entries[k]
            return len(to_drop)

    def clear(self) -> None:
        with self._lock:
            self._entries.clear()

    def stats(self) -> dict:
        with self._lock:
            return {
                "size": len(self._entries),
                "max": self._max,
                "ttl_seconds": self._ttl,
            }


_singleton: QueryCache | InProcessQueryCache | None = None
_singleton_lock = threading.Lock()


def get_cache() -> QueryCache | InProcessQueryCache:
    global _singleton
    with _singleton_lock:
        if _singleton is None:
            _singleton = QueryCache()
        return _singleton


def set_cache(cache: QueryCache | InProcessQueryCache | None) -> None:
    """Replace the singleton (used by tests). Pass None to reset to default."""
    global _singleton
    with _singleton_lock:
        _singleton = cache
