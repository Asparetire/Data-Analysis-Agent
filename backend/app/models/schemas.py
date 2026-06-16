from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field


class ChatMessageItem(BaseModel):
    """A single turn in the conversation history persisted to Redis."""

    role: Literal["user", "assistant"]
    content: str
    chart_data: Any | None = None
    sql_query: str | None = None
    timestamp: str | None = None


class ChatRequest(BaseModel):
    session_id: str
    message: str
    data_source_id: str | None = None


class ChatResponse(BaseModel):
    session_id: str
    message: str
    chart_data: Any | None = None
    sql_query: str | None = None
    error: str | None = None


class UploadResponse(BaseModel):
    file_id: str
    filename: str
    status: str
    message: str


class DataSource(BaseModel):
    id: str
    name: str  # effective name (custom display name, or filename if unset)
    filename: str  # original on-disk filename, immutable
    type: str  # "csv" | "excel" | "json" | "database"
    created_at: datetime


class DataSourceRename(BaseModel):
    """PATCH body for renaming a data source's display name."""

    display_name: str = Field(min_length=1, max_length=200)


class SessionCreateResponse(BaseModel):
    """Returned by POST /sessions. Includes the new session_id and the empty payload."""

    session_id: str
    data_source_id: str | None = None
    chat_history: list[ChatMessageItem] = Field(default_factory=list)
    intermediate_results: Any | None = None
    last_query: str | None = None
    created_at: str | None = None
    updated_at: str | None = None
    ttl_seconds: int = 0


class SessionView(BaseModel):
    session_id: str
    data_source_id: str | None = None
    chat_history: list[ChatMessageItem] = Field(default_factory=list)
    intermediate_results: Any | None = None
    last_query: str | None = None
    created_at: str | None = None
    updated_at: str | None = None
    ttl_seconds: int = 0


class SessionUpdate(BaseModel):
    """PATCH body. All fields are optional; only the ones present get merged."""

    data_source_id: str | None = None
    chat_history: list[ChatMessageItem] | None = None
    intermediate_results: Any | None = None
    last_query: str | None = None
