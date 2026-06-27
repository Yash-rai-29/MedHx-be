from pydantic import BaseModel, Field
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


# ── Response Models ─────────────────────────────────────────────────────────────

class DocumentResponse(BaseModel):
    id:                 str                    = Field(..., description="Unique document ID in Firestore")
    file_path:          str                    = Field(..., description="GCS file path reference (e.g. patients/{uid}/reports/{filename})")
    status:             DocumentStatus         = Field(DocumentStatus.in_progress, description="Processing status of the document")
    type:               DocumentType           = Field(DocumentType.other, description="Detected document category")
    raw_text:           str                    = Field("", description="Extracted raw text from OCR/Document AI parsing")
    summary:            str                    = Field("", description="Empathetic clinical summary of the document in layman English")
    translated_summary: Optional[str]          = Field(None, description="Translated summary in patient's preferred language, if requested")
    created_at:         datetime               = Field(..., description="Timestamp when the document record was created")
    title:              Optional[str]          = Field(None, description="User-provided optional title of the document")
    description:        Optional[str]          = Field(None, description="User-provided optional description/notes of the document")

    # Enriched clinical data variables
    doctor_name:        Optional[str]          = Field(None, description="Name of the physician or clinic/hospital found on the document")
    document_date:      Optional[str]          = Field(None, description="Inferred document or consultation date (YYYY-MM-DD)")
    medications:        List[dict]             = Field(default=[], description="Extracted medications list with dosage and frequency details")
    abnormal_labs:      List[dict]             = Field(default=[], description="Extracted lab metrics that are out of reference ranges")
    red_flags:          List[str]              = Field(default=[], description="Warning signs or red flags requiring immediate medical attention")
    actionable_steps:   List[str]              = Field(default=[], description="Actionable next steps and care instructions for the patient")


class TranslateSummaryRequest(BaseModel):
    target_language: str = Field("hi", description="ISO language code to translate into (e.g., hi, ta, te)")

class TranslateSummaryResponse(BaseModel):
    translated_summary: str = Field(..., description="Translated plain-English summary text")
    language:           str = Field(..., description="ISO language code the summary was translated into")
