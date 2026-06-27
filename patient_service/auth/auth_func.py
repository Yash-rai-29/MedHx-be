import datetime
from firebase_admin import auth
from google.cloud import firestore
from common_code.config import settings
from patient_service.auth.auth_model import PatientRegisterRequest, UserResponse

async def register_patient_user(uid: str, phone: str | None, req: PatientRegisterRequest, db: firestore.AsyncClient, auth_provider: str | None = None) -> UserResponse:
    """
    Registers a new patient: creates Firestore user record and updates custom Firebase claims.
    """
    # 1. Update Custom Claims in Firebase Auth to assign the 'patient' role
    try:
        auth.set_custom_user_claims(uid, {"role": "patient"})
    except Exception as e:
        # For mock local environment, log error and proceed
        pass
        
    user_doc = {
        "uid": uid,
        "name": req.name,
        "phone": phone,
        "email": req.email,
        "role": "patient",
        "language_preference": req.language_preference,
        "date_of_birth": req.date_of_birth,
        "location": req.location,
        "onboarding_status": "pending",
        "accepted_privacy_policy": req.accepted_privacy_policy,
        "accepted_terms_of_service": req.accepted_terms_of_service,
        "accepted_at": datetime.datetime.now(datetime.UTC),
        "auth_provider": auth_provider
    }

    
    # 2. Write to Firestore 'users' collection
    await db.collection(settings.USERS_COLLECTION).document(uid).set(user_doc)
    
    # Calculate age from DOB if provided
    age = None
    if req.date_of_birth:
        try:
            dob_date = datetime.datetime.strptime(req.date_of_birth, "%Y-%m-%d").date()
            today = datetime.date.today()
            age = today.year - dob_date.year - ((today.month, today.day) < (dob_date.month, dob_date.day))
        except Exception:
            pass

    # Initialize empty 'patients' detail document too
    patient_doc = {
        "allergies": [],
        "chronic_conditions": [],
        "current_medications": [],
        "blood_group": None,
        "past_surgeries": [],
        "family_history": [],
        "meal_times": {
            "breakfast": "08:30",
            "lunch": "13:30",
            "dinner": "20:30"
        },
        "emergency_contact": None,
        "age": age,
        "gender": None,
        "date_of_birth": req.date_of_birth,
        "location": req.location,
        "onboarding_status": "pending"
    }
    await db.collection(settings.PATIENTS_COLLECTION).document(uid).set(patient_doc)
    
    return UserResponse(**user_doc)

async def get_patient_user_by_id(uid: str, db: firestore.AsyncClient) -> UserResponse:
    """Gets patient user profile information from users collection."""
    doc = await db.collection(settings.USERS_COLLECTION).document(uid).get()
    if not doc.exists:
        raise ValueError("User profile does not exist.")
    return UserResponse(**doc.to_dict())
