from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, EmailStr, Field


class UserRegister(BaseModel):
    """POST /auth/register body."""

    email: EmailStr
    password: str = Field(min_length=8, max_length=128)


class UserLogin(BaseModel):
    """POST /auth/login body."""

    email: EmailStr
    password: str = Field(min_length=1, max_length=128)


class TokenResponse(BaseModel):
    """Returned by /auth/login, /auth/register, /auth/refresh."""

    access_token: str
    refresh_token: str
    token_type: str = "bearer"
    expires_in: int


class RefreshRequest(BaseModel):
    refresh_token: str


class UserView(BaseModel):
    """Returned by /auth/me and embedded in TokenResponse payloads."""

    id: str
    email: str
    is_active: bool = True
    must_change_password: bool = False


class ChangePasswordRequest(BaseModel):
    """POST /auth/change-password body."""

    old_password: str = Field(min_length=1, max_length=128)
    new_password: str = Field(min_length=8, max_length=128)


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
    data_source_ids: list[str] | None = None


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
    data_source_ids: list[str] = Field(default_factory=list)
    chat_history: list[ChatMessageItem] = Field(default_factory=list)
    intermediate_results: Any | None = None
    last_query: str | None = None
    created_at: str | None = None
    updated_at: str | None = None
    ttl_seconds: int = 0


class SessionView(BaseModel):
    session_id: str
    data_source_id: str | None = None
    data_source_ids: list[str] = Field(default_factory=list)
    chat_history: list[ChatMessageItem] = Field(default_factory=list)
    intermediate_results: Any | None = None
    last_query: str | None = None
    created_at: str | None = None
    updated_at: str | None = None
    ttl_seconds: int = 0


class SessionUpdate(BaseModel):
    """PATCH body. All fields are optional; only the ones present get merged."""

    data_source_id: str | None = None
    data_source_ids: list[str] | None = None
    chat_history: list[ChatMessageItem] | None = None
    intermediate_results: Any | None = None
    last_query: str | None = None


class LineageEntry(BaseModel):
    """One executed query against a data source.

    Sourced from the per-data-source sidecar (``metadata_service``). The
    ``ts`` field is a Unix timestamp in seconds; the frontend converts it
    to local time for display.
    """

    ts: float
    sql: str
    source_ids: list[str] = Field(default_factory=list)
    tables: list[str] = Field(default_factory=list)
    row_count: int = 0
    duration_ms: float = 0.0
    ok: bool = True
    cache_hit: bool = False
    error: str | None = None
    user_id: str | None = None  # Phase 4B: who ran the query


class LineageResponse(BaseModel):
    """GET /datasources/{id}/lineage response."""

    data_source_id: str
    entries: list[LineageEntry] = Field(default_factory=list)
    total: int
