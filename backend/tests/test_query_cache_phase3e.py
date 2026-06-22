"""Tests for the query cache.

Phase 6: the production cache is Redis-backed (``QueryCache``); the
in-process fallback (``InProcessQueryCache``) preserves the old surface
for tests that don't want Redis. These tests exercise the fallback
because it has the TTL + LRU semantics worth testing — the Redis-backed
class delegates the same logic to Redis primitives.
"""

from __future__ import annotations

import time

import pytest
from app.services import query_cache
from app.services.query_cache import InProcessQueryCache, QueryCache


@pytest.fixture
def fresh_cache(monkeypatch):
    """Replace the singleton so each test starts cold."""
    cache = InProcessQueryCache(ttl_seconds=0.2, max_entries=4)
    query_cache.set_cache(cache)
    yield cache
    query_cache.set_cache(None)


def test_make_key_strips_trailing_semicolon_and_whitespace():
    a = QueryCache.make_key("SELECT * FROM t", ["ds1"])
    b = QueryCache.make_key("  SELECT * FROM t;  ", ["ds1"])
    assert a == b


def test_make_key_differs_by_binding_set():
    a = QueryCache.make_key("SELECT * FROM t", ["ds1"])
    b = QueryCache.make_key("SELECT * FROM t", ["ds1", "ds2"])
    assert a != b


def test_make_key_sorts_binding_ids():
    a = QueryCache.make_key("SELECT 1", ["a", "b"])
    b = QueryCache.make_key("SELECT 1", ["b", "a"])
    assert a == b


def test_make_key_prefixed_with_query_cache_namespace():
    """Phase 6: Redis-backed key is namespaced so SCAN can find them."""
    k = QueryCache.make_key("SELECT 1", ["ds1"])
    assert k.startswith("query_cache:")


def test_get_returns_none_on_miss(fresh_cache):
    assert fresh_cache.get("nope") is None


def test_set_with_bindings_then_get(fresh_cache):
    fresh_cache.set_with_bindings("k1", {"columns": ["a"], "rows": [1], "row_count": 1}, ["ds1"])
    got = fresh_cache.get("k1")
    assert got is not None
    assert got["columns"] == ["a"]
    assert got["_binding_ids"] == ["ds1"]


def test_get_expires_after_ttl(fresh_cache):
    fresh_cache.set_with_bindings("k", {"a": 1}, [])
    time.sleep(0.25)
    assert fresh_cache.get("k") is None


def test_lru_eviction_drops_oldest(fresh_cache):
    for i in range(4):
        fresh_cache.set_with_bindings(f"k{i}", {"v": i}, [])
    # Touch k0 so it becomes MRU; k1 is now LRU.
    fresh_cache.get("k0")
    fresh_cache.set_with_bindings("k4", {"v": 4}, [])
    assert fresh_cache.get("k1") is None  # evicted
    assert fresh_cache.get("k0") is not None  # still here
    assert fresh_cache.get("k4") is not None


def test_invalidate_containing(fresh_cache):
    fresh_cache.set_with_bindings("k1", {"a": 1}, ["ds1"])
    fresh_cache.set_with_bindings("k2", {"a": 2}, ["ds2"])
    fresh_cache.set_with_bindings("k3", {"a": 3}, ["ds1", "ds2"])
    dropped = fresh_cache.invalidate_containing("ds1")
    assert dropped == 2
    assert fresh_cache.get("k1") is None
    assert fresh_cache.get("k3") is None
    assert fresh_cache.get("k2") is not None


def test_set_with_bindings_stamps_ids(fresh_cache):
    fresh_cache.set_with_bindings("k", {"x": 1}, ["ds1", "ds2"])
    got = fresh_cache.get("k")
    assert got is not None
    assert got.get("_binding_ids") == ["ds1", "ds2"]


def test_set_with_bindings_overwrite_bumps_to_mru(fresh_cache):
    fresh_cache.set_with_bindings("k0", {"v": 0}, [])
    for i in range(1, 4):
        fresh_cache.set_with_bindings(f"k{i}", {"v": i}, [])
    # Re-set k0 so it becomes MRU; k1 is now LRU.
    fresh_cache.set_with_bindings("k0", {"v": 0}, [])
    fresh_cache.set_with_bindings("k4", {"v": 4}, [])
    assert fresh_cache.get("k1") is None
    assert fresh_cache.get("k0") is not None


def test_stats(fresh_cache):
    s = fresh_cache.stats()
    assert s["max"] == 4
    assert s["ttl_seconds"] == 0.2
    assert s["size"] == 0
    fresh_cache.set_with_bindings("k", {"a": 1}, [])
    assert fresh_cache.stats()["size"] == 1


def test_clear(fresh_cache):
    fresh_cache.set_with_bindings("k", {"a": 1}, [])
    fresh_cache.clear()
    assert fresh_cache.get("k") is None


def test_redis_cache_skips_oversize_payload(monkeypatch):
    """Phase 6: payloads > MAX_PAYLOAD_BYTES are not written to Redis."""
    # Avoid touching real Redis — we just want to confirm the size guard fires.
    cache = QueryCache(ttl_seconds=60)
    calls: list[tuple] = []

    class _FakeClient:
        def setex(self, key, ttl, value):
            calls.append((key, ttl, value))

    monkeypatch.setattr(query_cache, "_sync_client", lambda: _FakeClient())
    big_payload = {"rows": ["x" * 1024] * 1024}  # ~1MB
    cache.set_with_bindings("query_cache:big", big_payload, ["ds1"])
    assert calls == []  # skipped because > MAX_PAYLOAD_BYTES
