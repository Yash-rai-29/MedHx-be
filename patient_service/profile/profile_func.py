import datetime
from google.cloud import firestore
from common_code.config import settings
from patient_service.profile.profile_model import (
    PatientProfileResponse,
    PatientProfileUpdateRequest,
    VitalsLogResponse,
    QRPassportResponse,
    FCMTokenUpdateRequest,
    FCMTokenUpdateResponse,
    MealTimes,
    EmergencyContact
)

async def get_patient_profile(uid: str, db: firestore.AsyncClient) -> PatientProfileResponse:
    """Retrieves detailed patient clinical profile records."""
    patient_doc = await db.collection(settings.PATIENTS_COLLECTION).document(uid).get()
    if not patient_doc.exists:
        raise ValueError("Patient clinical profile not found.")
        
    user_doc = await db.collection(settings.USERS_COLLECTION).document(uid).get()
    
    patient_data = patient_doc.to_dict()
    user_data = user_doc.to_dict() if user_doc.exists else {}
    
    # Map dictionary meal times to MealTimes object
    meal_dict = patient_data.get("meal_times", {})
    meal_times = MealTimes(
        breakfast=meal_dict.get("breakfast", "08:30"),
        lunch=meal_dict.get("lunch", "13:30"),
        dinner=meal_dict.get("dinner", "20:30")
    )
    
    # Map emergency contact
    ec_dict = patient_data.get("emergency_contact")
    emergency_contact = EmergencyContact(**ec_dict) if ec_dict else None
    
    # Fetch DOB and location from patient details (with fallback to user_data)
    dob = patient_data.get("date_of_birth") or user_data.get("date_of_birth")
    location = patient_data.get("location") or user_data.get("location")
    
    # If age is not set but DOB is available, calculate it
    age = patient_data.get("age")
    if age is None and dob:
        try:
            dob_date = datetime.datetime.strptime(dob, "%Y-%m-%d").date()
            today = datetime.date.today()
            age = today.year - dob_date.year - ((today.month, today.day) < (dob_date.month, dob_date.day))
        except Exception:
            pass
            
    return PatientProfileResponse(
        uid=user_data.get("uid") or uid,
        name=user_data.get("name"),
        phone=user_data.get("phone"),
        email=user_data.get("email"),
        role=user_data.get("role"),
        language_preference=user_data.get("language_preference"),
        auth_provider=user_data.get("auth_provider"),
        accepted_privacy_policy=user_data.get("accepted_privacy_policy"),
        accepted_terms_of_service=user_data.get("accepted_terms_of_service"),
        blood_group=patient_data.get("blood_group"),
        allergies=patient_data.get("allergies", []),
        chronic_conditions=patient_data.get("chronic_conditions", []),
        current_medications=patient_data.get("current_medications", []),
        past_surgeries=patient_data.get("past_surgeries", []),
        family_history=patient_data.get("family_history", []),
        meal_times=meal_times,
        emergency_contact=emergency_contact,
        age=age,
        gender=patient_data.get("gender"),
        date_of_birth=dob,
        location=location,
        onboarding_status=patient_data.get("onboarding_status") or user_data.get("onboarding_status") or "pending"
    )


async def update_patient_profile(uid: str, req: PatientProfileUpdateRequest, db: firestore.AsyncClient) -> PatientProfileResponse:
    """Updates selected fields in the patient clinical profile records."""
    patient_ref = db.collection(settings.PATIENTS_COLLECTION).document(uid)
    patient_snap = await patient_ref.get()
    
    current_status = "pending"
    if patient_snap.exists:
        current_status = patient_snap.to_dict().get("onboarding_status") or "pending"
        
    update_data = {}
    user_update = {}
    
    # If onboarding is pending, complete it automatically on profile update
    if current_status == "pending":
        update_data["onboarding_status"] = "completed"
        user_update["onboarding_status"] = "completed"
        
    if req.blood_group is not None:
        update_data["blood_group"] = req.blood_group
    if req.allergies is not None:
        update_data["allergies"] = req.allergies
    if req.chronic_conditions is not None:
        update_data["chronic_conditions"] = req.chronic_conditions
    if req.current_medications is not None:
        update_data["current_medications"] = req.current_medications
    if req.past_surgeries is not None:
        update_data["past_surgeries"] = req.past_surgeries
    if req.family_history is not None:
        update_data["family_history"] = req.family_history
    if req.age is not None:
        update_data["age"] = req.age
    if req.gender is not None:
        update_data["gender"] = req.gender
    if req.meal_times is not None:
        update_data["meal_times"] = req.meal_times.model_dump()
    if req.emergency_contact is not None:
        update_data["emergency_contact"] = req.emergency_contact.model_dump()
    if req.date_of_birth is not None:
        update_data["date_of_birth"] = req.date_of_birth
        # Recalculate age automatically if DOB is provided
        try:
            dob_date = datetime.datetime.strptime(req.date_of_birth, "%Y-%m-%d").date()
            today = datetime.date.today()
            update_data["age"] = today.year - dob_date.year - ((today.month, today.day) < (dob_date.month, dob_date.day))
        except Exception:
            pass
    if req.location is not None:
        update_data["location"] = req.location
        
    if update_data:
        await patient_ref.update(update_data)
        # Keep user collection synced too for search/general info
        if req.date_of_birth is not None:
            user_update["date_of_birth"] = req.date_of_birth
        if req.location is not None:
            user_update["location"] = req.location
        if user_update:
            await db.collection(settings.USERS_COLLECTION).document(uid).update(user_update)
        
    return await get_patient_profile(uid, db)

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

