from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from .api.auth_routes import router as auth_router
from .api.middleware import RateLimitMiddleware
from .api.routes import router
from .config import settings
from .services import auth_service
from .utils.logger import get_logger

logger = get_logger(__name__)

app = FastAPI(
    title=settings.PROJECT_NAME,
    version="0.1.0",
    openapi_url=f"{settings.API_V1_STR}/openapi.json",
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
app.add_middleware(RateLimitMiddleware)

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
