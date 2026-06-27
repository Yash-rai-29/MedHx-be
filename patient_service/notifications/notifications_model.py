from pydantic import BaseModel, Field
from typing import Optional, List
from datetime import datetime
from enum import Enum


# ── Enums ──────────────────────────────────────────────────────────────────────

class NotificationType(str, Enum):
    medicine     = "medicine"
    consultation = "consultation"
    report       = "report"
    vitals       = "vitals"
    chat         = "chat"
    general      = "general"

class PushStatus(str, Enum):
    pending          = "pending"
    sent             = "sent"
    failed           = "failed"
    skipped_no_token = "skipped_no_token"
    skipped_disabled = "skipped_disabled"


# ── Response Models ─────────────────────────────────────────────────────────────

class NotificationResponse(BaseModel):
    id:              str                      = Field(..., description="Unique document ID of the notification in Firestore")
    patient_id:      str                      = Field(..., description="The patient Firebase UID associated with this notification")
    title:           str                      = Field(..., description="The brief title of the notification alert")
    body:            str                      = Field(..., description="The main description or content body of the notification")
    deeplink:        Optional[str]            = Field(None, description="In-app navigation deep link routing path (e.g. /reminders/123)")
    is_read:         bool                     = Field(..., description="Indicates whether the user has read the notification")
    created_at:      datetime                 = Field(..., description="The UTC timestamp when the notification was generated")
    type:            NotificationType         = Field(..., description="Category of the notification: medicine, consultation, report, vitals, chat, or general")
    extra_data:      dict                     = Field(default={}, description="Arbitrary payload properties sent alongside the notification")
    push_status:     PushStatus              = Field(..., description="Push notification dispatch status")
    push_message_id: Optional[str]           = Field(None, description="FCM push notification message ID if successfully sent")


class NotificationListResponse(BaseModel):
    notifications: List[NotificationResponse] = Field(..., description="A list of in-app notifications for the patient")
    next_cursor:   Optional[str]              = Field(None, description="Opaque ISO-8601 timestamp cursor to pass as 'before' in the next request to fetch older notifications. Null when no more pages exist.")


class MarkReadResponse(BaseModel):
    success: bool = Field(..., description="Indicates if the read status was successfully updated")
