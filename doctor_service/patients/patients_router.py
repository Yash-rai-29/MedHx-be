from fastapi import APIRouter, Depends, HTTPException, status
from google.cloud import firestore
from common_code.firestore import get_db, log_audit_event
from common_code.firebase_auth import require_role
from doctor_service.patients.patients_model import PatientLookupRequest, PatientLookupResponse
from doctor_service.patients.patients_func import lookup_patient_by_consent

router = APIRouter()
doctor_gate = require_role(["doctor"])

@router.post("/lookup", response_model=PatientLookupResponse)
async def lookup_patient(
    req: PatientLookupRequest,
    current_user: dict = Depends(doctor_gate),
    db: firestore.AsyncClient = Depends(get_db)
):
    """
    Looks up a patient file by phone number and temporary OTP access code.
    If valid, activates the consult access session and returns the patient history records.
    """
    doctor_uid = current_user.get("uid")
    try:
        response = await lookup_patient_by_consent(doctor_uid, req.phone, req.access_code, db)
        await log_audit_event(
            actor=doctor_uid,
            action="PATIENT_LOOKUP_OTP",
            target=response.patientId,
            details={"consent_id": response.active_consent_id}
        )
        return response
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=str(e))
