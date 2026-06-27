from firebase_admin import auth
from google.cloud import firestore
from common_code.config import settings
from doctor_service.auth.auth_model import DoctorRegisterRequest, DoctorUserResponse

async def register_doctor_user(uid: str, req: DoctorRegisterRequest, db: firestore.AsyncClient) -> DoctorUserResponse:
    """Registers a new doctor: provisions Firestore user record and updates custom Firebase claims."""
    # 1. Update Custom Claims in Firebase Auth
    try:
        auth.set_custom_user_claims(uid, {"role": "doctor"})
    except Exception:
        # Mock safe local development
        pass
        
    user_doc = {
        "uid": uid,
        "name": req.name,
        "phone": req.phone,
        "role": "doctor",
        "verified": False  # requires administrative verification
    }
    
    # 2. Write to Firestore 'users' collection
    await db.collection(settings.USERS_COLLECTION).document(uid).set(user_doc)
    
    # Initialize detailed doctor profile
    doctor_profile = {
        "specialization": req.specialization,
        "registration_number": req.registration_number,
        "verification_status": "pending",
        "average_rating": 5.0,
        "ratings_count": 0
    }
    await db.collection(settings.DOCTORS_COLLECTION).document(uid).set(doctor_profile)
    
    return DoctorUserResponse(
        uid=uid,
        name=req.name,
        phone=req.phone,
        specialization=req.specialization,
        registration_number=req.registration_number,
        role="doctor",
        verified=False
    )

async def get_doctor_user_by_id(uid: str, db: firestore.AsyncClient) -> DoctorUserResponse:
    """Retrieves doctor information from users and doctors collection."""
    user_doc = await db.collection(settings.USERS_COLLECTION).document(uid).get()
    doc_profile = await db.collection(settings.DOCTORS_COLLECTION).document(uid).get()
    
    if not user_doc.exists or not doc_profile.exists:
        raise ValueError("Doctor profile not found.")
        
    u = user_doc.to_dict()
    p = doc_profile.to_dict()
    
    return DoctorUserResponse(
        uid=uid,
        name=u.get("name", "Unknown Doctor"),
        phone=u.get("phone", ""),
        specialization=p.get("specialization", "General"),
        registration_number=p.get("registration_number", "N/A"),
        role="doctor",
        verified=u.get("verified", False)
    )
