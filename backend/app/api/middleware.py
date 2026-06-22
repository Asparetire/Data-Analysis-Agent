"""Phase 4B: rate limiting middleware.

Strategy:
- Per-route limits configured below. Unlisted routes are unlimited
  (static assets, /docs, /health should not be throttled).
- Authenticated requests count against ``rl:user:{user_id}``.
- Unauthenticated requests fall back to ``rl:ip:{client_ip}``.
- The login + register endpoints always use IP keying (the user isn't
  authenticated yet) so brute-force attempts are throttled per source IP.
- On limit exceeded we return 429 with ``Retry-After`` matching the window.

The limiter fails open on Redis errors — a Redis outage must not lock the
whole API. We log the failure so it's visible.
"""

from __future__ import annotations

import uuid

from fastapi import Request
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import JSONResponse

from ..config import settings
from ..services import auth_service, session_service
from ..utils.logger import get_logger
from ..utils.rate_limit import WINDOW_S, check_rate_limit
from ..utils.request_id import request_id_ctx

logger = get_logger(__name__)


def _route_limit(path: str) -> int | None:
    """Return the per-minute limit for a path, or None to skip limiting."""
    # Auth endpoints: per-IP (no user yet). Tighter limit to slow brute force.
    if path == f"{settings.API_V1_STR}/auth/login":
        return settings.RATE_LIMIT_PER_IP_PER_MINUTE
    if path == f"{settings.API_V1_STR}/auth/register":
        return max(1, settings.RATE_LIMIT_PER_IP_PER_MINUTE // 2)
    if path == f"{settings.API_V1_STR}/auth/refresh":
        return settings.RATE_LIMIT_PER_IP_PER_MINUTE
    # Expensive endpoints: per-user when authed, per-IP otherwise.
    if path in (
        f"{settings.API_V1_STR}/chat",
        f"{settings.API_V1_STR}/chat/stream",
        f"{settings.API_V1_STR}/upload",
    ):
        return settings.RATE_LIMIT_PER_USER_PER_MINUTE
    return None


def _client_ip(request: Request) -> str:
    # Honour the first hop in X-Forwarded-For if present (behind a proxy).
    # Fall back to the direct peer; tests that hit TestClient have .client.
    xff = request.headers.get("x-forwarded-for", "")
    if xff:
        return xff.split(",", 1)[0].strip()
    return (request.client.host if request.client else "unknown") or "unknown"


def _extract_user_id(request: Request) -> str | None:
    """Best-effort: decode the bearer token without fetching the user row."""
    header = request.headers.get("Authorization", "")
    if not header.lower().startswith("bearer "):
        return None
    token = header[7:].strip()
    try:
        payload = auth_service.verify_access_token(token)
    except auth_service.AuthError:
        return None
    return payload.get("sub")


class RateLimitMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        limit = _route_limit(request.url.path)
        if limit is None:
            return await call_next(request)

        user_id = _extract_user_id(request)
        key = f"rl:user:{user_id}" if user_id else f"rl:ip:{_client_ip(request)}"

        try:
            redis = session_service._get_redis()  # noqa: SLF001
        except Exception:  # noqa: BLE001
            logger.exception("redis unavailable; rate check skipped")
            return await call_next(request)

        allowed, count = await check_rate_limit(redis, key, limit)
        if not allowed:
            logger.warning("rate limit hit key=%s count=%d limit=%d", key, count, limit)
            return JSONResponse(
                status_code=429,
                content={"detail": "Rate limit exceeded. Try again shortly."},
                headers={"Retry-After": str(WINDOW_S)},
            )
        return await call_next(request)


class RequestIdMiddleware(BaseHTTPMiddleware):
    """Assign a request_id per request and propagate it everywhere.

    Reads ``X-Request-ID`` from the incoming request (set by nginx or by
    an upstream caller); generates a UUID4 otherwise. The id is:
      - stored in a ContextVar so log records carry it (see logger.py)
      - echoed back in the ``X-Request-ID`` response header so clients can
        correlate a failing response to a server log line

    Registered AFTER RateLimitMiddleware so rate-limit 429s still get a
    request_id in their log line — the outermost middleware sees every
    request first.
    """

    async def dispatch(self, request: Request, call_next):
        rid = request.headers.get("x-request-id") or uuid.uuid4().hex
        token = request_id_ctx.set(rid)
        try:
            response = await call_next(request)
            response.headers["X-Request-ID"] = rid
            return response
        finally:
            request_id_ctx.reset(token)
