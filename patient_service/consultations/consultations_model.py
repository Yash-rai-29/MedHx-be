from pydantic import BaseModel, Field, ConfigDict
from typing import List, Optional
from datetime import datetime
from enum import Enum

from patient_service.documents.documents_model import SupportedLanguage
from patient_service.reminders.reminders_model import ReminderCreateRequest


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
    speaker_id:  Optional[str]   = Field(None, description="Raw speaker ID from diarization (e.g. speaker_0)")
    role:        Optional[str]   = Field(None, description="Inferred speaker role: Doctor, Patient, or Unknown")
    text:        str             = Field(...,  description="Spoken text for this segment")
    start_time:  Optional[float] = Field(None, description="Segment start time in seconds")
    end_time:    Optional[float] = Field(None, description="Segment end time in seconds")


class ExtractedMedicine(BaseModel):
    name:         str           = Field(..., description="Medicine name")
    dosage:       Optional[str] = Field(None, description="Dosage (e.g. 500mg)")
    frequency:    Optional[str] = Field(None, description="How often (e.g. twice daily)")
    instructions: Optional[str] = Field(None, description="Special instructions (e.g. after meals)")
    duration:     Optional[str] = Field(None, description="Duration to take (e.g. 7 days, ongoing)")


# ReminderSuggestion is the same model as ReminderCreateRequest so the frontend
# can pass a suggestion directly to POST /reminders without any transformation.
# Re-exported here for clarity in AudioConsultationResponse.
ReminderSuggestion = ReminderCreateRequest


class AttachedDocument(BaseModel):
    id:    str
    title: Optional[str] = None


class AudioConsultationUploadResponse(BaseModel):
    id:         str
    status:     AudioConsultationStatus
    file_path:  str
    created_at: datetime


class AudioConsultationListItem(BaseModel):
    """Lightweight model for list endpoints — no heavy transcript/segment/medicine data."""
    id:             str
    status:         AudioConsultationStatus
    file_path:      str
    title:          Optional[str]           = None
    language:       SupportedLanguage       = SupportedLanguage.english
    summary:        Optional[str]           = None
    doctor_name:    Optional[str]           = None
    key_diagnoses:  Optional[List[str]]     = None
    error_message:  Optional[str]           = None
    created_at:     datetime


class AudioConsultationResponse(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    id:                    str
    status:                AudioConsultationStatus
    file_path:             str
    title:                 Optional[str]                  = None
    language:              SupportedLanguage              = SupportedLanguage.english
    transcript:            Optional[str]                  = None
    segments:              Optional[List[DiarizedSegment]]     = None
    medicines:             Optional[List[ExtractedMedicine]]   = None
    reminder_suggestions:  Optional[List[ReminderSuggestion]]  = None
    key_diagnoses:         Optional[List[str]]            = None
    summary:               Optional[str]                  = None
    doctor_name:           Optional[str]                  = None
    attached_documents:    Optional[List[AttachedDocument]] = None
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
