import datetime
from fastapi import APIRouter, Depends, Header, HTTPException, status, Request
from google.cloud import firestore
from typing import Optional
from common_code.firestore import get_db, log_audit_event
from common_code.firebase_auth import get_current_user
from common_code.config import settings
from patient_service.auth.auth_model import (
    PatientRegisterRequest,
    UserUpdateRequest,
    UserResponse,
    LegalDocumentResponse,
    DeleteAccountRequest,
    DeleteAccountResponse,
    CancelDeleteAccountResponse,
)
from patient_service.auth.auth_func import (
    register_patient_user,
    get_patient_user_by_id,
    update_patient_user,
    schedule_patient_account_deletion,
    cancel_patient_account_deletion,
    execute_scheduled_account_deletion,
)

router = APIRouter()


@router.delete(
    "/delete-account",
    response_model=DeleteAccountResponse,
    summary="Request Account Deletion (48-Hour Grace Period)",
    description=(
        "Initiates a request to permanently delete the patient account and all associated medical records. "
        "A 48-hour grace period is provided during which the user can cancel the request via POST /auth/cancel-delete-account. "
        "After 48 hours, the Cloud Run Job executes and permanently purges all database records, files, and auth credentials."
    ),
    responses={
        200: {"description": "Account deletion scheduled for execution in 48 hours."},
        401: {"description": "Unauthorized or invalid credentials."},
        404: {"description": "User profile not found."},
    },
)
async def request_delete_account(
    req: Optional[DeleteAccountRequest] = None,
    current_token: dict = Depends(get_current_user),
    db: firestore.AsyncClient = Depends(get_db),
):
    uid = current_token.get("uid")
    email = current_token.get("email")
    reason = req.reason if req else None
    try:
        result = await schedule_patient_account_deletion(uid=uid, email=email, reason=reason, db=db)
        return result
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(e))
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to schedule account deletion: {str(e)}",
        )


@router.post(
    "/cancel-delete-account",
    response_model=CancelDeleteAccountResponse,
    summary="Cancel Account Deletion Request",
    description="Cancels an existing pending account deletion request within the 24-hour grace period, keeping the account active.",
    responses={
        200: {"description": "Account deletion request successfully cancelled."},
        400: {"description": "No active deletion request found or invalid state."},
    },
)
async def cancel_delete_account(
    current_token: dict = Depends(get_current_user),
    db: firestore.AsyncClient = Depends(get_db),
):
    uid = current_token.get("uid")
    try:
        result = await cancel_patient_account_deletion(uid=uid, db=db)
        return result
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to cancel account deletion: {str(e)}",
        )


@router.post(
    "/execute-deletion",
    summary="Execute Scheduled Account Deletion (Cloud Tasks)",
    description="Invoked by Google Cloud Tasks after 24 hours to trigger the Cloud Run deletion job if not cancelled.",
    include_in_schema=False,
)
async def execute_deletion(
    payload: dict,
    x_cloud_tasks_secret: Optional[str] = Header(None, alias="X-Cloud-Tasks-Secret"),
    db: firestore.AsyncClient = Depends(get_db),
):
    # Authenticate Cloud Tasks secret in production
    if settings.ENVIRONMENT == "production":
        expected_secret = settings.CLOUD_TASKS_SECRET
        if not expected_secret or x_cloud_tasks_secret != expected_secret:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Unauthorized Cloud Tasks request.",
            )

    patient_id = payload.get("patient_id")
    if not patient_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Missing 'patient_id' in task payload.",
        )

    try:
        result = await execute_scheduled_account_deletion(patient_id=patient_id, db=db)
        return result
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to execute account deletion: {str(e)}",
        )


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

    # Resolve fields dynamically prioritizing request then token properties
    phone_number = req.phone_number or current_token.get("phone_number")
    country_code = req.country_code
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
    req.country_code = country_code
    req.phone_number = phone_number
    req.email = email
    
    auth_provider = current_token.get("firebase", {}).get("sign_in_provider")
        
    try:
        user = await register_patient_user(uid, req, db, auth_provider=auth_provider)
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

@router.patch(
    "/me",
    response_model=UserResponse,
    summary="Update User Account Details",
    description="Updates patient user profile details (name, phone, language preference, date of birth, location). Email cannot be modified.",
)
@router.patch(
    "/account",
    response_model=UserResponse,
    include_in_schema=False,
)
async def update_me(
    req: UserUpdateRequest,
    request: Request,
    current_token: dict = Depends(get_current_user),
    db: firestore.AsyncClient = Depends(get_db),
):
    """Updates demographic and account settings for the authenticated patient user."""
    uid = current_token.get("uid")
    try:
        updated_user = await update_patient_user(uid=uid, req=req, db=db)
        await log_audit_event(
            actor=uid,
            action="UPDATE_PATIENT_ACCOUNT",
            target=uid,
            request=request,
        )
        return updated_user
    except ValueError as e:
        err_msg = str(e)
        status_code = status.HTTP_404_NOT_FOUND if "does not exist" in err_msg.lower() else status.HTTP_400_BAD_REQUEST
        await log_audit_event(
            actor=uid,
            action="UPDATE_PATIENT_ACCOUNT",
            target=uid,
            status="failed",
            details={"error": err_msg},
            request=request,
        )
        raise HTTPException(status_code=status_code, detail=err_msg)
    except Exception as e:
        await log_audit_event(
            actor=uid,
            action="UPDATE_PATIENT_ACCOUNT",
            target=uid,
            status="failed",
            details={"error": str(e)},
            request=request,
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to update user account: {str(e)}",
        )

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

