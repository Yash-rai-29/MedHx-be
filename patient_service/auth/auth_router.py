import datetime
from fastapi import APIRouter, Depends, HTTPException, status, Request
from google.cloud import firestore
from typing import Optional
from common_code.firestore import get_db, log_audit_event
from common_code.firebase_auth import get_current_user
from common_code.config import settings
from patient_service.auth.auth_model import PatientRegisterRequest, UserResponse, LegalDocumentResponse
from patient_service.auth.auth_func import register_patient_user, get_patient_user_by_id

router = APIRouter()

@router.post("/register", response_model=UserResponse, status_code=status.HTTP_201_CREATED)
async def register(
    req: PatientRegisterRequest,
    request: Request,
    current_token: dict = Depends(get_current_user),
    db: firestore.AsyncClient = Depends(get_db)
):
    """
    Registers a new patient. The user must first authenticate with Firebase (Email-Pass/Social)
    and pass their Bearer Token to this endpoint to construct their database profiles.
    """
    uid = current_token.get("uid")
    
    # 0. Enforce terms and privacy acceptance
    if not req.accepted_privacy_policy or not req.accepted_terms_of_service:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="You must accept the Privacy Policy and Terms of Service to register."
        )
    
    # Edge case 1: Check if user profile is already registered in Firestore
    user_doc = await db.collection(settings.USERS_COLLECTION).document(uid).get()
    if user_doc.exists:
        await log_audit_event(
            actor=uid,
            action="PATIENT_REGISTRATION",
            target=uid,
            status="failed",
            details={"error": "User profile already registered"},
            request=request
        )
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="User profile is already registered. Please login or update your profile."
        )

    # Resolve fields dynamically prioritizing the token properties (then request overrides)
    phone = current_token.get("phone_number") or req.phone or None
    email = current_token.get("email") or req.email or None
    
    # Resolve name from request first, fall back to Firebase Token, raise error if both are missing
    name = req.name or current_token.get("name")
    if not name or len(name.strip()) < 2:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Full name is required. Please specify a valid 'name' (min 2 chars) in body or ensure your token has it."
        )
    
    # Edge case 2: Validate Date of Birth if provided
    if req.date_of_birth:
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

    # Edge case 3: Validate basic email format if provided
    if email and ("@" not in email or "." not in email.split("@")[-1]):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid email format."
        )

    # Update request object properties to match resolved values
    req.name = name
    req.phone = phone
    req.email = email
    
    auth_provider = current_token.get("firebase", {}).get("sign_in_provider")
        
    try:
        user = await register_patient_user(uid, phone, req, db, auth_provider=auth_provider)
        await log_audit_event(
            actor=uid,
            action="PATIENT_REGISTRATION",
            target=uid,
            request=request
        )
        return user

    except Exception as e:
        await log_audit_event(
            actor=uid,
            action="PATIENT_REGISTRATION",
            target=uid,
            status="failed",
            details={"error": str(e)},
            request=request
        )
        raise HTTPException(status_code=400, detail=str(e))

@router.post("/login", response_model=UserResponse)
async def login(
    request: Request,
    current_token: dict = Depends(get_current_user),
    db: firestore.AsyncClient = Depends(get_db)
):
    """
    Logs in an authenticated patient. Resolves user profile from Firestore users collection.
    If the profile is not registered yet, returns a 404.
    """
    uid = current_token.get("uid")
    
    try:
        user = await get_patient_user_by_id(uid, db)
        await log_audit_event(
            actor=uid,
            action="PATIENT_LOGIN",
            target=uid,
            request=request
        )
        return user
    except ValueError as e:
        await log_audit_event(
            actor=uid,
            action="PATIENT_LOGIN",
            target=uid,
            status="failed",
            details={"error": str(e)},
            request=request
        )
        raise HTTPException(status_code=404, detail="User profile not registered. Call /register first.")

@router.get("/me", response_model=UserResponse)
async def get_me(
    current_token: dict = Depends(get_current_user),
    db: firestore.AsyncClient = Depends(get_db)
):
    """Retrieves user profile data for the active patient session."""
    uid = current_token.get("uid")
    try:
        user = await get_patient_user_by_id(uid, db)
        return user
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))

@router.get("/legal", response_model=list[LegalDocumentResponse])
async def list_legal_documents(
    db: firestore.AsyncClient = Depends(get_db)
):
    """Retrieves all legal document versions stored in Firestore."""
    docs = await db.collection(settings.LEGAL_COLLECTION).get()
    doc_list = [d.to_dict() for d in docs]
    doc_list.sort(key=lambda x: x.get("updated_at"), reverse=True)
    return doc_list

@router.get("/legal/{doc_type}", response_model=LegalDocumentResponse)
async def get_legal_document(
    doc_type: str,
    version: Optional[str] = None,
    db: firestore.AsyncClient = Depends(get_db)
):
    """Retrieves the privacy policy or terms of service document from Firestore in markdown format."""
    if doc_type not in ["privacy_policy", "terms_of_service"]:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Document type must be 'privacy_policy' or 'terms_of_service'"
        )
    
    query = db.collection(settings.LEGAL_COLLECTION).where("doc_type", "==", doc_type)
    if version:
        query = query.where("version", "==", version)
        
    docs = await query.get()
    if not docs:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Legal document of type '{doc_type}' not found."
        )
        
    doc_list = [d.to_dict() for d in docs]
    # Sort by updated_at descending to return the latest version
    doc_list.sort(key=lambda x: x.get("updated_at"), reverse=True)
    return doc_list[0]
