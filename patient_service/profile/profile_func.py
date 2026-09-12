import datetime
from datetime import UTC
from google.cloud import firestore
from common_code.config import settings
import urllib.parse
import jwt
from common_code.firestore import log_audit_event
from patient_service.profile.profile_model import (
    DoctorConsultationSummary,
    DoctorDocumentSummary,
    DoctorViewResponse,
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
    emergency_contact = EmergencyContact(**ec_dict) if isinstance(ec_dict, dict) else None
    
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
        country_code=user_data.get("country_code"),
        phone_number=user_data.get("phone_number"),
        email=user_data.get("email"),
        role=user_data.get("role"),
        language_preference=user_data.get("language_preference"),
        auth_provider=user_data.get("auth_provider"),
        accepted_privacy_policy=user_data.get("accepted_privacy_policy"),
        accepted_terms_of_service=user_data.get("accepted_terms_of_service"),
        account_status=user_data.get("account_status") or "active",
        deletion_scheduled_at=user_data.get("deletion_scheduled_at"),
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
    height_meters = height / 100.0
    bmi = round(weight / (height_meters ** 2), 2)
    category = compute_indian_bmi_category(bmi)
    now = datetime.datetime.now(UTC)

    vitals_data = {
        "patientId":   uid,
        "height":      height,
        "weight":      weight,
        "bmi":         bmi,
        "category":    category,
        "measured_at": now,
        "logged_at":   now,
        "vital_types": ["weight_bmi"],
    }

    doc_ref = await db.collection(settings.VITALS_COLLECTION).add(vitals_data)

    # Store current height/weight in profile for faster medication checks
    await db.collection(settings.PATIENTS_COLLECTION).document(uid).set({
        "height": height,
        "weight": weight,
        "bmi":    bmi,
    }, merge=True)

    return VitalsLogResponse(
        id=doc_ref[1].id,
        height=height,
        weight=weight,
        bmi=bmi,
        category=category,
        recorded_at=now,
    )


async def get_patient_vitals_history(uid: str, db: firestore.AsyncClient, limit: int = 90) -> list[VitalsLogResponse]:
    """Retrieves weight/BMI vitals history for the patient."""
    docs = await (
        db.collection(settings.VITALS_COLLECTION)
        .where("patientId", "==", uid)
        .limit(limit)
        .get()
    )

    history: list[VitalsLogResponse] = []
    for doc in docs:
        d = doc.to_dict()
        weight = d.get("weight")
        measured_at = d.get("measured_at") or d.get("recordedAt")
        if weight is None or measured_at is None:
            continue

        history.append(VitalsLogResponse(
            id=doc.id,
            height=d.get("height", 0.0),
            weight=weight,
            bmi=d.get("bmi", 0.0),
            category=d.get("category") or d.get("bmi_category", ""),
            recorded_at=measured_at,
        ))

    history.sort(key=lambda x: x.recorded_at, reverse=True)
    return history[:limit]

def generate_qr_passport_token(uid: str, email: str) -> tuple[str, datetime.datetime]:
    """Generates a secure 30-minute signed token for dynamic QR passport scanning."""
    now = datetime.datetime.now(datetime.UTC)
    expiry = now + datetime.timedelta(minutes=settings.QR_PASSPORT_TOKEN_EXPIRY_MINUTES)
    payload = {
        "sub": uid,
        "email": (email or "").strip().lower(),
        "type": "qr_passport",
        "iat": int(now.timestamp()),
        "exp": int(expiry.timestamp()),
    }
    token = jwt.encode(payload, settings.QR_PASSPORT_SECRET, algorithm="HS256")
    return token, expiry


def verify_qr_passport_token(token: str, email: str) -> dict:
    """
    Decodes and validates the 30-minute QR passport token.
    Enforces signature, expiration, and case-insensitive email match.
    """
    if not token or not email:
        raise ValueError("Token and email are both required for verification.")

    try:
        payload = jwt.decode(
            token,
            settings.QR_PASSPORT_SECRET,
            algorithms=["HS256"],
            options={"require": ["exp", "sub", "email", "type"]},
        )
    except jwt.ExpiredSignatureError:
        raise ValueError("QR passport token has expired (valid for 30 minutes). Please ask the patient to refresh their QR code.")
    except jwt.InvalidTokenError as e:
        raise ValueError(f"Invalid QR passport token: {e}")

    if payload.get("type") != "qr_passport":
        raise ValueError("Invalid token type.")

    token_email = (payload.get("email") or "").strip().lower()
    provided_email = email.strip().lower()
    if token_email and token_email != provided_email:
        raise PermissionError("Email mismatch: the provided email does not match the QR passport token.")

    return payload


async def get_patient_qr_passport(uid: str, db: firestore.AsyncClient) -> QRPassportResponse:
    """Constructs dynamic 30-minute time-limited emergency metadata and URL for QR scanning."""
    # Fetch identity
    user_doc = await db.collection(settings.USERS_COLLECTION).document(uid).get()
    if not user_doc.exists:
        raise ValueError("User not found.")
    user_data = user_doc.to_dict()
    user_email = user_data.get("email") or ""

    # Fetch medical
    prof = await get_patient_profile(uid, db)

    # Generate 30-minute signed token
    token, expires_at = generate_qr_passport_token(uid, user_email)

    # Construct frontend redirect URL pointing to https://medhx-ai.vercel.app
    frontend_base = settings.FRONTEND_WEB_URL.rstrip("/")
    encoded_email = urllib.parse.quote(user_email)
    qr_redirect_url = f"{frontend_base}/doctor-view?token={token}&email={encoded_email}"

    return QRPassportResponse(
        name=user_data.get("name", "Unknown Patient"),
        email=user_email,
        blood_group=prof.blood_group,
        allergies=prof.allergies,
        chronic_conditions=prof.chronic_conditions,
        current_medications=prof.current_medications,
        emergency_contact=prof.emergency_contact,
        token=token,
        token_expires_at=expires_at,
        validity_minutes=settings.QR_PASSPORT_TOKEN_EXPIRY_MINUTES,
        qr_redirect_url=qr_redirect_url,
    )


async def verify_and_get_doctor_view(
    token: str,
    email: str,
    db: firestore.AsyncClient,
) -> DoctorViewResponse:
    """
    Validates 30-minute QR token + email, retrieves full patient clinical summary
    (demographics, baseline, recent vitals, consultations, and test reports) for Doctor Web Portal.
    """
    payload = verify_qr_passport_token(token, email)
    uid = payload["sub"]
    exp_ts = payload["exp"]
    expires_at = datetime.datetime.fromtimestamp(exp_ts, tz=datetime.UTC)
    now = datetime.datetime.now(datetime.UTC)
    remaining_sec = max(0, int((expires_at - now).total_seconds()))

    # 1. Identity & Profile
    user_doc = await db.collection(settings.USERS_COLLECTION).document(uid).get()
    if not user_doc.exists:
        raise ValueError("Patient user record not found.")
    user_data = user_doc.to_dict() or {}
    prof = await get_patient_profile(uid, db)

    # 2. Recent Vitals (up to 5 latest measurements)
    vitals_snaps = await (
        db.collection(settings.VITALS_COLLECTION)
        .where("patientId", "==", uid)
        .order_by("measured_at", direction=firestore.Query.DESCENDING)
        .limit(5)
        .get()
    )
    recent_vitals = []
    for vs in vitals_snaps:
        vd = vs.to_dict()
        vd["id"] = vs.id
        recent_vitals.append(vd)

    # 3. Recent Consultations (up to 5 latest)
    consults_snaps = await (
        db.collection(settings.AUDIO_CONSULTATIONS_COLLECTION)
        .where("patientId", "==", uid)
        .order_by("created_at", direction=firestore.Query.DESCENDING)
        .limit(5)
        .get()
    )
    recent_consultations = []
    for cs in consults_snaps:
        cd = cs.to_dict()
        c_at = cd.get("created_at") or cd.get("createdAt")
        date_str = c_at.strftime("%d %b %Y") if isinstance(c_at, datetime.datetime) else str(c_at or "")[:10]
        recent_consultations.append(
            DoctorConsultationSummary(
                id=cs.id,
                title=cd.get("title") or "Audio Consultation",
                doctor_name=cd.get("doctor_name") or "AI Clinical Assistant",
                date=date_str,
                summary=cd.get("summary") or cd.get("chief_complaint"),
                diagnoses=cd.get("key_diagnoses") or cd.get("diagnoses") or [],
                medications=cd.get("medicines") or cd.get("prescriptions") or [],
            )
        )

    # 4. Recent Documents & Lab Reports (up to 5 latest)
    docs_snaps = await (
        db.collection(settings.DOCUMENTS_COLLECTION)
        .where("patientId", "==", uid)
        .order_by("createdAt", direction=firestore.Query.DESCENDING)
        .limit(5)
        .get()
    )
    recent_documents = []
    for ds in docs_snaps:
        dd = ds.to_dict()
        d_at = dd.get("createdAt") or dd.get("created_at")
        date_str = d_at.strftime("%d %b %Y") if isinstance(d_at, datetime.datetime) else str(d_at or "")[:10]
        recent_documents.append(
            DoctorDocumentSummary(
                id=ds.id,
                title=dd.get("title") or "Medical Document",
                type=dd.get("type"),
                date=date_str,
                summary=dd.get("summary"),
                abnormal_labs=dd.get("abnormal_labs") or [],
            )
        )

    await log_audit_event(
        actor=f"doctor_qr_scan:{email}",
        action="DOCTOR_VIEW_PATIENT_RECORDS",
        target=uid,
        details={"token_exp": exp_ts, "patient_name": user_data.get("name")},
    )

    return DoctorViewResponse(
        valid=True,
        patient_id=uid,
        name=user_data.get("name", "Unknown Patient"),
        email=user_data.get("email"),
        phone=user_data.get("phone"),
        age=prof.age,
        gender=prof.gender,
        blood_group=prof.blood_group,
        emergency_contact=prof.emergency_contact,
        allergies=prof.allergies,
        chronic_conditions=prof.chronic_conditions,
        current_medications=prof.current_medications,
        recent_vitals=recent_vitals,
        recent_consultations=recent_consultations,
        recent_documents=recent_documents,
        token_expires_at=expires_at,
        remaining_seconds=remaining_sec,
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
