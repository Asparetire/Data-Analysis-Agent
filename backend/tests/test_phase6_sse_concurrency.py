"""Phase 6: SSE concurrency cap + trusted-proxy XFF handling tests.

Covers the streaming._acquire_sse_slot / _release_sse_slot pair (the
per-user concurrent-stream cap) and middleware._peer_is_trusted +
_client_ip behavior.
"""

from __future__ import annotations

import pytest
from app.api import middleware
from app.services import streaming

# ---------------------------------------------------------------------------
# SSE concurrency slot
# ---------------------------------------------------------------------------


async def test_acquire_sse_slot_first_call_succeeds(fake_redis):
    ok = await streaming._acquire_sse_slot("user-1")
    assert ok is True
    # The counter should be 1 after the acquire.
    count = await fake_redis.get(f"{streaming._SSE_COUNTER_KEY_PREFIX}user-1")
    assert int(count) == 1


async def test_acquire_sse_slot_blocks_over_cap(fake_redis, monkeypatch):
    monkeypatch.setattr("app.config.settings.MAX_CONCURRENT_SSE_PER_USER", 2)
    assert await streaming._acquire_sse_slot("user-1") is True
    assert await streaming._acquire_sse_slot("user-1") is True
    # Third concurrent stream — should be refused, and the counter rolled back
    # to 2 (not 3).
    ok = await streaming._acquire_sse_slot("user-1")
    assert ok is False
    count = await fake_redis.get(f"{streaming._SSE_COUNTER_KEY_PREFIX}user-1")
    assert int(count) == 2


async def test_release_sse_slot_decrements(fake_redis):
    await streaming._acquire_sse_slot("user-1")
    await streaming._acquire_sse_slot("user-1")
    await streaming._release_sse_slot("user-1")
    count = await fake_redis.get(f"{streaming._SSE_COUNTER_KEY_PREFIX}user-1")
    assert int(count) == 1


async def test_release_never_drives_counter_negative(fake_redis):
    # DECR with no prior INCR would leave the counter at -1; the release
    # helper should clamp to 0 so a later INCR (-> 1) doesn't bypass the cap.
    await streaming._release_sse_slot("user-1")
    count = await fake_redis.get(f"{streaming._SSE_COUNTER_KEY_PREFIX}user-1")
    assert int(count) == 0


async def test_acquire_with_none_user_passes_through(fake_redis):
    # Unauth path (shouldn't happen for /chat/stream, but the helper must
    # not blow up) — returns True without touching Redis.
    ok = await streaming._acquire_sse_slot(None)
    assert ok is True
    keys = await fake_redis.keys(f"{streaming._SSE_COUNTER_KEY_PREFIX}*")
    assert keys == []


async def test_release_with_none_user_is_noop(fake_redis):
    await streaming._release_sse_slot(None)
    keys = await fake_redis.keys(f"{streaming._SSE_COUNTER_KEY_PREFIX}*")
    assert keys == []


# ---------------------------------------------------------------------------
# Trusted-proxy XFF handling
# ---------------------------------------------------------------------------


class _FakeRequest:
    def __init__(self, headers: dict[str, str], client_host: str | None):
        self.headers = headers
        self.client = type("C", (), {"host": client_host})() if client_host else None


def test_peer_is_trusted_loopback_by_default():
    # Default TRUSTED_PROXIES is "127.0.0.1,::1" (set in conftest / config).
    assert middleware._peer_is_trusted("127.0.0.1") is True
    assert middleware._peer_is_trusted("::1") is True


def test_peer_is_trusted_rejects_unlisted_ip():
    assert middleware._peer_is_trusted("203.0.113.1") is False
    assert middleware._peer_is_trusted(None) is False


def test_client_ip_honors_xff_when_peer_trusted(monkeypatch):
    monkeypatch.setattr("app.api.middleware._peer_is_trusted", lambda _p: True)
    req = _FakeRequest({"x-forwarded-for": "203.0.113.5, 10.0.0.1"}, "127.0.0.1")
    assert middleware._client_ip(req) == "203.0.113.5"


def test_client_ip_ignores_xff_when_peer_not_trusted(monkeypatch):
    # Direct connection from a random IP — XFF must be ignored so the
    # caller can't self-identify as a whitelisted IP to bypass rate limit.
    monkeypatch.setattr("app.api.middleware._peer_is_trusted", lambda _p: False)
    req = _FakeRequest({"x-forwarded-for": "203.0.113.5"}, "198.51.100.7")
    assert middleware._client_ip(req) == "198.51.100.7"


def test_client_ip_falls_back_to_peer_when_no_xff(monkeypatch):
    monkeypatch.setattr("app.api.middleware._peer_is_trusted", lambda _p: True)
    req = _FakeRequest({}, "127.0.0.1")
    assert middleware._client_ip(req) == "127.0.0.1"


# ---------------------------------------------------------------------------
# Password complexity (Phase 6)
# ---------------------------------------------------------------------------


def test_register_rejects_letters_only_password(users_db):
    from app.services import auth_service

    with pytest.raises(auth_service.InvalidCredentials):
        auth_service.register("letters@example.com", "abcdefgh")


def test_register_rejects_digits_only_password(users_db):
    from app.services import auth_service

    with pytest.raises(auth_service.InvalidCredentials):
        auth_service.register("digits@example.com", "12345678")


def test_register_accepts_mixed_password(users_db):
    from app.services import auth_service

    user = auth_service.register("mixed@example.com", "abcd1234")
    assert user["email"] == "mixed@example.com"
