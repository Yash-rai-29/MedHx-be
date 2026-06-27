import datetime
from fastapi import APIRouter, Depends, HTTPException, status, Request
from fastapi.responses import HTMLResponse
from google.cloud import firestore
from typing import List
from common_code.firestore import get_db, log_audit_event
from common_code.firebase_auth import require_role
from common_code.config import settings
from common_code.templates import render_sos_template
from patient_service.profile.profile_model import (
    PatientProfileResponse,
    PatientProfileUpdateRequest,
    PatientOnboardingRequest,
    OnboardingResponse,
    UserOnboardResponse,
    VitalsLogRequest,
    VitalsLogResponse,
    QRPassportResponse,
    FCMTokenUpdateRequest,
    FCMTokenUpdateResponse
)
from patient_service.profile.profile_func import (
    get_patient_profile,
    update_patient_profile,
    log_patient_vitals,
    get_patient_vitals_history,
    get_patient_qr_passport,
    update_fcm_token
)

router = APIRouter()

# Patient endpoints require the 'patient' role
patient_gate = require_role(["patient"])

@router.get("", response_model=PatientProfileResponse)
async def get_profile(
    current_user: dict = Depends(patient_gate),
    db: firestore.AsyncClient = Depends(get_db)
):
    """Retrieves clinical and contact profile information for the authenticated patient."""
    uid = current_user.get("uid")
    try:
        profile = await get_patient_profile(uid, db)
        return profile
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))

@router.put("", response_model=PatientProfileResponse)
async def update_profile(
    req: PatientProfileUpdateRequest,
    current_user: dict = Depends(patient_gate),
    db: firestore.AsyncClient = Depends(get_db)
):
    """Updates selected fields in the patient clinical profile records."""
    uid = current_user.get("uid")
    try:
        profile = await update_patient_profile(uid, req, db)
        await log_audit_event(actor=uid, action="UPDATE_PROFILE", target=uid)
        return profile
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))

@router.post("/vitals", response_model=VitalsLogResponse, status_code=status.HTTP_201_CREATED)
async def add_vitals(
    req: VitalsLogRequest,
    current_user: dict = Depends(patient_gate),
    db: firestore.AsyncClient = Depends(get_db)
):
    """Logs weight and height vitals, computes BMI with Indian Cut-offs, and appends to the log history."""
    uid = current_user.get("uid")
    try:
        vitals = await log_patient_vitals(uid, req.height, req.weight, db)
        await log_audit_event(actor=uid, action="LOG_VITALS", target=uid, details={"bmi": vitals.bmi})
        return vitals
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))

@router.get("/vitals/history", response_model=List[VitalsLogResponse])
async def get_vitals_history(
    current_user: dict = Depends(patient_gate),
    db: firestore.AsyncClient = Depends(get_db)
):
    """Retrieves list of all logged weight and BMI data trend points over time."""
    uid = current_user.get("uid")
    history = await get_patient_vitals_history(uid, db)
    return history

@router.get("/passport", response_model=QRPassportResponse)
async def get_qr_passport(
    current_user: dict = Depends(patient_gate),
    db: firestore.AsyncClient = Depends(get_db)
):
    """Generates the minimal critical information dataset required to populate the scannable SOS QR Passport card."""
    uid = current_user.get("uid")
    try:
        passport = await get_patient_qr_passport(uid, db)
        await log_audit_event(actor=uid, action="VIEW_QR_PASSPORT", target=uid)
        return passport
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))

