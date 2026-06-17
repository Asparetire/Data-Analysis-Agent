from __future__ import annotations

import json
import uuid
from datetime import datetime
from pathlib import Path

from fastapi import APIRouter, File, HTTPException, UploadFile
from sse_starlette.sse import EventSourceResponse

from ..config import settings
from ..models.schemas import (
    ChatRequest,
    ChatResponse,
    DataSource,
    DataSourceRename,
    SessionCreateResponse,
    SessionUpdate,
    SessionView,
    UploadResponse,
)
from ..services import chat_service, data_service, metadata_service, session_service, streaming
from ..services.chat_service import SessionBindingError
from ..utils.logger import get_logger

logger = get_logger(__name__)
router = APIRouter()


@router.post("/upload", response_model=UploadResponse)
async def upload_file(file: UploadFile = File(...)):
    file_id = str(uuid.uuid4())
    try:
        await data_service.save_uploaded_file(file, file_id)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    except Exception as e:
        logger.exception("upload failed")
        raise HTTPException(status_code=500, detail=f"Upload failed: {e}") from e

    return UploadResponse(
        file_id=file_id,
        filename=file.filename or "",
        status="success",
        message=f"File {file.filename} uploaded",
    )


@router.post("/chat", response_model=ChatResponse)
async def chat_endpoint(request: ChatRequest):
    try:
        return await chat_service.run_chat(
            session_id=request.session_id,
            message=request.message,
            data_source_id=request.data_source_id,
            data_source_ids=request.data_source_ids,
        )
    except SessionBindingError as e:
        raise HTTPException(status_code=403, detail=str(e)) from e
    except Exception as e:
        logger.exception("chat failed")
        raise HTTPException(status_code=500, detail=f"Chat failed: {e}") from e


@router.post("/chat/stream")
async def chat_stream_endpoint(request: ChatRequest):
    """SSE endpoint for a single chat turn.

    The protocol is documented in services/streaming.py. The response is
    application/x-event-stream and the connection stays open until the run
    finishes or an error event is emitted.

    `ping=15` keeps intermediate proxies (and the browser) from killing the
    connection while the LLM is thinking. `X-Accel-Buffering: no` tells
    nginx-style proxies not to buffer the body -- buffering would defeat
    the whole point of streaming.
    """

    async def event_generator():
        try:
            async for event in streaming.stream_chat(
                session_id=request.session_id,
                message=request.message,
                data_source_id=request.data_source_id,
                data_source_ids=request.data_source_ids,
            ):
                yield event
        except Exception as e:
            # stream_chat is supposed to handle its own errors, but if an
            # exception slips through (e.g. a bug in the generator), surface
            # it as a final `error` event so the frontend sees a clean
            # terminal signal instead of a raw disconnect.
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


@router.get("/datasources", response_model=list[DataSource])
async def get_datasources():
    uploads_dir = Path(settings.DATA_DIR) / "uploads"
    if not uploads_dir.exists():
        return []
    items: list[DataSource] = []
    for path in sorted(uploads_dir.iterdir(), key=lambda p: p.stat().st_mtime, reverse=True):
        if path.suffix.lower() not in {".csv", ".xlsx", ".xls", ".json"}:
            continue
        file_id = path.stem
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
async def rename_datasource(data_source_id: str, body: DataSourceRename):
    """Set a custom display name for a data source.

    The display name is a separate field from the on-disk filename; renaming
    it does not move the underlying file or rewrite the SQLite database.
    Passing an empty string is rejected by the schema (min_length=1).
    """
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
async def preview_datasource(data_source_id: str, limit: int = 5):
    rows = data_service.get_sample_rows(data_source_id, limit=limit)
    if rows is None:
        raise HTTPException(status_code=404, detail="Data source not found or empty")
    return {"rows": rows, "count": len(rows)}


@router.get("/datasources/{data_source_id}/schema")
async def schema_datasource(data_source_id: str):
    rows = data_service.get_sample_rows(data_source_id, limit=1)
    if rows is None:
        raise HTTPException(status_code=404, detail="Data source not found or empty")
    sample = rows[0]
    schema = [{"name": k, "type": _infer_type(v)} for k, v in sample.items()]
    return {"schema": schema}


@router.delete("/datasources/{data_source_id}")
async def delete_datasource(data_source_id: str):
    """Remove the data source and cascade-delete any sessions bound to it."""
    deleted = data_service.delete_data_source(data_source_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="Data source not found")
    # Forget any custom display name we held for this id, otherwise the
    # sidecar would grow stale entries across uploads.
    metadata_service.delete_entry(data_source_id)
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


@router.get("/sessions/{session_id}", response_model=SessionView)
async def get_session(session_id: str):
    session = await session_service.get_session(session_id)
    if session is None:
        raise HTTPException(status_code=404, detail="Session not found or expired")
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
async def create_session():
    session_id = await session_service.create_session()
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
async def update_session(session_id: str, body: SessionUpdate):
    updates = body.model_dump(exclude_unset=True)
    if "data_source_id" in updates and updates["data_source_id"] == "":
        updates["data_source_id"] = None
    if "data_source_ids" in updates and updates["data_source_ids"] is not None:
        # Empty list means "clear all bindings"; treat null as a no-op.
        updates["data_source_ids"] = list(updates["data_source_ids"])
    session = await session_service.update_session(session_id, updates)
    if session is None:
        raise HTTPException(status_code=404, detail="Session not found or expired")
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
async def delete_session(session_id: str):
    deleted = await session_service.delete_session(session_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="Session not found")
    return None


@router.get("/health")
async def health_check():
    redis_ok = await session_service.ping()
    return {
        "status": "ok" if redis_ok else "degraded",
        "redis": redis_ok,
    }
