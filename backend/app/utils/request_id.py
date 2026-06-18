"""ContextVar holding the current request's id.

Lives in its own module so it can be imported from both
``app.utils.logger`` (no FastAPI / starlette dependency) and
``app.api.middleware.RequestIdMiddleware`` without circular imports.

The ContextVar is request-scoped: asyncio copies the context per task,
so concurrent requests don't bleed their ids into each other.
"""

from __future__ import annotations

import contextvars

request_id_ctx: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "request_id", default=None
)