@router.post("/onboard", response_model=OnboardingResponse)
async def onboard_patient(
    req: PatientOnboardingRequest,
    request: Request,
    current_user: dict = Depends(patient_gate),
    db: firestore.AsyncClient = Depends(get_db)
):
    """Processes patient onboarding - either skipping or completing clinical data initialization."""
    uid = current_user.get("uid")
    
    if req.skip:
        # Mark onboarding_status as skipped
        await db.collection(settings.USERS_COLLECTION).document(uid).update({"onboarding_status": "skipped"})
        await db.collection(settings.PATIENTS_COLLECTION).document(uid).update({"onboarding_status": "skipped"})
        
        await log_audit_event(actor=uid, action="ONBOARD_USER", target=uid, details={"skipped": True}, request=request)
        
        # Get updated info
        user_doc = await db.collection(settings.USERS_COLLECTION).document(uid).get()
        profile = await get_patient_profile(uid, db)
        
        return OnboardingResponse(
            onboarding_status="skipped",
            profile=profile,
            user=UserOnboardResponse(**user_doc.to_dict())
        )
    
    # Validation for non-skipped onboarding flow
    missing_fields = []
    if not req.gender:
        missing_fields.append("gender")
    if not req.date_of_birth:
        missing_fields.append("date_of_birth")
    if not req.phone:
        missing_fields.append("phone")
    if req.allergies is None:
        missing_fields.append("allergies")
    if not req.height or req.height <= 0:
        missing_fields.append("height")
    if not req.weight or req.weight <= 0:
        missing_fields.append("weight")
        
    if missing_fields:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Validation failed. Missing required onboarding fields: {', '.join(missing_fields)}"
        )
        
    # Validate DOB format
    try:
        dob_date = datetime.datetime.strptime(req.date_of_birth, "%Y-%m-%d").date()
        if dob_date > datetime.date.today():
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Date of birth cannot be in the future."
            )
    except ValueError:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Date of birth must be in YYYY-MM-DD format."
        )

    # 1. Update User Record
    user_update = {"onboarding_status": "completed"}
    if req.name:
        user_update["name"] = req.name
    if req.phone:
        user_update["phone"] = req.phone
    if req.language_preference:
        user_update["language_preference"] = req.language_preference
    if req.date_of_birth:
        user_update["date_of_birth"] = req.date_of_birth
    if req.location:
        user_update["location"] = req.location
        
    await db.collection(settings.USERS_COLLECTION).document(uid).update(user_update)
    
    # Calculate age
    today = datetime.date.today()
    age = today.year - dob_date.year - ((today.month, today.day) < (dob_date.month, dob_date.day))
    
    # 2. Update Patient Profile details
    patient_update = {
        "onboarding_status": "completed",
        "gender": req.gender,
        "date_of_birth": req.date_of_birth,
        "age": age,
        "allergies": req.allergies
    }
    if req.location:
        patient_update["location"] = req.location
    if req.blood_group:
        patient_update["blood_group"] = req.blood_group
    if req.chronic_conditions is not None:
        patient_update["chronic_conditions"] = req.chronic_conditions
    if req.current_medications is not None:
        patient_update["current_medications"] = req.current_medications
    if req.past_surgeries is not None:
        patient_update["past_surgeries"] = req.past_surgeries
    if req.family_history is not None:
        patient_update["family_history"] = req.family_history
    if req.meal_times:
        patient_update["meal_times"] = req.meal_times.model_dump()
    if req.emergency_contact:
        patient_update["emergency_contact"] = req.emergency_contact.model_dump()
        
    await db.collection(settings.PATIENTS_COLLECTION).document(uid).update(patient_update)
    
    # 3. Log Vitals to compute BMI
    await log_patient_vitals(uid, req.height, req.weight, db)
    
    await log_audit_event(actor=uid, action="ONBOARD_USER", target=uid, details={"skipped": False}, request=request)
    
    user_doc = await db.collection(settings.USERS_COLLECTION).document(uid).get()
    profile = await get_patient_profile(uid, db)
    
    return OnboardingResponse(
        onboarding_status="completed",
        profile=profile,
        user=UserOnboardResponse(**user_doc.to_dict())
    )

@router.post("/fcm-token", response_model=FCMTokenUpdateResponse)
async def register_fcm_token(
    req: FCMTokenUpdateRequest,
    current_user: dict = Depends(patient_gate),
    db: firestore.AsyncClient = Depends(get_db)
):
    """Registers or updates the FCM device token for push notifications, keyed by platform."""
    uid = current_user.get("uid")
    try:
        result = await update_fcm_token(uid, req, db)
        await log_audit_event(
            actor=uid,
            action="UPDATE_FCM_TOKEN",
            target=uid,
            details={"platform": req.platform.value if req.platform else None}
        )
        return result
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/sos/{uid}", response_class=HTMLResponse)
async def get_public_sos_card(
    uid: str,
    db: firestore.AsyncClient = Depends(get_db)
):
    """
    Public unauthenticated endpoint to retrieve critical patient SOS metadata.
    Designed for emergency responders scanning the patient's QR SOS card.
    Renders a mobile-friendly, high-contrast HTML card.
    """
    try:
        passport = await get_patient_qr_passport(uid, db)
    except ValueError:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Emergency medical profile not found."
        )
    except Exception as e:
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=str(e))
        
    ec_name = passport.emergency_contact.name if passport.emergency_contact else "Not Configured"
    ec_phone = passport.emergency_contact.phone if passport.emergency_contact else "Not Configured"
    
    html_content = render_sos_template(
        name=passport.name,
        blood_group=passport.blood_group,
        allergies=passport.allergies,
        chronic_conditions=passport.chronic_conditions,
        current_medications=passport.current_medications,
        emergency_contact_name=ec_name,
        emergency_contact_phone=ec_phone
    )
    return HTMLResponse(content=html_content, status_code=200)

