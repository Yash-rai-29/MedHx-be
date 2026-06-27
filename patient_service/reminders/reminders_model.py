from pydantic import BaseModel, Field, ConfigDict
from typing import Optional, List
from datetime import datetime, date
from enum import Enum


# ── Enums ──────────────────────────────────────────────────────────────────────

class RecurrenceType(str, Enum):
    once         = "once"
    daily        = "daily"
    weekly       = "weekly"
    monthly      = "monthly"
    custom_dates = "custom_dates"

class ReminderType(str, Enum):
    medicine  = "medicine"
    follow_up = "follow_up"

class ReminderStatus(str, Enum):
    active    = "active"
    paused    = "paused"
    cancelled = "cancelled"
    expired   = "expired"


# ── Sub-models ─────────────────────────────────────────────────────────────────

class ReminderSchedule(BaseModel):
    """Defines when and how often a reminder fires."""
    recurrence:     RecurrenceType
    start_date:     date                    = Field(..., description="Date the schedule begins (YYYY-MM-DD)")
    end_date:       Optional[date]          = Field(None, description="Date the schedule ends (None = indefinite)")
    time_of_day:    str                     = Field("09:00", description="Time to fire in HH:MM (24h, UTC)")
    days_of_week:   Optional[List[int]]     = Field(None, description="0=Mon..6=Sun — for weekly recurrence")
    day_of_month:   Optional[int]           = Field(None, ge=1, le=28, description="1-28 — for monthly recurrence")
    specific_dates: Optional[List[date]]    = Field(None, description="Explicit list of dates — for custom_dates")

class MedicineReminderDetails(BaseModel):
    name:         str             = Field(..., description="Medicine name")
    dosage:       Optional[str]   = Field(None, description="Dosage amount (e.g. 500mg)")
    frequency:    Optional[str]   = Field(None, description="How often (e.g. twice daily)")
    instructions: Optional[str]   = Field(None, description="Special instructions (e.g. after meals)")

class FollowUpReminderDetails(BaseModel):
    specialty: Optional[str] = Field(None, description="Medical specialty (e.g. cardiology)")
    reason:    Optional[str] = Field(None, description="Reason for the follow-up")
    urgency:   Optional[str] = Field(None, description="urgent / routine / elective")


# ── Request Models ─────────────────────────────────────────────────────────────

class ReminderCreateRequest(BaseModel):
    type:                 ReminderType                  = Field(..., description="medicine or follow_up")
    title:                str                           = Field(..., description="Short display title")
    notes:                Optional[str]                 = Field(None, description="Optional patient notes")
    schedule:             ReminderSchedule
    medicine_details:     Optional[MedicineReminderDetails]  = None
    follow_up_details:    Optional[FollowUpReminderDetails]  = None
    consultation_id:      Optional[str]                 = Field(None, description="Source audio consultation ID")
    notification_enabled: bool                          = Field(True)

class BatchReminderCreateRequest(BaseModel):
    reminders: List[ReminderCreateRequest] = Field(..., min_length=1, max_length=50)

class ReminderUpdateRequest(BaseModel):
    title:                Optional[str]            = None
    notes:                Optional[str]            = None
    status:               Optional[ReminderStatus] = Field(None, description="paused / active (resume)")
    schedule:             Optional[ReminderSchedule] = None
    notification_enabled: Optional[bool]           = None


# ── Response Models ────────────────────────────────────────────────────────────

class ReminderResponse(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    id:                   str
    patientId:            str
    type:                 ReminderType
    status:               ReminderStatus
    title:                str
    notes:                Optional[str]                      = None
    schedule:             ReminderSchedule
    medicine_details:     Optional[MedicineReminderDetails]  = None
    follow_up_details:    Optional[FollowUpReminderDetails]  = None
    consultation_id:      Optional[str]                      = None
    notification_enabled: bool                               = True
    next_trigger_at:      Optional[datetime]                 = None
    last_triggered_at:    Optional[datetime]                 = None
    trigger_count:        int                                = 0
    created_at:           datetime

class BatchReminderCreateResponse(BaseModel):
    created: List[ReminderResponse]
    failed:  List[dict]

class DeleteReminderResponse(BaseModel):
    id:      str = Field(...)
    message: str = Field(...)


# ── Cloud Tasks callback payload ───────────────────────────────────────────────

class TriggerPayload(BaseModel):
    reminder_id: str
    patient_id:  str
    target_at:   str   # ISO datetime string (UTC)
    type:        str   # "notify" | "relay"


# ── Legacy models kept for backward compat ────────────────────────────────────

class PubSubMessage(BaseModel):
    data:      str = Field(..., description="Base64-encoded payload")
    messageId: str

class PubSubEnvelope(BaseModel):
    message:      PubSubMessage
    subscription: str
