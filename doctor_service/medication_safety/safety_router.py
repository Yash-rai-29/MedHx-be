from fastapi import APIRouter, Depends, HTTPException, status
from google.cloud import firestore
from common_code.firestore import get_db, log_audit_event
from common_code.firebase_auth import require_role
from doctor_service.medication_safety.safety_model import SafetyVerifyRequest, SafetyVerifyResponse
from doctor_service.medication_safety.safety_func import check_medication_safety

router = APIRouter()
doctor_gate = require_role(["doctor"])

@router.post("/verify", response_model=SafetyVerifyResponse)
async def verify_safety(
    req: SafetyVerifyRequest,
    current_user: dict = Depends(doctor_gate),
    db: firestore.AsyncClient = Depends(get_db)
):
    """
    Analyzes the proposed prescription medicines against the patient's existing record
    to identify allergy conflicts, duplicate therapies, or drug-drug interactions.
    """
    doctor_uid = current_user.get("uid")
    try:
        response = await check_medication_safety(doctor_uid, req.patient_id, req.prescribed_medicines, db)
        # Log audit log if conflicts are found to build safety profile audit trails
        if not response.is_safe:
            await log_audit_event(
                actor=doctor_uid,
                action="PRESCRIPTION_SAFETY_FLAG",
                target=req.patient_id,
                details={
                    "allergies_count": len(response.allergy_conflicts),
                    "interactions_count": len(response.drug_interactions),
                    "duplicates_count": len(response.duplicate_therapies)
                }
            )
        return response
    except PermissionError as e:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(e))
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=str(e))
