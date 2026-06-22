from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime
from typing import Any

import redis.asyncio as aioredis

from ..config import settings
from ..utils.logger import get_logger

logger = get_logger(__name__)

SESSION_TTL_SECONDS = 30 * 60
SESSION_KEY_PREFIX = "session:"
MAX_CHAT_HISTORY = 50

# Fields a client is allowed to write via update_session.
_UPDATABLE_FIELDS = {
    "data_source_id",
    "data_source_ids",
    "chat_history",
    "intermediate_results",
    "last_query",
    # owner_id is intentionally NOT here — clients can't reassign ownership.
}


def _utcnow() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


def _key(session_id: str) -> str:
    return f"{SESSION_KEY_PREFIX}{session_id}"


def _user_index_key(user_id: str) -> str:
    """Phase 6: Redis SET key tracking all sessions owned by a user.

    We SADD on create, SREM on delete, and SMEMBERS for the list endpoint.
    The SET has no TTL — orphaned ids (sessions that expired via setex TTL
    but weren't explicitly deleted) are filtered out lazily by the list
    endpoint (it GETs each id and drops the ones that come back None).
    """
    return f"user_sessions:{user_id}"


def _empty_session(session_id: str, owner_id: str | None = None) -> dict[str, Any]:
    now = _utcnow()
    return {
        "session_id": session_id,
        "owner_id": owner_id,  # Phase 4A: ACL — None until first chat binds a user
        "data_source_id": None,
        "data_source_ids": [],
        "chat_history": [],
        "intermediate_results": None,
        "last_query": None,
        "created_at": now,
        "updated_at": now,
    }


_redis: aioredis.Redis | None = None


def _get_redis() -> aioredis.Redis:
    global _redis
    if _redis is None:
        _redis = aioredis.from_url(
            settings.REDIS_URL,
            decode_responses=True,
            socket_connect_timeout=2,
            socket_timeout=2,
        )
    return _redis


def _serialize(payload: dict[str, Any]) -> str:
    return json.dumps(payload, ensure_ascii=False, default=str)


async def ping() -> bool:
    """Return True if Redis is reachable. Useful for health checks."""
    try:
        return bool(await _get_redis().ping())
    except Exception as e:
        logger.warning("redis ping failed: %s", e)
        return False


async def create_session(owner_id: str | None = None) -> str:
    """Create a new session and return its UUID4 id.

    Phase 4A: ``owner_id`` is stamped at creation so subsequent reads can
    enforce ACL. Sessions created without an owner (legacy callers, tests)
    are visible to everyone — the route layer never allows that path.

    Phase 6: when ``owner_id`` is set, SADD the session id into the owner's
    index set so the list endpoint can find them without scanning every
    ``session:*`` key.
    """
    session_id = str(uuid.uuid4())
    payload = _empty_session(session_id, owner_id=owner_id)
    redis = _get_redis()
    await redis.setex(_key(session_id), SESSION_TTL_SECONDS, _serialize(payload))
    if owner_id:
        try:
            await redis.sadd(_user_index_key(owner_id), session_id)
        except Exception:
            # Index is best-effort; a failure here must not block creation.
            logger.exception("failed to add session %s to user index", session_id)
    logger.info("created session %s", session_id)
    return session_id


async def get_session(session_id: str) -> dict[str, Any] | None:
    """Return the session dict, or None if missing/expired."""
    raw = await _get_redis().get(_key(session_id))
    if not raw:
        return None
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        logger.exception("session %s contained invalid JSON; dropping", session_id)
        await _get_redis().delete(_key(session_id))
        return None


async def update_session(
    session_id: str,
    updates: dict[str, Any],
    *,
    reset_ttl: bool = True,
) -> dict[str, Any] | None:
    """Merge allowed fields into the session. Returns the merged payload or None.

    The TTL is reset on every successful update so an active session never expires
    mid-conversation.
    """
    raw = await _get_redis().get(_key(session_id))
    if not raw:
        return None
    session = json.loads(raw)

    for key, value in updates.items():
        if key in _UPDATABLE_FIELDS:
            session[key] = value

    session["updated_at"] = _utcnow()

    new_ttl = SESSION_TTL_SECONDS if reset_ttl else await ttl(session_id)
    await _get_redis().setex(
        _key(session_id),
        max(new_ttl or SESSION_TTL_SECONDS, 1),
        _serialize(session),
    )
    return session


