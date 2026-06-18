from __future__ import annotations

import asyncio
import json
import time
import uuid
from datetime import datetime
from pathlib import Path

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from sse_starlette.sse import EventSourceResponse
from starlette.responses import JSONResponse

from ..config import settings
from ..models.schemas import (
    ChatRequest,
    ChatResponse,
    DataSource,
    DataSourceRename,
    LineageEntry,
    LineageResponse,
    SessionCreateResponse,
    SessionUpdate,
    SessionView,
    UploadResponse,
)
from ..services import chat_service, data_service, metadata_service, session_service, streaming
from ..services.chat_service import SessionBindingError
from ..utils.logger import get_logger
from .dependencies import current_user

logger = get_logger(__name__)
router = APIRouter()

# Phase 5C: process start time for /health/ready. Captured at module import
# (≈ process start) so uptime_seconds reflects the real lifetime.
_started_at = time.monotonic()


def _get_app_version() -> str:
    """Read the FastAPI app version, deferring the import to avoid a
    circular load (main.py imports routes.py, so we can't import main at
    module top here)."""
    try:
        from ..main import app as _app

        return _app.version
    except Exception:  # noqa: BLE001
        return "unknown"


# ---------------------------------------------------------------------------
# Upload
# ---------------------------------------------------------------------------


@router.post("/upload", response_model=UploadResponse)
async def upload_file(
    file: UploadFile = File(...),
    user: dict = Depends(current_user),
):
    file_id = str(uuid.uuid4())
    try:
        await data_service.save_uploaded_file(file, file_id)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    except Exception as e:
        logger.exception("upload failed")
        raise HTTPException(status_code=500, detail=f"Upload failed: {e}") from e

    # Phase 4A: stamp owner so subsequent reads can enforce ACL.
    try:
        metadata_service.set_owner(file_id, user["id"])
    except Exception:  # noqa: BLE001
        logger.warning("failed to stamp owner on %s", file_id, exc_info=True)

    return UploadResponse(
        file_id=file_id,
        filename=file.filename or "",
        status="success",
        message=f"File {file.filename} uploaded",
    )


# ---------------------------------------------------------------------------
# Chat
# ---------------------------------------------------------------------------


@router.post("/chat", response_model=ChatResponse)
async def chat_endpoint(request: ChatRequest, user: dict = Depends(current_user)):
    try:
        return await chat_service.run_chat(
            session_id=request.session_id,
            message=request.message,
            data_source_id=request.data_source_id,
            data_source_ids=request.data_source_ids,
            owner_id=user["id"],
        )
    except SessionBindingError as e:
        raise HTTPException(status_code=403, detail=str(e)) from e
    except chat_service.SessionNotFound as e:
        raise HTTPException(status_code=404, detail=str(e)) from e
    except chat_service.SessionForbidden as e:
        raise HTTPException(status_code=404, detail=str(e)) from e
    except Exception as e:
        logger.exception("chat failed")
        raise HTTPException(status_code=500, detail=f"Chat failed: {e}") from e


@router.post("/chat/stream")
async def chat_stream_endpoint(request: ChatRequest, user: dict = Depends(current_user)):
    """SSE endpoint for a single chat turn.

    See services/streaming.py for the protocol. The connection stays open
    until the run finishes or an error event is emitted.
    """

    async def event_generator():
        try:
            async for event in streaming.stream_chat(
                session_id=request.session_id,
                message=request.message,
                data_source_id=request.data_source_id,
                data_source_ids=request.data_source_ids,
                owner_id=user["id"],
            ):
                yield event
        except streaming.StreamForbidden as e:
            yield {
                "event": "error",
                "data": json.dumps(
                    {"type": "error", "code": 404, "message": str(e)},
                    ensure_ascii=False,
                ),
            }
        except Exception as e:
            logger.exception("stream generator crashed")
            yield {
                "event": "error",
                "data": json.dumps(
                    {
                        "type": "error",
                        "code": 500,
                        "message": f"Stream error: {e}",
                    },
                    ensure_ascii=False,
                ),
            }

    return EventSourceResponse(
        event_generator(),
        ping=15,
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
            "Connection": "keep-alive",
        },
    )


