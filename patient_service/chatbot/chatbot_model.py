from pydantic import BaseModel, Field
from typing import List, Optional
from datetime import datetime

class ChatRequest(BaseModel):
    prompt: str = Field(..., description="The medical/health question to ask the AI companion")

class ChatResponse(BaseModel):
    reply: str = Field(..., description="The generated response from the AI companion")
    sources: List[str] = Field(default=[], description="Source report filenames reference used to answer the question")

class ChatSessionCreateRequest(BaseModel):
    title: Optional[str] = Field(None, description="Optional custom title for the chat session")

class ChatMessage(BaseModel):
    role: str = Field(..., description="Sender role: 'user' (patient) or 'model' (AI companion)")
    content: str = Field(..., description="Text content of the message")
    created_at: datetime = Field(default_factory=datetime.utcnow, description="Timestamp when the message was sent")
    sources: List[str] = Field(default=[], description="Source report filenames referenced for the AI reply")

class ChatSessionResponse(BaseModel):
    id: str = Field(..., description="Unique chat session ID")
    patient_id: str = Field(..., description="Patient User ID owning this session")
    title: str = Field(..., description="Descriptive title of the chat session")
    created_at: datetime = Field(..., description="Timestamp when the session was created")
    updated_at: datetime = Field(..., description="Timestamp when the session was last updated")

class ChatSessionDetailResponse(BaseModel):
    id: str = Field(..., description="Unique chat session ID")
    patient_id: str = Field(..., description="Patient User ID owning this session")
    title: str = Field(..., description="Descriptive title of the chat session")
    created_at: datetime = Field(..., description="Timestamp when the session was created")
    updated_at: datetime = Field(..., description="Timestamp when the session was last updated")
    messages: List[ChatMessage] = Field(default=[], description="List of messages exchanged in this conversation session")
