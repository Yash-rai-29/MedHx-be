from pydantic import BaseModel, Field, ConfigDict, model_validator, field_validator
from typing import Optional, List
from datetime import datetime, date
from zoneinfo import ZoneInfo
from enum import Enum

_IST = ZoneInfo("Asia/Kolkata")


def _today_ist() -> date:
    """Returns today's date in IST (not UTC). Use this everywhere instead of date.today()
    so that server-side date defaults match the user's calendar day in India."""
    return datetime.now(_IST).date()


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

class MealTiming(str, Enum):
    before_breakfast = "before_breakfast"
    after_breakfast  = "after_breakfast"
    before_lunch     = "before_lunch"
    after_lunch      = "after_lunch"
    before_dinner    = "before_dinner"
    after_dinner     = "after_dinner"
    specific_time    = "specific_time"   # explicit HH:MM — time_of_day required


# ── Sub-models ─────────────────────────────────────────────────────────────────

class ReminderSchedule(BaseModel):
    """Defines when and how often a reminder fires.

    Timing is set via ONE of two mechanisms:
    - meal_timing (e.g. before_breakfast) — time_of_day is resolved at scheduling
      time by reading the patient's profile meal times ± 15 minutes.
    - time_of_day explicitly — meal_timing absent or specific_time.

    start_date is optional so AI-generated suggestions can omit it;
    when None it defaults to today in the validator.
    """
    recurrence:     RecurrenceType
    start_date:     Optional[date]           = Field(None, description="Date the schedule begins (YYYY-MM-DD). Defaults to today when omitted.")
    end_date:       Optional[date]           = Field(None, description="Date the schedule ends (None = indefinite)")
    time_of_day:    Optional[str]            = Field(None, description="HH:MM 24h IST. Required when meal_timing is absent or specific_time.")
    meal_timing:    Optional[MealTiming]     = Field(None, description="Meal-relative timing. Resolved from patient profile at scheduling time.")
    days_of_week:   Optional[List[int]]      = Field(None, description="0=Mon..6=Sun — for weekly recurrence")
    day_of_month:   Optional[int]            = Field(None, ge=1, le=28, description="1-28 — for monthly recurrence")
    specific_dates: Optional[List[date]]     = Field(None, description="Explicit list of dates — for custom_dates")

    @field_validator("meal_timing", mode="before")
    @classmethod
    def _normalize_meal_timing(cls, v):
        # Gemini sometimes returns "before breakfast" instead of "before_breakfast"
        if isinstance(v, str):
            return v.strip().lower().replace(" ", "_")
        return v

    @model_validator(mode="after")
    def _apply_defaults(self) -> "ReminderSchedule":
        # Only default None start_date — never nudge an explicit past date (existing
        # reminders read from Firestore keep their original start_date intact).
        # Use IST today so the date matches the user's calendar day in India, not the
        # server's UTC date (which can be a day behind IST between midnight and 05:30 IST).
        if self.start_date is None:
            self.start_date = _today_ist()
        meal_relative = self.meal_timing and self.meal_timing != MealTiming.specific_time
        if not meal_relative and not self.time_of_day:
            self.time_of_day = "09:00"
        return self


class MedicineReminderDetails(BaseModel):
    name:         str             = Field(..., description="Medicine name")
    dosage:       Optional[str]   = Field(None, description="Dosage amount (e.g. 500mg)")
    frequency:    Optional[str]   = Field(None, description="How often (e.g. twice daily)")
    instructions: Optional[str]   = Field(None, description="Special instructions (e.g. after meals)")

class FollowUpReminderDetails(BaseModel):
    specialty:        Optional[str]  = Field(None, description="Medical specialty (e.g. cardiology)")
    reason:           Optional[str]  = Field(None, description="Reason for the follow-up")
    urgency:          Optional[str]  = Field(None, description="urgent / routine / elective")
    appointment_date: Optional[date] = Field(None, description="Specific appointment date (YYYY-MM-DD)")
    appointment_time: Optional[str]  = Field(None, description="Appointment time HH:MM IST (overrides schedule time_of_day when present)")


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

class ReminderListItem(BaseModel):
    """List-optimised model — omits history fields (trigger_count, last_triggered_at, consultation_id)."""
    model_config = ConfigDict(populate_by_name=True)

    id:                   str
    patientId:            str
    type:                 ReminderType
    status:               ReminderStatus
    title:                str
    notes:                Optional[str]                     = None
    schedule:             Optional[ReminderSchedule]        = None
    medicine_details:     Optional[MedicineReminderDetails] = None
    follow_up_details:    Optional[FollowUpReminderDetails] = None
    notification_enabled: bool                              = True
    next_trigger_at:      Optional[datetime]                = None
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