# ---------------------------------------------------------------------------
# Data sources
# ---------------------------------------------------------------------------


def _check_datasource_owner(data_source_id: str, user: dict) -> None:
    """404 if the data source doesn't exist OR is owned by someone else."""
    owner = metadata_service.get_owner(data_source_id)
    # Two failure modes collapse to the same 404 so unauthenticated callers
    # can't enumerate ids: missing sidecar entry, or owner mismatch.
    if not owner or owner != user["id"]:
        # Also confirm the upload file exists — owner-less pre-Phase-4 data
        # should not be reachable through this API even if it lingers.
        raise HTTPException(status_code=404, detail="Data source not found")


@router.get("/datasources", response_model=list[DataSource])
async def get_datasources(user: dict = Depends(current_user)):
    """List only the data sources owned by the current user."""
    uploads_dir = Path(settings.DATA_DIR) / "uploads"
    if not uploads_dir.exists():
        return []
    owned_ids = set(metadata_service.list_ids_for_owner(user["id"]))
    items: list[DataSource] = []
    for path in sorted(uploads_dir.iterdir(), key=lambda p: p.stat().st_mtime, reverse=True):
        if path.suffix.lower() not in {".csv", ".xlsx", ".xls", ".json"}:
            continue
        file_id = path.stem
        if file_id not in owned_ids:
            continue
        ext = path.suffix.lower().lstrip(".")
        ds_type = "csv" if ext == "csv" else "excel" if ext in {"xlsx", "xls"} else "json"
        stat = path.stat()
        custom_name = metadata_service.get_display_name(file_id)
        items.append(
            DataSource(
                id=file_id,
                name=custom_name or path.name,
                filename=path.name,
                type=ds_type,
                created_at=datetime.fromtimestamp(stat.st_mtime),
            )
        )
    return items


@router.patch("/datasources/{data_source_id}", response_model=DataSource)
async def rename_datasource(
    data_source_id: str,
    body: DataSourceRename,
    user: dict = Depends(current_user),
):
    _check_datasource_owner(data_source_id, user)
    uploads_dir = Path(settings.DATA_DIR) / "uploads"
    matching = None
    if uploads_dir.exists():
        for path in uploads_dir.iterdir():
            if path.stem == data_source_id and path.suffix.lower() in {
                ".csv",
                ".xlsx",
                ".xls",
                ".json",
            }:
                matching = path
                break
    if matching is None:
        raise HTTPException(status_code=404, detail="Data source not found")
    try:
        new_name = metadata_service.set_display_name(data_source_id, body.display_name)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    ext = matching.suffix.lower().lstrip(".")
    ds_type = "csv" if ext == "csv" else "excel" if ext in {"xlsx", "xls"} else "json"
    return DataSource(
        id=data_source_id,
        name=new_name,
        filename=matching.name,
        type=ds_type,
        created_at=datetime.fromtimestamp(matching.stat().st_mtime),
    )


@router.get("/datasources/{data_source_id}/preview")
async def preview_datasource(
    data_source_id: str,
    limit: int = 5,
    user: dict = Depends(current_user),
):
    _check_datasource_owner(data_source_id, user)
    # data_service.get_sample_rows is a sync SQLite call; run it in a thread
    # so it doesn't block the event loop on large tables.
    rows = await asyncio.to_thread(data_service.get_sample_rows, data_source_id, limit=limit)
    if rows is None:
        raise HTTPException(status_code=404, detail="Data source not found or empty")
    return {"rows": rows, "count": len(rows)}


@router.get("/datasources/{data_source_id}/schema")
async def schema_datasource(
    data_source_id: str,
    table: str | None = None,
    user: dict = Depends(current_user),
):
    _check_datasource_owner(data_source_id, user)
    rows = await asyncio.to_thread(
        data_service.get_sample_rows, data_source_id, limit=1, table=table
    )
    if rows is None:
        raise HTTPException(status_code=404, detail="Data source not found or empty")
    sample = rows[0]
    schema = [{"name": k, "type": _infer_type(v)} for k, v in sample.items()]
    return {"schema": schema}


