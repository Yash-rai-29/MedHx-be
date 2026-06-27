from fastapi import APIRouter, Depends, HTTPException, status
from google.cloud import firestore
from typing import List
from common_code.firestore import get_db, log_audit_event
from common_code.firebase_auth import require_role
from patient_service.ratings.ratings_model import RatingCreateRequest, RatingResponse
from patient_service.ratings.ratings_func import create_doctor_rating, get_patient_ratings_history

router = APIRouter()
patient_gate = require_role(["patient"])

@router.post("", response_model=RatingResponse, status_code=status.HTTP_201_CREATED)
async def submit_rating(
    req: RatingCreateRequest,
    current_user: dict = Depends(patient_gate),
    db: firestore.AsyncClient = Depends(get_db)
):
    """Submits doctor rating and comments after a consultation."""
    patient_id = current_user.get("uid")
    try:
        rating = await create_doctor_rating(patient_id, req, db)
        await log_audit_event(
            actor=patient_id,
            action="SUBMIT_DOCTOR_RATING",
            target=req.doctor_id,
            details={"stars": req.stars, "consultation_id": req.consultation_id}
        )
        return rating
    except PermissionError as e:
        raise HTTPException(status_code=403, detail=str(e))
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

@router.get("/history", response_model=List[RatingResponse])
async def get_ratings_history(
    current_user: dict = Depends(patient_gate),
    db: firestore.AsyncClient = Depends(get_db)
):
    """Retrieves all feedback ratings submitted by the logged-in patient."""
    patient_id = current_user.get("uid")
    ratings = await get_patient_ratings_history(patient_id, db)
    return ratings
