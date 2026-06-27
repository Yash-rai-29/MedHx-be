from pydantic import BaseModel, Field
from typing import List, Optional

class StartConsultationRequest(BaseModel):
    patient_id: str = Field(..., description="Target patient unique ID")

class SignedAudioUrlResponse(BaseModel):
    upload_url: str = Field(..., description="GCS signed URL to upload consultation audio")
    file_path: str = Field(..., description="Target storage path of the audio file")

class DiarizedSegment(BaseModel):
    speaker: str
    text: str

class TranscriptionResponse(BaseModel):
    full_text: str
    segments: List[DiarizedSegment]

class ICD11Diagnosis(BaseModel):
    condition: str = Field(..., description="Extracted clinical diagnosis name")
    icd11_code: str = Field(..., description="WHO ICD-11 Interoperability code (e.g. CA01.0)")

class MedicineEntry(BaseModel):
    name: str = Field(..., description="Medicine brand or generic name")
    dosage: str = Field(..., description="Dosage instructions (e.g., 1 tablet twice daily)")
    meal_relation: str = Field("AFTER_FOOD", description="BEFORE_FOOD, AFTER_FOOD, WITH_FOOD, NONE")
    duration_days: int = Field(5, description="Number of days to take")

class ExtractionResponse(BaseModel):
    symptoms: List[str] = []
    diagnoses: List[ICD11Diagnosis] = []
    medicines: List[MedicineEntry] = []
    follow_up_days: int = 0

class ReviewConsultationRequest(BaseModel):
    diagnoses: List[ICD11Diagnosis]
    medicines: List[MedicineEntry]
    follow_up_days: int
    summary: str = Field(..., description="Doctor's confirmation summary of the visit")

class PublishReportResponse(BaseModel):
    pdf_download_url: str
    message: str = "Report published and reminders dispatched."
