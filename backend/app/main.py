from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from .api.auth_routes import router as auth_router
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

# 注册路由
app.include_router(router, prefix=settings.API_V1_STR)
app.include_router(auth_router, prefix=settings.API_V1_STR)


@app.on_event("startup")
async def _startup() -> None:
    """Build the users table and migrate any ownerless data to a default admin.

    Runs synchronously — SQLite DDL is fast, and migrating the sidecar is a
    handful of file writes. Failures are logged but never fatal: the app
    still starts, /auth/* will surface the missing table as 500s instead.
    """
    try:
        auth_service.init_users_table()
        auth_service.migrate_ownerless_data()
    except Exception:  # noqa: BLE001
        logger.exception("startup auth init failed; /auth/* will be broken")


@app.get("/")
async def root():
    return {"message": "Data Analysis Agent API"}
