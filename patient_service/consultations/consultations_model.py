from pydantic import BaseModel, Field, ConfigDict
from typing import List, Optional
from datetime import datetime
from enum import Enum

from patient_service.documents.documents_model import SupportedLanguage


# ══════════════════════════════════════════════════════════════
#  Doctor-published consultation models
# ══════════════════════════════════════════════════════════════

class PatientICD11Diagnosis(BaseModel):
    condition: str
    icd11_code: str

class PatientMedicineEntry(BaseModel):
    name: str
    dosage: str
    meal_relation: str
    duration_days: int

class PatientConsultationDetail(BaseModel):
    id: str
    doctor_id: str = Field(..., alias="doctorId")
    patient_id: str = Field(..., alias="patientId")
    status: str
    created_at: datetime = Field(..., alias="createdAt")
    summary_en: Optional[str] = None
    diagnoses: List[PatientICD11Diagnosis] = []
    medicines: List[PatientMedicineEntry] = []
    follow_up_days: int = 0
    pdf_ref: Optional[str] = Field(None, alias="pdfRef")
    pdf_url: Optional[str] = None

    class Config:
        populate_by_name = True

class TranslateSummaryResponse(BaseModel):
    translated_text: str
    language: str


# ══════════════════════════════════════════════════════════════
#  Audio consultation models — patient-uploaded recordings
# ══════════════════════════════════════════════════════════════

class AudioConsultationStatus(str, Enum):
    pending     = "pending"
    in_progress = "in_progress"
    completed   = "completed"
    failed      = "failed"


class DiarizedSegment(BaseModel):
    speaker: str = Field(..., description="Speaker label (e.g. Doctor, Patient, Speaker 1)")
    text:    str = Field(..., description="Spoken text for this segment")


class ExtractedMedicine(BaseModel):
    name:         str           = Field(..., description="Medicine name")
    dosage:       Optional[str] = Field(None, description="Dosage (e.g. 500mg)")
    frequency:    Optional[str] = Field(None, description="How often (e.g. twice daily)")
    instructions: Optional[str] = Field(None, description="Special instructions (e.g. after meals)")
    duration:     Optional[str] = Field(None, description="Duration to take (e.g. 7 days, ongoing)")


class FollowUpSuggestion(BaseModel):
    specialty:             str           = Field(..., description="Medical specialty (e.g. cardiology)")
    reason:                Optional[str] = Field(None, description="Why follow-up is needed")
    suggested_within_days: Optional[int] = Field(None, description="Timeframe in days")


class SuggestedReminderSchedule(BaseModel):
    recurrence:   str                   = Field(..., description="once / daily / weekly / monthly")
    time_of_day:  str                   = Field("09:00", description="HH:MM (24h UTC)")
    days_of_week: Optional[List[int]]   = Field(None, description="0=Mon..6=Sun for weekly")


class ReminderSuggestion(BaseModel):
    title:              str                              = Field(..., description="Reminder title")
    type:               str                              = Field(..., description="medicine or follow_up")
    notes:              Optional[str]                    = None
    medicine_details:   Optional[ExtractedMedicine]      = None
    follow_up_details:  Optional[FollowUpSuggestion]     = None
    suggested_schedule: Optional[SuggestedReminderSchedule] = None


class AudioConsultationUploadResponse(BaseModel):
    id:         str
    status:     AudioConsultationStatus
    file_path:  str
    created_at: datetime


class AudioConsultationResponse(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    id:                    str
    status:                AudioConsultationStatus
    file_path:             str
    language:              SupportedLanguage              = SupportedLanguage.english
    transcript:            Optional[str]                  = None
    segments:              Optional[List[DiarizedSegment]]     = None
    medicines:             Optional[List[ExtractedMedicine]]   = None
    follow_ups:            Optional[List[FollowUpSuggestion]]  = None
    reminder_suggestions:  Optional[List[ReminderSuggestion]]  = None
    key_diagnoses:         Optional[List[str]]            = None
    summary:               Optional[str]                  = None
    doctor_name:           Optional[str]                  = None
    attached_document_ids: Optional[List[str]]            = None
    error_message:         Optional[str]                  = None
    created_at:            datetime


class RefineConsultationRequest(BaseModel):
    prompt: str = Field(
        ...,
        min_length=5,
        description="Plain-text instruction to apply to the consultation. "
                    "e.g. 'Change Metformin dosage to 1000mg' or 'Add that doctor advised low-salt diet'.",
    )


class DeleteAudioConsultationResponse(BaseModel):
    id:      str
    message: str
