"""TTL + LRU cache for query_database results, plus a no-op fall back.

The agent's ``query_database`` tool talks to SQLite on every turn. Most
turns re-issue queries that are very similar to the last run (refinement
loops, "show me the top 10 by X" → "now group by Y" → "order desc") and
SQLite's prepared-statement cache won't help us across separate ``execute``
calls. We add a small in-process cache keyed by (sql, binding ids) with a
60s TTL and an LRU cap of 256 entries.

Scope: in-process only. The agent runs single-process so we don't need a
distributed cache. If the process restarts the cache is cold, which is
fine — lineage records persist in the sidecar either way.

Why (sql, binding ids) and not just sql: a multi-source session can issue
the same SQL with different ATTACH'd sources and the result *will* differ.
Including the sorted binding list in the key avoids stale cross-binding
hits.
"""

from __future__ import annotations

import hashlib
import json
import threading
import time
from collections import OrderedDict
from collections.abc import Iterable

logger = __import__("logging").getLogger(__name__)


DEFAULT_TTL_SECONDS = 60.0
DEFAULT_MAX_ENTRIES = 256


class QueryCache:
    """Bounded TTL + LRU cache for query results.

    The cache stores the raw ``query_database`` output dict
    ``{columns, rows, row_count}``. The key is a sha1 of (sql, sorted
    binding ids). When the cache is full we evict the least-recently-used
    entry; on a hit we reinsert to bump the entry to the front.
    """

    def __init__(
        self, ttl_seconds: float = DEFAULT_TTL_SECONDS, max_entries: int = DEFAULT_MAX_ENTRIES
    ):
        self._ttl = float(ttl_seconds)
        self._max = int(max_entries)
        self._lock = threading.Lock()
        # value: (expires_at, payload_dict)
        self._entries: OrderedDict[str, tuple[float, dict]] = OrderedDict()

    @staticmethod
    def make_key(sql: str, binding_ids: Iterable[str] | None) -> str:
        """Stable key: sha1 of (sql, sorted binding ids).

        ``sql`` is normalized by stripping trailing whitespace and the
        trailing semicolon so cosmetic re-runs hit the same slot.
        """
        norm = (sql or "").strip().rstrip(";").strip()
        ids = sorted(binding_ids or [])
        payload = json.dumps({"sql": norm, "ids": ids}, ensure_ascii=False, sort_keys=True)
        return hashlib.sha1(payload.encode("utf-8")).hexdigest()

    def get(self, key: str) -> dict | None:
        with self._lock:
            entry = self._entries.get(key)
            if entry is None:
                return None
            expires_at, payload = entry
            if expires_at < time.monotonic():
                # Expired: drop and miss.
                del self._entries[key]
                return None
            # Hit: re-insert to mark as MRU.
            self._entries.move_to_end(key)
            return payload

    def set(self, key: str, payload: dict) -> None:
        with self._lock:
            # If the key already exists, drop the old value so move_to_end
            # bumps it to the back (MRU).
            if key in self._entries:
                del self._entries[key]
            self._entries[key] = (time.monotonic() + self._ttl, payload)
            self._entries.move_to_end(key)
            while len(self._entries) > self._max:
                self._entries.popitem(last=False)

    def invalidate_containing(self, data_source_id: str) -> int:
        """Drop every entry whose binding list includes ``data_source_id``.

        We can't recover the binding list from a sha1, so we walk the
        values. Values include a small header ``_binding_ids`` injected by
        ``set_with_bindings`` so this is O(N) and cheap.
        """
        with self._lock:
            to_drop = []
            for key, (_, payload) in self._entries.items():
                ids = payload.get("_binding_ids") or []
                if data_source_id in ids:
                    to_drop.append(key)
            for key in to_drop:
                del self._entries[key]
            return len(to_drop)

    def clear(self) -> None:
        with self._lock:
            self._entries.clear()

    def stats(self) -> dict:
        with self._lock:
            return {"size": len(self._entries), "max": self._max, "ttl_seconds": self._ttl}

    def set_with_bindings(self, key: str, payload: dict, binding_ids: Iterable[str]) -> None:
        """Like ``set`` but stamps the binding list on the payload so
        ``invalidate_containing`` can find it later.
        """
        stamped = dict(payload)
        stamped["_binding_ids"] = list(binding_ids or [])
        self.set(key, stamped)


# Process-wide singleton. Tests can swap this out via ``set_singleton``.
_singleton: QueryCache | None = None
_singleton_lock = threading.Lock()


def get_cache() -> QueryCache:
    global _singleton
    with _singleton_lock:
        if _singleton is None:
            _singleton = QueryCache()
        return _singleton


def set_cache(cache: QueryCache | None) -> None:
    """Replace the singleton (used by tests). Pass None to reset to default."""
    global _singleton
    with _singleton_lock:
        _singleton = cache
