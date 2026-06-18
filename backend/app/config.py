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
    # ``LLM_PROVIDER`` picks the chat-model backend: ``"openai"`` (default,
    # OpenAI-compatible: Ark v3 endpoint, OpenAI itself, etc.) or
    # ``"anthropic"`` (Anthropic-compatible: Claude, Ark coding endpoint
    # at /api/coding without the /v3 suffix). Switching is env-only —
    # change the value and restart the backend; there's no runtime swap.
    LLM_PROVIDER: str = "openai"

    OPENAI_API_KEY: str = ""
    OPENAI_MODEL: str = "gpt-4o"
    OPENAI_BASE_URL: str | None = None

    ANTHROPIC_API_KEY: str = ""
    ANTHROPIC_MODEL: str = "claude-3-5-sonnet-20241022"
    # Anthropic-compatible base URL. The official API is
    # https://api.anthropic.com; Ark's coding endpoint is
    # https://ark.cn-beijing.volces.com/api/coding. Leave None for the
    # official API.
    ANTHROPIC_BASE_URL: str | None = None

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

    # Phase 5B: 日志格式 "json"（生产，方便 ELK/Loki 摄入）或 "text"（本地可读）
    LOG_FORMAT: str = "json"

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


_DEV_DEFAULT_JWT_SECRET = "dev-only-change-me-in-prod"


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


def _validate_jwt_secret() -> None:
    """Refuse to boot with the placeholder JWT secret outside dev/test.

    The default in Settings is a known string committed to the repo; anyone
    reading the source can forge tokens for an instance that didn't override
    it. We treat that as a fatal misconfiguration rather than a warning.

    Exempt environments:
      - ``LLM_MOCK=1`` (E2E / CI) — the E2E config sets its own short secret,
        but tests that don't go through playwright.config may inherit the
        default; we don't want pytest collection to abort.
      - ``JWT_SECRET_DEV_OK=1`` — explicit opt-in for local dev when the
        developer wants to run without configuring a secret.
    """
    secret = settings.JWT_SECRET
    if secret == _DEV_DEFAULT_JWT_SECRET or len(secret) < 32:
        if getattr(settings, "LLM_MOCK", False) or os.getenv("JWT_SECRET_DEV_OK"):
            return
        raise RuntimeError(
            "JWT_SECRET is the committed placeholder or shorter than 32 bytes. "
            "Set a long random string in .env (or JWT_SECRET_DEV_OK=1 for local dev)."
        )


settings = Settings()
_check_required_at_startup()
_validate_jwt_secret()
