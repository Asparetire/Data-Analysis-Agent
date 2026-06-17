from __future__ import annotations

import json
import os
import warnings

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=True,
        extra="ignore",
    )

    # API
    API_V1_STR: str = "/api/v1"
    PROJECT_NAME: str = "Data Analysis Agent"

    # LLM
    OPENAI_API_KEY: str = ""
    OPENAI_MODEL: str = "gpt-4o"
    OPENAI_BASE_URL: str | None = None
    # Phase 4E: when true, ``_build_llm`` returns ``MockChatModel`` instead of
    # ``ChatOpenAI``. Used by Playwright E2E tests so they don't need an
    # OpenAI key. Never set this in production -- the mock cannot answer
    # real questions.
    LLM_MOCK: bool = False

    # 主库（用于会话元数据等，data_source 业务表走独立 SQLite 文件）
    DATABASE_URL: str = "sqlite:///./data/main.db"

    # 数据目录
    DATA_DIR: str = "./data"

    # Redis
    REDIS_URL: str = "redis://localhost:6379/0"

    # CORS：从环境变量读 JSON 字符串，例如 '["http://localhost:5173"]'
    CORS_ORIGINS: list[str] = ["http://localhost:5173", "http://localhost:3000"]

    # Phase 4: 认证 / JWT
    # JWT_SECRET 应在 .env 中覆盖；保留默认值只为本地开发不被卡住。
    JWT_SECRET: str = "dev-only-change-me-in-prod"
    JWT_ALGORITHM: str = "HS256"
    ACCESS_TOKEN_TTL_MINUTES: int = 15
    REFRESH_TOKEN_TTL_DAYS: int = 7

    # 首次启动且 main.db 中已有无主数据源时，把它们绑定到这个默认用户。
    # 仅在迁移路径下创建；之后用户应自行注册。
    MIGRATION_ADMIN_EMAIL: str = "admin@local.invalid"
    MIGRATION_ADMIN_PASSWORD: str = "change-me-now"

    # Phase 4B: 限流（按用户 / 按 IP 的 sliding window，每分钟）
    RATE_LIMIT_PER_USER_PER_MINUTE: int = 60
    RATE_LIMIT_PER_IP_PER_MINUTE: int = 20

    @field_validator("CORS_ORIGINS", mode="before")
    @classmethod
    def _parse_cors(cls, v):
        if isinstance(v, str):
            v = v.strip()
            if not v:
                return []
            try:
                return json.loads(v)
            except json.JSONDecodeError:
                return [item.strip() for item in v.split(",") if item.strip()]
        return v


def _check_required_at_startup() -> None:
    # In mock mode (E2E tests) the OpenAI key isn't needed; skip the warning
    # so test logs aren't noisy.
    if getattr(settings, "LLM_MOCK", False):
        return
    if not os.getenv("OPENAI_API_KEY"):
        warnings.warn(
            "OPENAI_API_KEY 未设置；调用 LLM 相关接口会失败。",
            stacklevel=1,
        )


settings = Settings()
_check_required_at_startup()
