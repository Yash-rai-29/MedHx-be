import base64
import json
from fastapi import APIRouter, Depends, HTTPException, status, Header
from google.cloud import firestore
from typing import List, Optional
from common_code.firestore import get_db, log_audit_event
from common_code.firebase_auth import require_role
from common_code.config import settings
from patient_service.reminders.reminders_model import (
    ReminderResponse,
    ReminderUpdateRequest,
    TriggerNotificationRequest,
    TriggerNotificationResponse,
    ReminderCreateRequest,
    PubSubEnvelope
)
from patient_service.reminders.reminders_func import (
    get_patient_reminders,
    update_patient_reminder,
    create_manual_reminder,
    create_consultation_reminders
)
from common_code.notification_dispatcher import dispatch_notification

router = APIRouter()
patient_gate = require_role(["patient"])

@router.get("", response_model=List[ReminderResponse])
async def get_reminders(
    current_user: dict = Depends(patient_gate),
    db: firestore.AsyncClient = Depends(get_db)
):
    """Retrieves all active medicine and follow-up reminders configured for this patient."""
    uid = current_user.get("uid")
    try:
        reminders = await get_patient_reminders(uid, db)
        return reminders
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@router.put("/{reminder_id}", response_model=ReminderResponse)
async def update_reminder(
    reminder_id: str,
    req: ReminderUpdateRequest,
    current_user: dict = Depends(patient_gate),
    db: firestore.AsyncClient = Depends(get_db)
):
    """Updates a reminder state, such as toggling push alerts or marking as completed."""
    uid = current_user.get("uid")
    try:
        reminder = await update_patient_reminder(uid, reminder_id, req, db)
        await log_audit_event(
            actor=uid,
            action="UPDATE_REMINDER",
            target=reminder_id,
            details={"status": req.status, "enabled": req.notification_enabled}
        )
        return reminder
    except PermissionError as e:
        raise HTTPException(status_code=403, detail=str(e))
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@router.post("/trigger-notification", response_model=TriggerNotificationResponse)
async def trigger_notification(
    req: TriggerNotificationRequest,
    x_cloud_tasks_secret: Optional[str] = Header(None, alias="X-Cloud-Tasks-Secret"),
    db: firestore.AsyncClient = Depends(get_db)
):
    """
    Trigger callback for Cloud Tasks to dispatch scheduled notifications.
    Authenticates requests using the shared X-Cloud-Tasks-Secret header in production.
    """
    expected_secret = settings.CLOUD_TASKS_SECRET or "local-tasks-secret"
    if settings.ENVIRONMENT == "production":
        if not x_cloud_tasks_secret or x_cloud_tasks_secret != expected_secret:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Unauthorized Cloud Task request."
            )
            
    reminder_ref = db.collection(settings.REMINDERS_COLLECTION).document(req.reminder_id)
    reminder_snap = await reminder_ref.get()
    if not reminder_snap.exists:
        raise HTTPException(status_code=404, detail="Reminder not found.")
        
    reminder_data = reminder_snap.to_dict()
    if not reminder_data.get("notificationEnabled", True) or reminder_data.get("status", "active") != "active":
        return TriggerNotificationResponse(success=False)
        
    success = await dispatch_notification(
        patient_id=req.patient_id,
        title=None,
        body=None,
        notification_type=reminder_data.get("type", "medicine"),
        extra_data={"reminder_id": req.reminder_id}
    )

    
    if success:
        await reminder_ref.update({"status": "completed"})
        
    return TriggerNotificationResponse(success=success)


@router.post("", response_model=ReminderResponse, status_code=status.HTTP_201_CREATED)
async def create_reminder(
    req: ReminderCreateRequest,
    current_user: dict = Depends(patient_gate),
    db: firestore.AsyncClient = Depends(get_db)
):
    """Manually creates a new medicine or follow-up reminder for the patient."""
    uid = current_user.get("uid")
    try:
        reminder = await create_manual_reminder(uid, req, db)
        await log_audit_event(
            actor=uid,
            action="CREATE_REMINDER",
            target=reminder.id,
            details={"type": req.type, "title": req.title}
        )
        return reminder
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/pubsub-handler", status_code=status.HTTP_200_OK)
async def handle_pubsub_published_event(
    envelope: PubSubEnvelope,
    db: firestore.AsyncClient = Depends(get_db)
):
    """
    Handles GCP Pub/Sub push notification for 'consultation-published' event.
    Decodes the payload and generates patient alarms/reminders.
    """
    try:
        # Decode base64 payload data
        decoded_bytes = base64.b64decode(envelope.message.data)
        payload = json.loads(decoded_bytes.decode("utf-8"))
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Invalid base64 payload or non-JSON data: {str(e)}"
        )
        
    consultation_id = payload.get("consultation_id")
    patient_id = payload.get("patient_id")
    medicines = payload.get("medicines", [])
    follow_up_days = payload.get("follow_up_days", 0)
    
    if not consultation_id or not patient_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Missing required fields: consultation_id or patient_id."
        )
        
    try:
        await create_consultation_reminders(
            patient_id=patient_id,
            consultation_id=consultation_id,
            medicines=medicines,
            follow_up_days=follow_up_days,
            db=db
        )
        await log_audit_event(
            actor="pubsub-subscriber",
            action="PUBSUB_CREATE_REMINDERS",
            target=consultation_id,
            details={"patient_id": patient_id, "medicines_count": len(medicines)}
        )
        return {"status": "success", "message": "Reminders generated successfully."}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
