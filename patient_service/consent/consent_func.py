import random
import datetime
import uuid
from google.cloud import firestore
from common_code.config import settings
from patient_service.consent.consent_model import ConsentGenerateResponse, ConsentRecordResponse

async def generate_consent_otp(uid: str, db: firestore.AsyncClient) -> ConsentGenerateResponse:
    """Generates a random 6-digit access code for doctors to request patient profile access."""
    # Generate 6-digit OTP
    code = f"{random.randint(100000, 999999)}"
    expiry = datetime.datetime.utcnow() + datetime.timedelta(minutes=15)
    
    consent_id = str(uuid.uuid4())
    consent_doc = {
        "id": consent_id,
        "patientId": uid,
        "accessCode": code,
        "scope": "full_history",
        "grantedAt": datetime.datetime.utcnow(),
        "expiresAt": expiry,
        "status": "pending",  # pending input from doctor side
        "createdAt": datetime.datetime.utcnow()
    }
    
    await db.collection(settings.CONSENTS_COLLECTION).document(consent_id).set(consent_doc)
    
    return ConsentGenerateResponse(
        access_code=code,
        expires_at=expiry
    )

async def get_patient_active_grants(uid: str, db: firestore.AsyncClient) -> list[ConsentRecordResponse]:
    """Retrieves all active session grants issued by the patient."""
    now = datetime.datetime.utcnow()
    docs = await db.collection(settings.CONSENTS_COLLECTION) \
        .where("patientId", "==", uid) \
        .where("status", "==", "active") \
        .get()
        
    grants = []
    for doc in docs:
        d = doc.to_dict()
        # Verify expiration manually since firestore where queries are limited
        if d.get("expiresAt") > now:
            grants.append(ConsentRecordResponse(
                id=doc.id,
                patientId=d["patientId"],
                doctorId=d.get("doctorId"),
                scope=d.get("scope", "full_history"),
                granted_at=d.get("grantedAt"),
                expires_at=d.get("expiresAt"),
                status=d.get("status", "active")
            ))
    return grants

async def revoke_consent_grant(uid: str, grant_id: str, db: firestore.AsyncClient) -> bool:
    """Revokes an active profile access grant instantly."""
    doc_ref = db.collection(settings.CONSENTS_COLLECTION).document(grant_id)
    doc_snap = await doc_ref.get()
    
    if not doc_snap.exists:
        raise ValueError("Consent grant not found.")
        
    d = doc_snap.to_dict()
    if d.get("patientId") != uid:
        raise PermissionError("Access is unauthorized.")
        
    await doc_ref.update({"status": "revoked"})
    return True
