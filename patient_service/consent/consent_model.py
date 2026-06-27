from pydantic import BaseModel, Field
from datetime import datetime
from typing import Optional

class ConsentGenerateResponse(BaseModel):
    access_code: str = Field(..., description="6-digit OTP access code for the doctor")
    expires_at: datetime = Field(..., description="Timestamp when this code expires")

class ConsentRecordResponse(BaseModel):
    id: str
    patientId: str
    doctorId: Optional[str] = None
    scope: str = "full_history"
    granted_at: datetime
    expires_at: datetime
    status: str
