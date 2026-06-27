from pydantic import BaseModel, Field
from typing import Optional

class DoctorRegisterRequest(BaseModel):
    name: str = Field(..., min_length=2, description="Doctor's full name")
    phone: str = Field(..., description="Phone number with country code")
    specialization: str = Field(..., description="Specialization (e.g. General Physician, Pediatrician)")
    registration_number: str = Field(..., description="Medical Council registration number")

class DoctorUserResponse(BaseModel):
    uid: str
    name: str
    phone: str
    specialization: str
    registration_number: str
    role: str = "doctor"
    verified: bool = False
