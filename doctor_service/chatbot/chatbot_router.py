from fastapi import APIRouter, Depends, HTTPException, status
from google.cloud import firestore
from common_code.firestore import get_db, log_audit_event
from common_code.firebase_auth import require_role
from doctor_service.chatbot.chatbot_model import DoctorChatRequest, DoctorChatResponse
from doctor_service.chatbot.chatbot_func import answer_doctor_consult_query

router = APIRouter()
doctor_gate = require_role(["doctor"])

@router.post("/consult-assistant", response_model=DoctorChatResponse)
async def consult_assistant(
    req: DoctorChatRequest,
    current_user: dict = Depends(doctor_gate),
    db: firestore.AsyncClient = Depends(get_db)
):
    """Provides a patient-grounded chatbot assistant to help the doctor during a consultation."""
    doctor_uid = current_user.get("uid")
    try:
        response = await answer_doctor_consult_query(doctor_uid, req.patient_id, req.prompt, db)
        await log_audit_event(
            actor=doctor_uid,
            action="CHAT_DOCTOR_ASSISTANT",
            target=req.patient_id
        )
        return response
    except PermissionError as e:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(e))
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=str(e))
