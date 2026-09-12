import datetime
import logging
from firebase_admin import auth
from google.cloud import firestore
from common_code.config import settings
from common_code.cloud_tasks import create_cloud_task, delete_cloud_task
from common_code.firestore import log_audit_event
from patient_service.auth.auth_model import PatientRegisterRequest, UserResponse, UserUpdateRequest

logger = logging.getLogger(__name__)


async def trigger_account_deletion_job(patient_id: str) -> None:
    """
    Triggers Cloud Run Job 'account-deletion-job' for a specific patient
    or executes via async coroutine fallback.
    """
    if settings.ENVIRONMENT == "production":
        try:
            from google.cloud import run_v2
            client = run_v2.JobsClient()
            job_name = f"projects/{settings.GCP_PROJECT_ID}/locations/{settings.CLOUD_RUN_JOB_REGION}/jobs/{settings.CLOUD_RUN_DELETION_JOB_NAME}"

            request = run_v2.RunJobRequest(
                name=job_name,
                overrides=run_v2.RunJobRequest.Overrides(
                    container_overrides=[
                        run_v2.RunJobRequest.Overrides.ContainerOverride(
                            env=[
                                run_v2.EnvVar(name="PATIENT_ID", value=patient_id),
                            ]
                        )
                    ]
                ),
            )
            operation = client.run_job(request=request)
            logger.info(f"Cloud Run Job 'account-deletion-job' triggered for patient {patient_id}: {operation.operation.name}")
            return
        except Exception as e:
            logger.warning(f"Cloud Run Job client trigger failed ({e}). Running via fallback engine.")

    # Fallback / Local / Dev execution
    try:
        from account_deletion_job.deletion_engine import permanently_delete_patient
        from common_code.firestore import get_db
        db = get_db()
        await permanently_delete_patient(uid=patient_id, db=db)
    except Exception as err:
        logger.error(f"Fallback deletion runner failed for patient {patient_id}: {err}", exc_info=True)


async def execute_scheduled_account_deletion(patient_id: str, db: firestore.AsyncClient) -> dict:
    """
    Called by Cloud Task when 24 hours expire:
    Verifies that the request has not been cancelled, and triggers the Cloud Run Job.
    """
    del_ref = db.collection(settings.ACCOUNT_DELETIONS_COLLECTION).document(patient_id)
    del_snap = await del_ref.get()

    if not del_snap.exists:
        logger.warning(f"No deletion request found for patient {patient_id}. Skipping purge.")
        return {"status": "skipped", "reason": "not_found", "patient_id": patient_id}

    data = del_snap.to_dict() or {}
    status_val = data.get("status")

    if status_val != "pending_deletion":
        logger.info(f"Account deletion for {patient_id} has status '{status_val}'. Skipping purge.")
        return {"status": "skipped", "reason": f"status_is_{status_val}", "patient_id": patient_id}

    logger.info(f"24-hour grace period expired for patient {patient_id}. Triggering deletion job...")
    await trigger_account_deletion_job(patient_id=patient_id)

    return {"status": "triggered", "patient_id": patient_id}


async def schedule_patient_account_deletion(
    uid: str,
    email: str | None,
    reason: str | None,
    db: firestore.AsyncClient,
) -> dict:
    """
    Schedules an account deletion request in the 'account_deletion_requests' collection,
    and enqueues a Cloud Task to execute after the 24-hour grace period.
    """
    user_doc = await db.collection(settings.USERS_COLLECTION).document(uid).get()
    if not user_doc.exists:
        raise ValueError("User profile does not exist.")

    grace_hours = settings.ACCOUNT_DELETION_GRACE_PERIOD_HOURS
    now = datetime.datetime.now(datetime.UTC)
    scheduled_deletion_at = now + datetime.timedelta(hours=grace_hours)

    # 1. Enqueue Google Cloud Task for execution after grace period (48 hours)
    service_url = settings.SERVICE_URL or "https://patient-service-302860899707.asia-south1.run.app"
    task_url = f"{service_url.rstrip('/')}/auth/execute-deletion"
    task_name = f"account-delete-{uid}-{int(now.timestamp())}"

    task_res = create_cloud_task(
        url=task_url,
        payload={"patient_id": uid},
        schedule_at=scheduled_deletion_at,
        task_name=task_name,
        queue=settings.ACCOUNT_DELETION_QUEUE_NAME,
    )

    deletion_record = {
        "patient_id": uid,
        "email": email or (user_doc.to_dict() or {}).get("email"),
        "reason": reason,
        "status": "pending_deletion",
        "requested_at": now,
        "scheduled_deletion_at": scheduled_deletion_at,
        "cloud_task_name": task_res,
        "cancelled_at": None,
        "completed_at": None,
    }

    # 2. Store in dedicated account_deletion_requests collection
    await db.collection(settings.ACCOUNT_DELETIONS_COLLECTION).document(uid).set(deletion_record)

    # 3. Flag user document
    await db.collection(settings.USERS_COLLECTION).document(uid).update({
        "account_status": "pending_deletion",
        "deletion_scheduled_at": scheduled_deletion_at,
    })

    await log_audit_event(
        actor=uid,
        action="REQUEST_DELETE_PATIENT_ACCOUNT",
        target=uid,
        status="pending",
        details={
            "patient_id": uid,
            "scheduled_deletion_at": scheduled_deletion_at.isoformat(),
            "cloud_task": task_res,
            "reason": reason,
        },
    )

    return {
        "status": "pending_deletion",
        "message": f"Your account deletion request has been scheduled. Your account and all associated health data will be permanently deleted in the next {grace_hours} hours.",
        "patient_id": uid,
        "requested_at": now,
        "scheduled_deletion_at": scheduled_deletion_at,
    }

