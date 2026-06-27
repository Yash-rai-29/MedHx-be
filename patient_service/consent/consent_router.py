from fastapi import APIRouter, Depends, HTTPException, status
from google.cloud import firestore
from typing import List
from common_code.firestore import get_db, log_audit_event
from common_code.firebase_auth import require_role
from patient_service.consent.consent_model import ConsentGenerateResponse, ConsentRecordResponse
from patient_service.consent.consent_func import (
    generate_consent_otp,
    get_patient_active_grants,
    revoke_consent_grant
)

router = APIRouter()
patient_gate = require_role(["patient"])

@router.post("/generate", response_model=ConsentGenerateResponse, status_code=status.HTTP_201_CREATED)
async def generate_otp(
    current_user: dict = Depends(patient_gate),
    db: firestore.AsyncClient = Depends(get_db)
):
    """Generates a temporary 6-digit access token (OTP) valid for 15 minutes to share with the doctor."""
    uid = current_user.get("uid")
    try:
        otp_resp = await generate_consent_otp(uid, db)
        await log_audit_event(actor=uid, action="GENERATE_CONSENT_OTP", target=uid)
        return otp_resp
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@router.get("/active", response_model=List[ConsentRecordResponse])
async def get_active_grants(
    current_user: dict = Depends(patient_gate),
    db: firestore.AsyncClient = Depends(get_db)
):
    """Retrieves all current active doctor sessions that have access permissions to this patient's medical records."""
    uid = current_user.get("uid")
    grants = await get_patient_active_grants(uid, db)
    return grants

@router.delete("/{grant_id}", status_code=status.HTTP_200_OK)
async def revoke_grant(
    grant_id: str,
    current_user: dict = Depends(patient_gate),
    db: firestore.AsyncClient = Depends(get_db)
):
    """Instantly revokes a doctor's active authorization to read records."""
    uid = current_user.get("uid")
    try:
        await revoke_consent_grant(uid, grant_id, db)
        await log_audit_event(actor=uid, action="REVOKE_CONSENT_GRANT", target=grant_id)
        return {"status": "success", "message": "Consent grant successfully revoked."}
    except PermissionError as e:
        raise HTTPException(status_code=403, detail=str(e))
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
