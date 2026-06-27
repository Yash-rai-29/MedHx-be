from pydantic import BaseModel, Field
from typing import List
from doctor_service.consultations.consultations_model import MedicineEntry

class SafetyVerifyRequest(BaseModel):
    patient_id: str = Field(..., description="Target patient unique ID")
    prescribed_medicines: List[MedicineEntry] = Field(..., description="List of medicines the doctor intends to prescribe")

class AllergyConflictDetail(BaseModel):
    medicine_name: str
    allergy: str
    severity: str
    message: str

class DrugInteractionDetail(BaseModel):
    medicine_a: str
    medicine_b: str
    severity: str
    message: str

class DuplicateTherapyDetail(BaseModel):
    medicine_name: str
    existing_medicine: str
    severity: str
    message: str

class DosingWarningDetail(BaseModel):
    medicine_name: str
    type: str
    severity: str
    message: str

class SafetyVerifyResponse(BaseModel):
    is_safe: bool
    allergy_conflicts: List[AllergyConflictDetail] = []
    drug_interactions: List[DrugInteractionDetail] = []
    duplicate_therapies: List[DuplicateTherapyDetail] = []
    dosing_warnings: List[DosingWarningDetail] = []