async def delete_session(session_id: str) -> bool:
    """Delete a session. Returns True if the key existed.

    Phase 6: also SREM the session from its owner's index set. We don't
    know the owner here without reading the session first — the caller is
    expected to pass it via ``delete_session_for_user`` if they want the
    index cleaned up. The list endpoint tolerates orphan ids anyway.
    """
    return bool(await _get_redis().delete(_key(session_id)))


async def delete_session_for_user(session_id: str, owner_id: str | None) -> bool:
    """Delete a session AND remove it from the owner's index.

    Use this when the caller already knows the owner (the route layer does
    — it fetched the session for the ACL check). ``delete_session`` without
    the owner still works, just leaves the id in the index set (harmless
    — the list endpoint filters orphans).
    """
    redis = _get_redis()
    deleted = bool(await redis.delete(_key(session_id)))
    if owner_id:
        try:
            await redis.srem(_user_index_key(owner_id), session_id)
        except Exception:
            logger.exception("failed to srem session %s from user index", session_id)
    return deleted


async def list_sessions_for_user(user_id: str) -> list[dict[str, Any]]:
    """Return all live sessions owned by ``user_id``, newest first.

    Phase 6: reads the owner's index SET, then GETs each id. Sessions that
    expired via TTL (but weren't explicitly deleted) return None and are
    lazily SREM'd so the index doesn't grow without bound.
    """
    redis = _get_redis()
    try:
        ids = await redis.smembers(_user_index_key(user_id))
    except Exception:
        logger.exception("failed to read session index for user %s", user_id)
        return []
    out: list[dict[str, Any]] = []
    orphans: list[str] = []
    for sid in ids:
        raw = await redis.get(_key(sid))
        if not raw:
            orphans.append(sid)
            continue
        try:
            session = json.loads(raw)
        except json.JSONDecodeError:
            orphans.append(sid)
            continue
        out.append(session)
    if orphans:
        try:
            await redis.srem(_user_index_key(user_id), *orphans)
        except Exception:
            logger.exception("failed to prune orphan session ids from index")
    # Newest first by updated_at (fallback created_at).
    out.sort(key=lambda s: s.get("updated_at") or s.get("created_at") or "", reverse=True)
    return out


async def delete_sessions_by_data_source(data_source_id: str) -> int:
    """Remove the data source from every session that has it.

    Sessions with only this source are deleted outright; sessions that
    also bind other sources have the id stripped from their list and
    their primary ``data_source_id`` repointed (or nulled) so the
    remaining session is still usable.
    """
    redis = _get_redis()
    removed = 0
    async for key in redis.scan_iter(match=f"{SESSION_KEY_PREFIX}*"):
        raw = await redis.get(key)
        if not raw:
            continue
        try:
            session = json.loads(raw)
        except json.JSONDecodeError:
            logger.warning("malformed session at %s; skipping", key)
            continue
        primary = session.get("data_source_id")
        ids = list(session.get("data_source_ids") or [])
        if data_source_id not in ids and primary != data_source_id:
            continue
        # Drop the id from the list; if it was the primary, hand off to
        # the first remaining entry (or null when there are none).
        new_ids = [i for i in ids if i != data_source_id]
        new_primary = (new_ids[0] if new_ids else None) if primary == data_source_id else primary
        if not new_ids and not new_primary:
            # The session is bound to nothing usable -- drop it.
            await redis.delete(key)
            removed += 1
            continue
        session["data_source_id"] = new_primary
        session["data_source_ids"] = new_ids
        session["updated_at"] = _utcnow()
        await redis.setex(key, SESSION_TTL_SECONDS, _serialize(session))
    if removed:
        logger.info(
            "removed %d sessions whose only data source was %s",
            removed,
            data_source_id,
        )
    return removed


async def ttl(session_id: str) -> int | None:
    return await _get_redis().ttl(_key(session_id))


