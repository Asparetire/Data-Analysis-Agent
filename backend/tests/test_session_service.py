"""Tests for session_service against a fakeredis backend."""

from __future__ import annotations

from app.services import session_service


async def test_create_and_get_session_round_trip(fake_redis):
    sid = await session_service.create_session()
    assert sid
    s = await session_service.get_session(sid)
    assert s is not None
    assert s["session_id"] == sid
    assert s["data_source_id"] is None
    assert s["chat_history"] == []


async def test_get_missing_session_returns_none(fake_redis):
    assert await session_service.get_session("does-not-exist") is None


async def test_bind_data_source_is_one_way(fake_redis):
    sid = await session_service.create_session()
    out = await session_service.bind_data_source(sid, "ds-1")
    assert out is not None
    assert out["data_source_id"] == "ds-1"

    out2 = await session_service.bind_data_source(sid, "ds-2")
    assert out2 is not None
    assert out2["data_source_id"] == "ds-1"


async def test_update_session_merges_allowed_fields(fake_redis):
    sid = await session_service.create_session()
    out = await session_service.update_session(
        sid,
        {
            "data_source_id": "ds-1",
            "last_query": "SELECT 1",
            "forbidden_field": "should be dropped",
        },
    )
    assert out is not None
    assert out["data_source_id"] == "ds-1"
    assert out["last_query"] == "SELECT 1"
    # _UPDATABLE_FIELDS is enforced; junk keys are silently dropped.
    assert "forbidden_field" not in out


async def test_append_chat_extends_history_and_caps_it(fake_redis):
    from app.services.session_service import MAX_CHAT_HISTORY

    sid = await session_service.create_session()
    for i in range(MAX_CHAT_HISTORY + 5):
        await session_service.append_chat(sid, "user", f"msg {i}")

    s = await session_service.get_session(sid)
    assert s is not None
    assert len(s["chat_history"]) == MAX_CHAT_HISTORY
    # Oldest entries dropped.
    assert s["chat_history"][0]["content"] == "msg 5"
    assert s["chat_history"][-1]["content"] == f"msg {MAX_CHAT_HISTORY + 4}"


async def test_delete_sessions_by_data_source_only_removes_matches(fake_redis):
    a = await session_service.create_session()
    b = await session_service.create_session()
    c = await session_service.create_session()
    await session_service.bind_data_source(a, "ds-x")
    await session_service.bind_data_source(b, "ds-x")
    await session_service.bind_data_source(c, "ds-y")

    removed = await session_service.delete_sessions_by_data_source("ds-x")
    assert removed == 2

    assert await session_service.get_session(a) is None
    assert await session_service.get_session(b) is None
    s = await session_service.get_session(c)
    assert s is not None and s["data_source_id"] == "ds-y"


async def test_delete_sessions_by_data_source_empty_match(fake_redis):
    await session_service.create_session()
    assert await session_service.delete_sessions_by_data_source("nope") == 0


async def test_delete_session(fake_redis):
    sid = await session_service.create_session()
    assert await session_service.delete_session(sid) is True
    assert await session_service.get_session(sid) is None
    # Idempotent.
    assert await session_service.delete_session(sid) is False


async def test_set_intermediate_overwrites_payload(fake_redis):
    sid = await session_service.create_session()
    await session_service.set_intermediate(sid, {"rows": [1, 2]}, last_query="SELECT 1")
    s = await session_service.get_session(sid)
    assert s is not None
    assert s["intermediate_results"] == {"rows": [1, 2]}
    assert s["last_query"] == "SELECT 1"

    await session_service.set_intermediate(sid, None)
    s = await session_service.get_session(sid)
    assert s is not None and s["intermediate_results"] is None


async def test_ttl_is_set_on_create(fake_redis):
    from app.services.session_service import SESSION_TTL_SECONDS

    sid = await session_service.create_session()
    ttl = await session_service.ttl(sid)
    assert ttl is not None
    # Within a few seconds of the configured TTL (clock skew safe).
    assert SESSION_TTL_SECONDS - 5 <= ttl <= SESSION_TTL_SECONDS
