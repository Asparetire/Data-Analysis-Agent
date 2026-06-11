from pydantic import BaseModel
from typing import Optional, List, Any
from datetime import datetime

class ChatMessage(BaseModel):
    role: str  # "user" | "assistant"
    content: str
    timestamp: datetime = datetime.now()

class ChatRequest(BaseModel):
    session_id: str
    message: str
    data_source_id: Optional[str] = None

class ChatResponse(BaseModel):
    session_id: str
    message: str
    chart_data: Optional[Any] = None
    sql_query: Optional[str] = None
    error: Optional[str] = None

class UploadResponse(BaseModel):
    file_id: str
    filename: str
    status: str
    message: str

class DataSource(BaseModel):
    id: str
    name: str
    type: str  # "csv" | "excel" | "database"
    created_at: datetime