from fastapi import APIRouter, Depends, HTTPException, status
from google.cloud import firestore
from common_code.firestore import get_db, log_audit_event
from common_code.firebase_auth import get_current_user
from doctor_service.auth.auth_model import DoctorRegisterRequest, DoctorUserResponse
from doctor_service.auth.auth_func import register_doctor_user, get_doctor_user_by_id

router = APIRouter()

@router.post("/register", response_model=DoctorUserResponse, status_code=status.HTTP_201_CREATED)
async def register(
    req: DoctorRegisterRequest,
    current_token: dict = Depends(get_current_user),
    db: firestore.AsyncClient = Depends(get_db)
):
    """Registers a doctor. The user must first authenticate with Firebase to retrieve a JWT token."""
    uid = current_token.get("uid")
    try:
        doctor = await register_doctor_user(uid, req, db)
        await log_audit_event(actor=uid, action="DOCTOR_REGISTRATION", target=uid)
        return doctor
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))

@router.get("/me", response_model=DoctorUserResponse)
async def get_me(
    current_token: dict = Depends(get_current_user),
    db: firestore.AsyncClient = Depends(get_db)
):
    """Gets details of the currently authenticated doctor user."""
    uid = current_token.get("uid")
    try:
        doctor = await get_doctor_user_by_id(uid, db)
        return doctor
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
