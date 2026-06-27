from pydantic import BaseModel, Field
from typing import List, Optional
from datetime import datetime

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

class TranslateSummaryRequest(BaseModel):
    target_language: str = Field("hi", description="ISO 639-1 code (e.g. hi, ta, te, mr, bn)")

class TranslateSummaryResponse(BaseModel):
    translated_text: str
    language: str
