from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from prometheus_fastapi_instrumentator import Instrumentator

from .api.auth_routes import router as auth_router
from .api.middleware import RateLimitMiddleware, RequestIdMiddleware
from .api.routes import router
from .config import settings
from .services import auth_service
from .utils.logger import get_logger

logger = get_logger(__name__)

# Phase 5D: tag metadata for OpenAPI grouping in Swagger UI. Routes that
# don't set `tags=` default to their module name; setting tags here lets
# /docs show a clean grouped layout instead of a flat list.
_TAGS_METADATA = [
    {"name": "auth", "description": "注册、登录、refresh、logout、me"},
    {
        "name": "datasources",
        "description": "上传、列出、重命名、删除、预览、schema、分页、表浏览、lineage",
    },
    {"name": "sessions", "description": "会话 CRUD"},
    {"name": "chat", "description": "同步 / SSE 流式对话"},
    {"name": "system", "description": "health、metrics"},
]

app = FastAPI(
    title=settings.PROJECT_NAME,
    version="0.1.0",
    openapi_url=f"{settings.API_V1_STR}/openapi.json",
    openapi_tags=_TAGS_METADATA,
)

# CORS 中间件
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.CORS_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
# Phase 4B: per-user / per-IP sliding window rate limit on expensive routes.
# Outermost — sees every request, including rate-limited 429s, before any
# other middleware short-circuits.
app.add_middleware(RateLimitMiddleware)
# Phase 5B: per-request id for log correlation. Inside rate-limit so that
# 429s still get an id (rate limit ran first and stamped its log line with
# the id we're about to set). Order is intentional — see middleware.py docstring.
app.add_middleware(RequestIdMiddleware)

# Phase 5B: Prometheus metrics at /metrics. Default buckets cover HTTP
# latency well enough; custom metrics can be added later via
# Instrumentator().add(...) if needed. The endpoint is unauthenticated
# so Prometheus can scrape without credentials — restrict at the nginx
# layer in production (allow only the scraper's IP).
Instrumentator().instrument(app).expose(app, endpoint="/metrics")

# 注册路由
app.include_router(router, prefix=settings.API_V1_STR)
app.include_router(auth_router, prefix=settings.API_V1_STR)


@app.on_event("startup")
async def _startup() -> None:
    """Build the users table and migrate any ownerless data to a default admin.

    Failures here are FATAL: if the users table can't be created, every
    /auth/* call will 500 and the app is useless. Previously we swallowed
    the exception (logged + kept running) which masked the root cause —
    e.g. Phase 4E CI hung for a full run before anyone noticed /auth/*
    was broken because the main DB directory didn't exist. Letting the
    process die lets the orchestrator (Docker / systemd / uvicorn
    supervisor) restart it and surface the failure immediately.
    """
    auth_service.init_users_table()
    auth_service.migrate_ownerless_data()
    logger.info("auth tables initialized; migration complete")


@app.get("/")
async def root():
    return {"message": "Data Analysis Agent API"}
