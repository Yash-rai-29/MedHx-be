from pydantic import BaseModel, ConfigDict, Field
from typing import List, Optional
from datetime import datetime, UTC
from enum import Enum


class MessageRole(str, Enum):
    user  = "user"
    model = "model"


class ChatRequest(BaseModel):
    prompt: str = Field(
        ...,
        min_length=1,
        max_length=2000,
        description="Health or medical history question for the AI companion",
    )


class ChatCitation(BaseModel):
    model_config = ConfigDict(json_schema_extra={
        "example": {
            "id":       "doc_abc123",
            "title":    "Blood Test Report Jan 2025",
            "filename": "blood_test_jan25.pdf",
            "type":     "lab_report",
        }
    })

    id:       str           = Field(..., description="Document ID — use with GET /documents/{id}")
    title:    str           = Field(..., description="User-defined or Gemini-generated document title")
    filename: Optional[str] = Field(None, description="Original uploaded filename (e.g. blood_test_jan25.pdf)")
    type:     Optional[str] = Field(None, description="Document category (prescription, lab_report, etc.)")


class ChatResponse(BaseModel):
    model_config = ConfigDict(json_schema_extra={
        "example": {
            "reply": "Based on your January blood test, your haemoglobin is within normal range. Please consult your doctor for any medical decisions.",
            "sources": [
                {"id": "doc_abc123", "title": "Blood Test Report Jan 2025", "filename": "blood_test_jan25.pdf", "type": "lab_report"}
            ],
        }
    })

    reply:   str                = Field(..., description="AI companion response (Markdown)")
    sources: List[ChatCitation] = Field(default=[], description="Documents used to ground this response")


class ChatSessionCreateRequest(BaseModel):
    title: Optional[str] = Field(None, max_length=120, description="Optional session title — auto-generated from first message if omitted")


class ChatMessage(BaseModel):
    model_config = ConfigDict(json_schema_extra={
        "example": {
            "role":       "model",
            "content":    "Your HbA1c of 6.1% is near the pre-diabetic threshold. Please discuss this with your doctor.",
            "created_at": "2025-01-15T10:30:00Z",
            "sources": [
                {"id": "doc_abc123", "title": "Blood Test Report Jan 2025", "filename": "blood_test_jan25.pdf", "type": "lab_report"}
            ],
        }
    })

    role:       MessageRole      = Field(..., description="'user' (patient) or 'model' (AI companion)")
    content:    str              = Field(..., description="Message text (Markdown for model turns)")
    created_at: datetime         = Field(default_factory=lambda: datetime.now(UTC), description="Message timestamp (UTC)")
    sources:    List[ChatCitation] = Field(default=[], description="Documents cited in this reply (model turns only; empty for user turns)")


class ChatSessionResponse(BaseModel):
    model_config = ConfigDict(json_schema_extra={
        "example": {
            "id":            "sess_xyz789",
            "patient_id":    "uid_patient001",
            "title":         "HbA1c levels and diet",
            "message_count": 4,
            "created_at":    "2025-01-15T10:00:00Z",
            "updated_at":    "2025-01-15T10:30:00Z",
        }
    })

    id:            str           = Field(..., description="Unique session ID")
    patient_id:    str           = Field(..., description="Owning patient UID")
    title:         Optional[str] = Field(None, description="Session title — null until the first message is sent")
    message_count: int           = Field(0, description="Total messages exchanged in this session")
    created_at:    datetime      = Field(..., description="Session creation timestamp (UTC)")
    updated_at:    datetime      = Field(..., description="Last activity timestamp (UTC)")


class ChatSessionDetailResponse(BaseModel):
    model_config = ConfigDict(json_schema_extra={
        "example": {
            "id":         "sess_xyz789",
            "patient_id": "uid_patient001",
            "title":      "HbA1c levels and diet",
            "created_at": "2025-01-15T10:00:00Z",
            "updated_at": "2025-01-15T10:30:00Z",
            "messages": [
                {
                    "role":       "user",
                    "content":    "What does my HbA1c of 6.1% mean?",
                    "created_at": "2025-01-15T10:15:00Z",
                    "sources":    [],
                },
                {
                    "role":       "model",
                    "content":    "Your HbA1c of 6.1% is near the pre-diabetic threshold. Please discuss this with your doctor.",
                    "created_at": "2025-01-15T10:15:01Z",
                    "sources": [
                        {"id": "doc_abc123", "title": "Blood Test Report Jan 2025", "filename": "blood_test_jan25.pdf", "type": "lab_report"}
                    ],
                },
            ],
        }
    })

    id:         str               = Field(..., description="Unique session ID")
    patient_id: str               = Field(..., description="Owning patient UID")
    title:      Optional[str]     = Field(None, description="Session title — null until the first message is sent")
    created_at: datetime          = Field(..., description="Session creation timestamp (UTC)")
    updated_at: datetime          = Field(..., description="Last activity timestamp (UTC)")
    messages:   List[ChatMessage] = Field(default=[], description="Full conversation history, oldest first")


class DeleteSessionResponse(BaseModel):
    id:      str = Field(..., description="ID of the deleted session")
    message: str = Field(..., description="Confirmation message")
