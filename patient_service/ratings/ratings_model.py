from pydantic import BaseModel, Field
from typing import Optional
from datetime import datetime

class RatingCreateRequest(BaseModel):
    doctor_id: str = Field(..., description="Unique ID of the doctor being rated")
    consultation_id: str = Field(..., description="Associated consultation ID")
    stars: int = Field(..., ge=1, le=5, description="Star rating (1 to 5)")
    comments: Optional[str] = Field(None, description="Optional patient feedback or comments")

class RatingResponse(BaseModel):
    id: str
    patient_id: str
    doctor_id: str
    consultation_id: str
    stars: int
    comments: Optional[str] = None
    created_at: datetime
