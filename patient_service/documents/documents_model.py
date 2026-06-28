from pydantic import BaseModel, Field, ConfigDict
from typing import Optional, List
from datetime import datetime
from enum import Enum


# ── Enums ──────────────────────────────────────────────────────────────────────

class DocumentStatus(str, Enum):
    in_progress = "in_progress"
    completed   = "completed"
    failed      = "failed"

class DocumentType(str, Enum):
    prescription       = "prescription"
    lab_report         = "lab_report"
    discharge_summary  = "discharge_summary"
    imaging_report     = "imaging_report"
    other              = "other"

class SupportedLanguage(str, Enum):
    """Indian regional languages supported by both Gemini 2.5 Flash and ElevenLabs TTS."""
    english   = "en"
    hindi     = "hi"
    tamil     = "ta"
    telugu    = "te"
    bengali   = "bn"
    marathi   = "mr"
    gujarati  = "gu"
    kannada   = "kn"
    malayalam = "ml"
    punjabi   = "pa"

# Maps language code → display name used in Gemini prompts
LANGUAGE_DISPLAY_NAMES: dict[str, str] = {
    "en": "English",
    "hi": "Hindi",
    "ta": "Tamil", 
    "te": "Telugu",
    "bn": "Bengali",
    "mr": "Marathi",
    "gu": "Gujarati",
    "kn": "Kannada",
    "ml": "Malayalam",
    "pa": "Punjabi",
}


# ── Clinical Item Models ────────────────────────────────────────────────────────

class MedicationItem(BaseModel):
    model_config = ConfigDict(json_schema_extra={
        "example": {
            "name": "Metformin",
            "dosage": "500mg",
            "frequency": "twice daily after meals",
            "instructions": "take with food, avoid alcohol"
        }
    })
    name:         Optional[str]  = Field(None, description="Medication name")
    dosage:       Optional[str]  = Field(None, description="Dosage amount and unit (e.g. 500mg)")
    frequency:    Optional[str]  = Field(None, description="How often to take (e.g. twice daily after meals)")
    instructions: Optional[str]  = Field(None, description="Special instructions (e.g. take with food, avoid sunlight)")

class AbnormalLabItem(BaseModel):
    model_config = ConfigDict(json_schema_extra={
        "example": {
            "parameter_name": "Haemoglobin",
            "value": "10.2 g/dL",
            "reference_range": "12.0-17.0 g/dL",
            "status": "Low"
        }
    })
    parameter_name:  Optional[str]  = Field(None, description="Lab parameter name (e.g. Haemoglobin, Creatinine)")
    value:           Optional[str]  = Field(None, description="Measured value with unit (e.g. 10.2 g/dL)")
    reference_range: Optional[str]  = Field(None, description="Normal reference range (e.g. 12.0-17.0 g/dL)")
    status:          Optional[str]  = Field(None, description="Deviation status: High, Low, or Critical")


# ── Response Models ─────────────────────────────────────────────────────────────

class DocumentResponse(BaseModel):
    id:                 str                      = Field(..., description="Unique document ID in Firestore")
    file_path:          str                      = Field(..., description="GCS file path (e.g. patients/{uid}/reports/{filename})")
    status:             DocumentStatus           = Field(DocumentStatus.in_progress, description="Processing status of the document")
    type:               DocumentType             = Field(DocumentType.other, description="Detected document category")
    raw_text:           str                      = Field("", description="Extracted raw text from OCR/Document AI")
    summary:            str                      = Field("", description="Empathetic clinical summary in the document's language")
    translated_summary: Optional[str]            = Field(None, description="Translated summary in patient's preferred language")
    created_at:         datetime                 = Field(..., description="Timestamp when the document record was created")
    title:              Optional[str]            = Field(None, description="User-provided or Gemini-generated document title")
    description:        Optional[str]            = Field(None, description="User-provided notes about the document")
    language:           SupportedLanguage        = Field(SupportedLanguage.english, description="Language the summary was generated in")
    consultation_id:    Optional[str]            = Field(None, description="Audio consultation this document was attached to, if any")

    # Enriched clinical data
    doctor_name:        Optional[str]            = Field(None, description="Physician or clinic/hospital name found on the document")
    document_date:      Optional[str]            = Field(None, description="Inferred document or consultation date (YYYY-MM-DD)")
    medications:        List[MedicationItem]     = Field(
        default=[],
        description="Extracted medications with dosage and frequency details",
        examples=[[
            {"name": "Metformin", "dosage": "500mg", "frequency": "twice daily after meals", "instructions": "take with food"},
            {"name": "Amlodipine", "dosage": "5mg", "frequency": "once daily", "instructions": None},
        ]]
    )
    abnormal_labs:      List[AbnormalLabItem]    = Field(
        default=[],
        description="Lab metrics that are outside normal reference ranges",
        examples=[[
            {"parameter_name": "Haemoglobin", "value": "10.2 g/dL", "reference_range": "12.0-17.0 g/dL", "status": "Low"},
            {"parameter_name": "HbA1c", "value": "7.1%", "reference_range": "< 5.7%", "status": "High"},
        ]]
    )
    red_flags:          List[str]                = Field(
        default=[],
        description="Warning signs requiring immediate medical attention",
        examples=[["Chest pain or tightness", "Difficulty breathing", "Sudden vision loss"]]
    )
    actionable_steps:   List[str]                = Field(
        default=[],
        description="Actionable next steps and care instructions for the patient",
        examples=[["Take Metformin 500mg twice daily with meals", "Avoid sugar and refined carbohydrates", "Follow up with endocrinologist in 4 weeks"]]
    )
    warnings:           List[str]                = Field(
        default=[],
        description="Processing warnings for this document (e.g. patient name mismatch, unsupported format).",
    )


class DocumentListItem(BaseModel):
    """Lightweight model for list endpoints — excludes raw_text, full clinical arrays, and summaries."""
    id:             str
    file_path:      str
    status:         DocumentStatus
    type:           DocumentType
    title:          Optional[str]     = None
    description:    Optional[str]     = None
    language:       SupportedLanguage = SupportedLanguage.english
    doctor_name:    Optional[str]     = None
    document_date:  Optional[str]     = None
    consultation_id: Optional[str]   = None
    warnings:       List[str]         = Field(default=[])
    created_at:     datetime


class TranslateSummaryRequest(BaseModel):
    target_language: SupportedLanguage = Field(SupportedLanguage.hindi, description="Target language for translation")

class TranslateSummaryResponse(BaseModel):
    translated_summary: str = Field(..., description="Translated plain-English summary text")
    language:           str = Field(..., description="ISO language code the summary was translated into")

class DeleteDocumentResponse(BaseModel):
    id:      str = Field(..., description="ID of the deleted document")
    message: str = Field(..., description="Confirmation message")