async def append_chat(
    session_id: str,
    role: str,
    content: str,
    *,
    chart_data: Any = None,
    sql_query: str | None = None,
) -> dict[str, Any] | None:
    """Append a chat turn to the session history and reset the TTL."""
    raw = await _get_redis().get(_key(session_id))
    if not raw:
        return None
    session = json.loads(raw)

    history = list(session.get("chat_history") or [])
    history.append(
        {
            "role": role,
            "content": content,
            "chart_data": chart_data if role == "assistant" else None,
            "sql_query": sql_query if role == "assistant" else None,
            "timestamp": _utcnow(),
        }
    )
    if len(history) > MAX_CHAT_HISTORY:
        history = history[-MAX_CHAT_HISTORY:]
    session["chat_history"] = history
    session["updated_at"] = _utcnow()
    await _get_redis().setex(_key(session_id), SESSION_TTL_SECONDS, _serialize(session))
    return session


async def set_intermediate(
    session_id: str,
    payload: dict[str, Any] | None,
    *,
    last_query: str | None = None,
) -> dict[str, Any] | None:
    """Overwrite the intermediate_results snapshot (only the latest is kept)."""
    raw = await _get_redis().get(_key(session_id))
    if not raw:
        return None
    session = json.loads(raw)
    session["intermediate_results"] = payload
    if last_query is not None:
        session["last_query"] = last_query
    session["updated_at"] = _utcnow()
    await _get_redis().setex(_key(session_id), SESSION_TTL_SECONDS, _serialize(session))
    return session


async def bind_data_source(session_id: str, data_source_id: str | None) -> dict[str, Any] | None:
    """Add ``data_source_id`` to the session's binding list.

    The first id added also becomes the session's primary ``data_source_id``;
    subsequent additions are appended to ``data_source_ids`` so the LLM can
    write JOINs across them. Returns the (possibly unchanged) session, or
    None if missing.
    """
    raw = await _get_redis().get(_key(session_id))
    if not raw:
        return None
    session = json.loads(raw)
    if not data_source_id:
        return session
    ids = list(session.get("data_source_ids") or [])
    if data_source_id in ids:
        return session
    ids.append(data_source_id)
    session["data_source_ids"] = ids
    if not session.get("data_source_id"):
        session["data_source_id"] = data_source_id
    session["updated_at"] = _utcnow()
    await _get_redis().setex(_key(session_id), SESSION_TTL_SECONDS, _serialize(session))
    return session


async def unbind_data_source(session_id: str, data_source_id: str) -> dict[str, Any] | None:
    """Remove ``data_source_id`` from the session's binding list.

    If it was the primary, the next remaining id takes its place. The
    session itself is NOT deleted -- callers decide whether to drop a
    session that ends up with no bindings (see ``delete_sessions_by_data_source``).
    """
    raw = await _get_redis().get(_key(session_id))
    if not raw:
        return None
    session = json.loads(raw)
    ids = [i for i in (session.get("data_source_ids") or []) if i != data_source_id]
    session["data_source_ids"] = ids
    if session.get("data_source_id") == data_source_id:
        session["data_source_id"] = ids[0] if ids else None
    session["updated_at"] = _utcnow()
    await _get_redis().setex(_key(session_id), SESSION_TTL_SECONDS, _serialize(session))
    return session


async def set_data_source_ids(session_id: str, data_source_ids: list[str]) -> dict[str, Any] | None:
    """Replace the session's binding list wholesale.

    Used by update_session when the client PATCHes the full list (e.g. after
    multi-select on the sidebar). The first id in the new list becomes the
    primary; an empty list clears all bindings.
    """
    raw = await _get_redis().get(_key(session_id))
    if not raw:
        return None
    session = json.loads(raw)
    # Dedup while preserving order.
    seen: set[str] = set()
    deduped: list[str] = []
    for i in data_source_ids:
        if i and i not in seen:
            seen.add(i)
            deduped.append(i)
    session["data_source_ids"] = deduped
    session["data_source_id"] = deduped[0] if deduped else None
    session["updated_at"] = _utcnow()
    await _get_redis().setex(_key(session_id), SESSION_TTL_SECONDS, _serialize(session))
    return session