@router.get("/datasources/{data_source_id}/rows")
async def rows_datasource(
    data_source_id: str,
    table: str | None = None,
    offset: int = 0,
    limit: int = 20,
    sort: str | None = None,
    dir: str = "asc",  # noqa: A002 — `dir` is the natural query-param name
    user: dict = Depends(current_user),
):
    """Phase 4D: server-side paginated browse of a single table.

    Defaults to the primary table when ``table`` is omitted. Sort column
    must exist on the table; direction must be ``asc`` or ``desc``. The
    data service validates both against ``PRAGMA table_info`` before
    interpolating them into SQL.
    """
    _check_datasource_owner(data_source_id, user)

    def _fetch() -> dict | None:
        target = table or data_service.get_primary_table(data_source_id)
        return data_service.fetch_rows(
            data_source_id,
            table=target,
            offset=offset,
            limit=limit,
            sort=sort,
            direction=dir,
        )

    payload = await asyncio.to_thread(_fetch)
    if payload is None:
        raise HTTPException(status_code=404, detail="Table not found")
    # Phase 4C layer 2: mask PII in the rows leaving the API, even though
    # upload-time scrub already ran. Defense in depth.
    from ..utils.pii_mask import mask_rows

    payload["rows"] = mask_rows(payload["rows"])
    return payload


@router.get("/datasources/{data_source_id}/tables")
async def tables_datasource(
    data_source_id: str,
    user: dict = Depends(current_user),
):
    """Phase 4D: list tables in this data source for the table browser."""
    _check_datasource_owner(data_source_id, user)

    def _list() -> list[dict]:
        names = data_service.list_tables(data_source_id)
        out = []
        for name in names:
            info = data_service.get_table_info(data_source_id, name)
            out.append({"name": name, "row_count": info.get("row_count", 0) if info else 0})
        return out

    out = await asyncio.to_thread(_list)
    return {"tables": out}


@router.get("/datasources/{data_source_id}/lineage", response_model=LineageResponse)
async def lineage_datasource(
    data_source_id: str,
    limit: int = 50,
    user: dict = Depends(current_user),
):
    _check_datasource_owner(data_source_id, user)
    cap = max(1, min(int(limit), 200))

    def _load() -> tuple[list[dict], list[dict]]:
        raw = metadata_service.get_lineage(data_source_id, limit=cap)
        full = metadata_service.get_lineage(data_source_id, limit=None)
        return raw, full

    raw, full = await asyncio.to_thread(_load)
    entries = [LineageEntry(**r) for r in raw if isinstance(r, dict)]
    return LineageResponse(
        data_source_id=data_source_id,
        entries=entries,
        total=len(full),
    )


@router.delete("/datasources/{data_source_id}")
async def delete_datasource(
    data_source_id: str,
    user: dict = Depends(current_user),
):
    _check_datasource_owner(data_source_id, user)

    def _drop() -> bool:
        deleted = data_service.delete_data_source(data_source_id)
        if deleted:
            metadata_service.delete_entry(data_source_id)
        return deleted

    deleted = await asyncio.to_thread(_drop)
    if not deleted:
        raise HTTPException(status_code=404, detail="Data source not found")
    sessions_removed = await session_service.delete_sessions_by_data_source(data_source_id)
    return {
        "status": "ok",
        "data_source_id": data_source_id,
        "sessions_removed": sessions_removed,
    }


def _infer_type(value) -> str:
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "boolean"
    if isinstance(value, int):
        return "integer"
    if isinstance(value, float):
        return "number"
    return "string"


# ---------------------------------------------------------------------------
# Sessions
# ---------------------------------------------------------------------------


def _check_session_owner(session: dict, user: dict) -> None:
    """404 if the session's owner_id is missing or doesn't match.

    Sessions created before Phase 4A lack ``owner_id`` — we treat those as
    not-found so old clients fail closed and re-authenticate.
    """
    owner = session.get("owner_id")
    if not owner or owner != user["id"]:
        raise HTTPException(status_code=404, detail="Session not found or expired")


