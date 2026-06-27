from pydantic import BaseModel, Field
from typing import Optional
from enum import Enum


# ── Enums ──────────────────────────────────────────────────────────────────────

class ReminderType(str, Enum):
    medicine   = "medicine"
    follow_up  = "follow-up"

class MealRelation(str, Enum):
    before_food = "BEFORE_FOOD"
    after_food  = "AFTER_FOOD"
    with_food   = "WITH_FOOD"
    none        = "NONE"

class ReminderStatus(str, Enum):
    active    = "active"
    completed = "completed"
    skipped   = "skipped"


# ── Response & Request Models ───────────────────────────────────────────────────

class ReminderResponse(BaseModel):
    id:                   str             = Field(..., description="Unique reminder ID in Firestore")
    patientId:            str             = Field(..., description="Unique Firebase User ID of the patient")
    type:                 ReminderType    = Field(..., description="Reminder category: medicine or follow-up")
    title:                str             = Field(..., description="Details of the medicine or follow-up activity")
    schedule_time:        str             = Field(..., description="Actual absolute alarm time (HH:MM)")
    meal_relation:        MealRelation    = Field(MealRelation.none, description="Relationship to meal timing")
    notification_enabled: bool            = Field(True, description="Whether notification alert is enabled for this reminder")
    status:               ReminderStatus  = Field(ReminderStatus.active, description="Current lifecycle status of the reminder")
    consultationId:       Optional[str]   = Field(None, description="Optional ID referencing the consultation this reminder was generated from")
    target_date:          Optional[str]   = Field(None, description="Optional target date in YYYY-MM-DD format")

class ReminderCreateRequest(BaseModel):
    type:                 ReminderType    = Field(..., description="Reminder category: medicine or follow-up")
    title:                str             = Field(..., description="Details of the medicine or follow-up activity")
    schedule_time:        str             = Field(..., description="Alarm time in HH:MM format (24-hour)")
    meal_relation:        MealRelation    = Field(MealRelation.none, description="Relationship to meal: BEFORE_FOOD, AFTER_FOOD, WITH_FOOD, NONE")
    notification_enabled: bool            = Field(True, description="Whether notification alert is enabled for this reminder")
    target_date:          Optional[str]   = Field(None, description="Optional target date in YYYY-MM-DD format")

class ReminderUpdateRequest(BaseModel):
    notification_enabled: Optional[bool]           = Field(None, description="Toggle whether notification alerts are enabled")
    status:               Optional[ReminderStatus] = Field(None, description="New lifecycle status of the reminder")
    schedule_time:        Optional[str]             = Field(None, description="New alarm time in HH:MM format (24-hour)")

class TriggerNotificationRequest(BaseModel):
    reminder_id: str = Field(..., description="Unique reminder ID to trigger notification for")
    patient_id:  str = Field(..., description="Unique Firebase User ID of the patient")

class TriggerNotificationResponse(BaseModel):
    success: bool = Field(..., description="True if push was dispatched successfully")

class PubSubMessage(BaseModel):
    data:      str = Field(..., description="Base64 encoded payload string")
    messageId: str = Field(..., description="Unique message identifier")

class PubSubEnvelope(BaseModel):
    message:      PubSubMessage
    subscription: str
