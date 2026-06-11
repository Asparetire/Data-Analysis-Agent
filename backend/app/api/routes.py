from __future__ import annotations

import uuid
from datetime import datetime
from pathlib import Path
from typing import List

from fastapi import APIRouter, File, HTTPException, UploadFile

from ..agents.graph import build_graph, initial_state
from ..config import settings
from ..models.schemas import (
    ChatRequest,
    ChatResponse,
    DataSource,
    UploadResponse,
)
from ..services import data_service
from ..utils.logger import get_logger

logger = get_logger(__name__)
router = APIRouter()


@router.post("/upload", response_model=UploadResponse)
async def upload_file(file: UploadFile = File(...)):
    file_id = str(uuid.uuid4())
    try:
        await data_service.save_uploaded_file(file, file_id)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.exception("upload failed")
        raise HTTPException(status_code=500, detail=f"Upload failed: {e}")

    return UploadResponse(
        file_id=file_id,
        filename=file.filename or "",
        status="success",
        message=f"File {file.filename} uploaded",
    )


@router.post("/chat", response_model=ChatResponse)
async def chat_endpoint(request: ChatRequest):
    try:
        graph = build_graph(request.data_source_id)
        state = initial_state(
            session_id=request.session_id,
            message=request.message,
            data_source_id=request.data_source_id,
        )
        result = await graph.ainvoke(state)
    except Exception as e:
        logger.exception("chat failed")
        raise HTTPException(status_code=500, detail=f"Chat failed: {e}")

    messages = result.get("messages") or []
    final = messages[-1] if messages else None
    final_content = getattr(final, "content", "") if final is not None else ""

    sql_query = None
    for msg in reversed(messages):
        if hasattr(msg, "tool_calls") and msg.tool_calls:
            for tc in msg.tool_calls:
                if tc.get("name") == "query_database":
                    args = tc.get("args") or {}
                    sql_query = args.get("sql_query")
                    break
            if sql_query:
                break

    return ChatResponse(
        session_id=request.session_id,
        message=final_content or "",
        chart_data=result.get("chart_data"),
        sql_query=sql_query,
        error=result.get("error"),
    )


@router.get("/datasources", response_model=List[DataSource])
async def get_datasources():
    uploads_dir = Path(settings.DATA_DIR) / "uploads"
    if not uploads_dir.exists():
        return []
    items: List[DataSource] = []
    for path in sorted(uploads_dir.iterdir(), key=lambda p: p.stat().st_mtime, reverse=True):
        if path.suffix.lower() not in {".csv", ".xlsx", ".xls", ".json"}:
            continue
        file_id = path.stem
        ext = path.suffix.lower().lstrip(".")
        ds_type = "csv" if ext == "csv" else "excel" if ext in {"xlsx", "xls"} else "json"
        stat = path.stat()
        items.append(
            DataSource(
                id=file_id,
                name=path.name,
                type=ds_type,
                created_at=datetime.fromtimestamp(stat.st_mtime),
            )
        )
    return items


@router.get("/datasources/{data_source_id}/preview")
async def preview_datasource(data_source_id: str, limit: int = 5):
    rows = data_service.get_sample_rows(data_source_id, limit=limit)
    if rows is None:
        raise HTTPException(status_code=404, detail="Data source not found or empty")
    return {"rows": rows, "count": len(rows)}


@router.get("/health")
async def health_check():
    return {"status": "ok"}
