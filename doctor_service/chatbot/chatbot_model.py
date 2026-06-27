from pydantic import BaseModel, Field

class DoctorChatRequest(BaseModel):
    patient_id: str = Field(..., description="Target patient unique ID currently open in doctor's workspace")
    prompt: str = Field(..., description="Query about patient's history or clinical records")

class DoctorChatResponse(BaseModel):
    reply: str = Field(..., description="AI generated clinical assistant response")