async def register_patient_user(uid: str, req: PatientRegisterRequest, db: firestore.AsyncClient, auth_provider: str | None = None) -> UserResponse:
    """
    Registers a new patient: creates Firestore user record and updates custom Firebase claims.
    """
    # 1. Update Custom Claims in Firebase Auth to assign the 'patient' role
    try:
        auth.set_custom_user_claims(uid, {"role": "patient"})
    except Exception:
        # For mock local environment, log error and proceed
        pass

    user_doc = {
        "uid": uid,
        "name": req.name,
        "country_code": req.country_code,
        "phone_number": req.phone_number,
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


async def update_patient_user(uid: str, req: UserUpdateRequest, db: firestore.AsyncClient) -> UserResponse:
    """
    Updates user account details in Firestore 'users' collection and syncs shared
    demographic fields (date_of_birth, location, age) to 'patients' collection.
    Note: Email cannot be updated as it is bound to the authentication provider.
    """
    user_ref = db.collection(settings.USERS_COLLECTION).document(uid)
    user_snap = await user_ref.get()
    if not user_snap.exists:
        raise ValueError("User profile does not exist.")

    user_data = user_snap.to_dict() or {}
    update_data = {}
    patient_sync_data = {}

    if req.name is not None:
        name_clean = req.name.strip()
        if len(name_clean) < 2:
            raise ValueError("Full name must be at least 2 characters.")
        update_data["name"] = name_clean

    if req.country_code is not None:
        update_data["country_code"] = req.country_code.strip() if req.country_code else None

    if req.phone_number is not None:
        update_data["phone_number"] = req.phone_number.strip() if req.phone_number else None

    if req.language_preference is not None:
        update_data["language_preference"] = (
            req.language_preference.value
            if hasattr(req.language_preference, "value")
            else str(req.language_preference)
        )

    if req.location is not None:
        loc_val = req.location.strip() if req.location else None
        update_data["location"] = loc_val
        patient_sync_data["location"] = loc_val

    if req.date_of_birth is not None:
        dob_str = req.date_of_birth.strip() if req.date_of_birth else None
        if dob_str:
            try:
                dob_date = datetime.datetime.strptime(dob_str, "%Y-%m-%d").date()
                if dob_date > datetime.date.today():
                    raise ValueError("Date of birth cannot be in the future.")
            except ValueError as e:
                if "future" in str(e):
                    raise
                raise ValueError("Date of birth must be in YYYY-MM-DD format.")

            today = datetime.date.today()
            age = today.year - dob_date.year - ((today.month, today.day) < (dob_date.month, dob_date.day))
            update_data["date_of_birth"] = dob_str
            patient_sync_data["date_of_birth"] = dob_str
            patient_sync_data["age"] = age
        else:
            update_data["date_of_birth"] = None
            patient_sync_data["date_of_birth"] = None
            patient_sync_data["age"] = None

    if update_data:
        await user_ref.update(update_data)

    if patient_sync_data:
        patient_ref = db.collection(settings.PATIENTS_COLLECTION).document(uid)
        patient_snap = await patient_ref.get()
        if patient_snap.exists:
            await patient_ref.update(patient_sync_data)

    updated_doc = await user_ref.get()
    return UserResponse(**updated_doc.to_dict())


async def cancel_patient_account_deletion(
    uid: str,
    db: firestore.AsyncClient,
) -> dict:
    """
    Cancels an existing pending account deletion request within the 24-hour grace period,
    and cancels the scheduled Cloud Task in Google Cloud Tasks.
    """
    del_ref = db.collection(settings.ACCOUNT_DELETIONS_COLLECTION).document(uid)
    del_snap = await del_ref.get()

    if not del_snap.exists:
        raise ValueError("No active deletion request found for this account.")

    data = del_snap.to_dict() or {}
    if data.get("status") != "pending_deletion":
        raise ValueError(f"Cannot cancel deletion request in status '{data.get('status')}'.")

    # 1. Proactively delete the scheduled Cloud Task from GCP
    cloud_task_name = data.get("cloud_task_name")
    if cloud_task_name:
        delete_cloud_task(cloud_task_name)

    now = datetime.datetime.now(datetime.UTC)
    await del_ref.update({
        "status": "cancelled",
        "cancelled_at": now,
    })

    # 2. Restore user account status
    user_ref = db.collection(settings.USERS_COLLECTION).document(uid)
    if (await user_ref.get()).exists:
        await user_ref.update({
            "account_status": "active",
            "deletion_scheduled_at": None,
        })

    await log_audit_event(
        actor=uid,
        action="CANCEL_DELETE_PATIENT_ACCOUNT",
        target=uid,
        status="cancelled",
        details={"patient_id": uid, "cancelled_at": now.isoformat()},
    )

    return {
        "status": "cancelled",
        "message": "Your account deletion request has been cancelled. Your account and health records will remain active.",
        "patient_id": uid,
        "cancelled_at": now,
    }