async def log_patient_vitals(uid: str, height: float, weight: float, db: firestore.AsyncClient) -> VitalsLogResponse:
    """Logs weight and height, computes local BMI, and appends a vitals document."""
    # BMI = weight (kg) / height^2 (m^2)
    height_meters = height / 100.0
    bmi = round(weight / (height_meters ** 2), 2)
    category = compute_indian_bmi_category(bmi)
    recorded_at = datetime.datetime.utcnow()
    
    vitals_data = {
        "patientId": uid,
        "height": height,
        "weight": weight,
        "bmi": bmi,
        "category": category,
        "recordedAt": recorded_at
    }
    
    # Save log
    doc_ref = await db.collection(settings.VITALS_COLLECTION).add(vitals_data)
    
    # Store current height/weight in profile for faster medication checks
    await db.collection(settings.PATIENTS_COLLECTION).document(uid).update({
        "height": height,
        "weight": weight
    })
    
    return VitalsLogResponse(
        id=doc_ref[1].id,
        height=height,
        weight=weight,
        bmi=bmi,
        category=category,
        recorded_at=recorded_at
    )

async def get_patient_vitals_history(uid: str, db: firestore.AsyncClient) -> list[VitalsLogResponse]:
    """Retrieves weight/BMI vitals history. Skips entries that lack height/weight (extended vitals docs)."""
    docs = await (
        db.collection(settings.VITALS_COLLECTION)
        .where("patientId", "==", uid)
        .order_by("recordedAt", direction=firestore.Query.DESCENDING)
        .get()
    )
    history = []
    for doc in docs:
        d = doc.to_dict()
        # Extended vitals documents (new module) won't have recordedAt or weight/bmi — skip them
        if not d.get("height") or not d.get("weight") or not d.get("recordedAt"):
            continue
        history.append(VitalsLogResponse(
            id=doc.id,
            height=d["height"],
            weight=d["weight"],
            bmi=d.get("bmi", 0.0),
            category=d.get("category", ""),
            recorded_at=d["recordedAt"],
        ))
    return history

async def get_patient_qr_passport(uid: str, db: firestore.AsyncClient) -> QRPassportResponse:
    """Constructs restricted emergency metadata payload for emergency QR scanning."""
    # Fetch identity
    user_doc = await db.collection(settings.USERS_COLLECTION).document(uid).get()
    if not user_doc.exists:
        raise ValueError("User not found.")
    user_data = user_doc.to_dict()
    
    # Fetch medical
    prof = await get_patient_profile(uid, db)
    
    # Resolve the redirect URL dynamically using configured service domain or local fallback
    service_url = settings.SERVICE_URL or "http://localhost:8001"
    qr_redirect_url = f"{service_url}/profile/sos/{uid}"
    
    return QRPassportResponse(
        name=user_data.get("name", "Unknown Patient"),
        blood_group=prof.blood_group,
        allergies=prof.allergies,
        chronic_conditions=prof.chronic_conditions,
        current_medications=prof.current_medications,
        emergency_contact=prof.emergency_contact,
        qr_redirect_url=qr_redirect_url
    )


async def update_fcm_token(
    uid: str,
    req: FCMTokenUpdateRequest,
    db: firestore.AsyncClient
) -> FCMTokenUpdateResponse:
    """
    Stores/updates the FCM registration token for the user's device.
    Tokens are stored platform-wise under the 'fcm_tokens' map:
      users/{uid}.fcm_tokens = { "ios": "...", "android": "...", "web": "..." }
    This lets a single user be reachable on multiple devices simultaneously.
    The 'platform' field is required; if omitted the token is stored under
    the legacy flat key 'fcm_token' as a fallback for backwards-compatibility.
    """
    user_ref = db.collection(settings.USERS_COLLECTION).document(uid)

    if req.platform:
        # Store under the platform-keyed map so multiple devices co-exist
        update_payload = {
            f"fcm_tokens.{req.platform.value}": req.fcm_token
        }
    else:
        # Backwards-compatible flat key when platform is not supplied
        update_payload = {"fcm_token": req.fcm_token}

    await user_ref.update(update_payload)
    return FCMTokenUpdateResponse(success=True)
