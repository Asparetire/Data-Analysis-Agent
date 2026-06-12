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

    # 主库（用于会话元数据等，data_source 业务表走独立 SQLite 文件）
    DATABASE_URL: str = "sqlite:///./data/main.db"

    # 数据目录
    DATA_DIR: str = "./data"

    # Redis
    REDIS_URL: str = "redis://localhost:6379/0"

    # CORS：从环境变量读 JSON 字符串，例如 '["http://localhost:5173"]'
    CORS_ORIGINS: list[str] = ["http://localhost:5173", "http://localhost:3000"]

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
    if not os.getenv("OPENAI_API_KEY"):
        warnings.warn(
            "OPENAI_API_KEY 未设置；调用 LLM 相关接口会失败。",
            stacklevel=1,
        )


settings = Settings()
_check_required_at_startup()
