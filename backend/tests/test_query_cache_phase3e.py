"""Tests for the in-process query cache (Phase 3E)."""

from __future__ import annotations

import time

import pytest
from app.services import query_cache
from app.services.query_cache import QueryCache


@pytest.fixture
def fresh_cache(monkeypatch):
    """Replace the singleton so each test starts cold."""
    cache = QueryCache(ttl_seconds=0.2, max_entries=4)
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
    """The binding set is treated as a set, not a list."""
    a = QueryCache.make_key("SELECT 1", ["a", "b"])
    b = QueryCache.make_key("SELECT 1", ["b", "a"])
    assert a == b


def test_get_returns_none_on_miss(fresh_cache):
    assert fresh_cache.get("nope") is None


def test_set_then_get(fresh_cache):
    fresh_cache.set("k1", {"columns": ["a"], "rows": [1], "row_count": 1})
    got = fresh_cache.get("k1")
    assert got == {"columns": ["a"], "rows": [1], "row_count": 1}


def test_get_expires_after_ttl(fresh_cache):
    fresh_cache.set("k", {"a": 1})
    # ttl is 0.2s in fresh_cache
    time.sleep(0.25)
    assert fresh_cache.get("k") is None


def test_lru_eviction_drops_oldest(fresh_cache):
    # max_entries=4
    for i in range(4):
        fresh_cache.set(f"k{i}", {"v": i})
    # Touch k0 so it becomes MRU; k1 is now LRU.
    fresh_cache.get("k0")
    fresh_cache.set("k4", {"v": 4})
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


def test_set_overwrite_bumps_to_mru(fresh_cache):
    fresh_cache.set("k0", {"v": 0})
    for i in range(1, 4):
        fresh_cache.set(f"k{i}", {"v": i})
    # Re-set k0 so it becomes MRU; k1 is now LRU.
    fresh_cache.set("k0", {"v": 0})
    fresh_cache.set("k4", {"v": 4})
    assert fresh_cache.get("k1") is None
    assert fresh_cache.get("k0") is not None


def test_stats(fresh_cache):
    s = fresh_cache.stats()
    assert s["max"] == 4
    assert s["ttl_seconds"] == 0.2
    assert s["size"] == 0
    fresh_cache.set("k", {"a": 1})
    assert fresh_cache.stats()["size"] == 1


def test_clear(fresh_cache):
    fresh_cache.set("k", {"a": 1})
    fresh_cache.clear()
    assert fresh_cache.get("k") is None
