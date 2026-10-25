import uuid
from datetime import datetime
from typing import Any, Dict, List, Optional
from pydantic import BaseModel, Field


# --- Assistants ---
class AssistantCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=255)
    slug: str = Field(..., min_length=1, max_length=100, pattern=r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
    openrouter_api_key: str = Field(..., min_length=5)
    instructions: Optional[Dict[str, Any]] = Field(default_factory=dict)


class AssistantUpdate(BaseModel):
    name: Optional[str] = Field(None, min_length=1, max_length=255)
    openrouter_api_key: Optional[str] = None
    instructions: Optional[Dict[str, Any]] = None


class AssistantResponse(BaseModel):
    id: uuid.UUID
    name: str
    slug: str
    instructions: Dict[str, Any]
    has_api_key: bool
    document_count: int = 0
    created_at: datetime
    updated_at: datetime


# --- Documents ---
class DocumentResponse(BaseModel):
    id: uuid.UUID
    assistant_id: uuid.UUID
    name: str
    current_version_number: int
    file_size: int
    mime_type: str
    processing_status: str
    processing_error: Optional[str] = None
    created_at: datetime
    updated_at: datetime


class DocumentVersionResponse(BaseModel):
    id: uuid.UUID
    document_id: uuid.UUID
    version_number: int
    file_size: int
    mime_type: str
    processing_status: str
    processing_error: Optional[str] = None
    created_at: datetime


# --- Chats & Messages ---
class ChatCreate(BaseModel):
    title: Optional[str] = "New Chat"


class ChatResponse(BaseModel):
    id: uuid.UUID
    assistant_id: uuid.UUID
    title: str
    created_at: datetime
    updated_at: datetime


class MessageCreate(BaseModel):
    content: str = Field(..., min_length=1)


class MessageCitationResponse(BaseModel):
    id: uuid.UUID
    quote: str
    document_chunk_id: uuid.UUID
    document_name: Optional[str] = None
    metadata: Dict[str, Any] = Field(default_factory=dict)
    created_at: datetime


class MessageEventResponse(BaseModel):
    id: uuid.UUID
    sequence_number: int
    event_type: str
    payload: Dict[str, Any] = Field(default_factory=dict)
    created_at: datetime


class MessageResponse(BaseModel):
    id: uuid.UUID
    chat_id: uuid.UUID
    role: str
    content: str
    created_at: datetime
    citations: List[MessageCitationResponse] = Field(default_factory=list)
    events: List[MessageEventResponse] = Field(default_factory=list)


# --- Preferences ---
class UserPreferenceUpdate(BaseModel):
    language: Optional[str] = Field(None, pattern=r"^(en|pt|es)$")
    theme: Optional[str] = Field(None, pattern=r"^(dark|light)$")


class UserPreferenceResponse(BaseModel):
    user_id: uuid.UUID
    language: str
    theme: str
    updated_at: datetime
