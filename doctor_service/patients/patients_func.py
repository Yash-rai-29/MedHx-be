import datetime
from google.cloud import firestore
from common_code.config import settings
from doctor_service.patients.patients_model import PatientLookupResponse

def compute_indian_bmi_category(bmi: float) -> str:
    """
    Computes patient BMI health categories using localized Indian (Asian) standards.
    - Underweight: < 18.5
    - Normal Range: 18.5 - 22.9
    - Overweight: 23.0 - 24.9
    - Obese: >= 25.0
    """
    if bmi < 18.5:
        return "Underweight"
    elif 18.5 <= bmi < 23.0:
        return "Normal"
    elif 23.0 <= bmi < 25.0:
        return "Overweight"
    else:
        return "Obese"

async def lookup_patient_by_consent(
    doctor_uid: str,
    phone: str,
    access_code: str,
    db: firestore.AsyncClient
) -> PatientLookupResponse:
    """
    Validates the patient access OTP, flags the consent grant active for the doctor,
    and returns the clinical medical file of the patient.
    """
    # 1. Resolve patient UID from phone number
    users_ref = db.collection(settings.USERS_COLLECTION)
    user_docs = await users_ref.where("phone", "==", phone).where("role", "==", "patient").get()
    
    if not user_docs:
        raise ValueError("No registered patient found with this phone number.")
    patient_user = user_docs[0].to_dict()
    patient_uid = patient_user["uid"]
    
    # 2. Check for matching, unexpired consent code
    now = datetime.datetime.utcnow()
    consents_ref = db.collection(settings.CONSENTS_COLLECTION)
    consent_docs = await consents_ref \
        .where("patientId", "==", patient_uid) \
        .where("accessCode", "==", access_code) \
        .get()
        
    matching_consent = None
    for doc in consent_docs:
        c_data = doc.to_dict()
        if c_data.get("expiresAt") > now and c_data.get("status") in ["pending", "active"]:
            matching_consent = (doc.id, c_data)
            break
            
    if not matching_consent:
        raise ValueError("Invalid, expired, or revoked access code.")
        
    consent_id, consent_data = matching_consent
    
    # 3. Associate doctor ID and activate the session
    await consents_ref.document(consent_id).update({
        "doctorId": doctor_uid,
        "status": "active",
        # Extend active consult session expiration window (e.g. 1 hour from activation)
        "expiresAt": now + datetime.timedelta(hours=1)
    })
    
    # 4. Gather medical profile
    pat_doc = await db.collection(settings.PATIENTS_COLLECTION).document(patient_uid).get()
    if not pat_doc.exists:
        raise ValueError("Patient medical record profile not initialized.")
    p = pat_doc.to_dict()
    
    # Calculate BMI metrics if height/weight are logged
    height = p.get("height")
    weight = p.get("weight")
    bmi = None
    bmi_category = None
    if height and weight:
        height_m = height / 100.0
        bmi = round(weight / (height_m ** 2), 2)
        bmi_category = compute_indian_bmi_category(bmi)
        
    return PatientLookupResponse(
        patientId=patient_uid,
        name=patient_user.get("name", "Unknown Patient"),
        phone=phone,
        age=p.get("age"),
        gender=p.get("gender"),
        blood_group=p.get("blood_group"),
        allergies=p.get("allergies", []),
        chronic_conditions=p.get("chronic_conditions", []),
        current_medications=p.get("current_medications", []),
        past_surgeries=p.get("past_surgeries", []),
        family_history=p.get("family_history", []),
        height=height,
        weight=weight,
        bmi=bmi,
        bmi_category=bmi_category,
        active_consent_id=consent_id
    )

async def validate_active_session_consent(
    doctor_uid: str,
    patient_id: str,
    db: firestore.AsyncClient
) -> str | None:
    """
    Enforces record-level check. Verifies if there is an active consent session
    authorizing this doctor to view this patient's details.
    Returns the consent document ID if active, otherwise None.
    """
    now = datetime.datetime.utcnow()
    consents_ref = db.collection(settings.CONSENTS_COLLECTION)
    docs = await consents_ref \
        .where("patientId", "==", patient_id) \
        .where("doctorId", "==", doctor_uid) \
        .where("status", "==", "active") \
        .get()
        
    for doc in docs:
        d = doc.to_dict()
        if d.get("expiresAt") > now:
            return doc.id
            
    return None