@router.get("/sessions/{session_id}", response_model=SessionView)
async def get_session(session_id: str, user: dict = Depends(current_user)):
    session = await session_service.get_session(session_id)
    if session is None:
        raise HTTPException(status_code=404, detail="Session not found or expired")
    _check_session_owner(session, user)
    ttl = await session_service.ttl(session_id)
    return SessionView(
        session_id=session["session_id"],
        data_source_id=session.get("data_source_id"),
        data_source_ids=session.get("data_source_ids") or [],
        chat_history=session.get("chat_history", []),
        intermediate_results=session.get("intermediate_results"),
        last_query=session.get("last_query"),
        created_at=session.get("created_at"),
        updated_at=session.get("updated_at"),
        ttl_seconds=ttl or 0,
    )


@router.post("/sessions", response_model=SessionCreateResponse, status_code=201)
async def create_session(user: dict = Depends(current_user)):
    session_id = await session_service.create_session(owner_id=user["id"])
    session = await session_service.get_session(session_id)
    assert session is not None
    return SessionCreateResponse(
        session_id=session["session_id"],
        data_source_id=session.get("data_source_id"),
        data_source_ids=session.get("data_source_ids") or [],
        chat_history=[],
        intermediate_results=None,
        last_query=None,
        created_at=session.get("created_at"),
        updated_at=session.get("updated_at"),
        ttl_seconds=session_service.SESSION_TTL_SECONDS,
    )


@router.patch("/sessions/{session_id}", response_model=SessionView)
async def update_session(
    session_id: str,
    body: SessionUpdate,
    user: dict = Depends(current_user),
):
    updates = body.model_dump(exclude_unset=True)
    if "data_source_id" in updates and updates["data_source_id"] == "":
        updates["data_source_id"] = None
    if "data_source_ids" in updates and updates["data_source_ids"] is not None:
        updates["data_source_ids"] = list(updates["data_source_ids"])
    session = await session_service.update_session(session_id, updates)
    if session is None:
        raise HTTPException(status_code=404, detail="Session not found or expired")
    _check_session_owner(session, user)
    ttl = await session_service.ttl(session_id)
    return SessionView(
        session_id=session["session_id"],
        data_source_id=session.get("data_source_id"),
        data_source_ids=session.get("data_source_ids") or [],
        chat_history=session.get("chat_history", []),
        intermediate_results=session.get("intermediate_results"),
        last_query=session.get("last_query"),
        created_at=session.get("created_at"),
        updated_at=session.get("updated_at"),
        ttl_seconds=ttl or 0,
    )


@router.delete("/sessions/{session_id}", status_code=204)
async def delete_session(session_id: str, user: dict = Depends(current_user)):
    session = await session_service.get_session(session_id)
    if session is None:
        raise HTTPException(status_code=404, detail="Session not found")
    _check_session_owner(session, user)
    deleted = await session_service.delete_session(session_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="Session not found")
    return None


@router.get("/health/live")
async def health_live():
    """Liveness — process is up and the event loop is turning.

    No dependency checks. Use this for K8s liveness probes / load balancer
    health: if the process can't answer this, the orchestrator should
    restart it.
    """
    return {"status": "alive"}


@router.get("/health/ready")
async def health_ready():
    """Readiness — process can serve real traffic (Redis + main DB reachable).

    Returns 503 if any dependency is down so the load balancer pulls this
    instance out of rotation without restarting it. K8s readiness probe.
    """
    from sqlalchemy import text

    from ..utils.database import get_engine

    redis_ok = await session_service.ping()
    db_ok = True
    try:
        engine = get_engine(None)
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
    except Exception:  # noqa: BLE001
        db_ok = False

    ok = redis_ok and db_ok
    return JSONResponse(
        status_code=200 if ok else 503,
        content={
            "status": "ok" if ok else "degraded",
            "redis": redis_ok,
            "db": db_ok,
            "version": _get_app_version(),
            "uptime_seconds": round(time.monotonic() - _started_at, 3),
        },
    )


@router.get("/health")
async def health_check():
    """Backward-compat alias for /health/ready.

    Pre-Phase 5 monitors hit /health; keep it working. The body matches
    /health/ready so callers don't need to change their parser.
    """
    return await health_ready()
