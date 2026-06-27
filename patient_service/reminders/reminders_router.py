import base64
import json
import logging

from fastapi import APIRouter, Depends, HTTPException, status, Header, Query
from google.cloud import firestore
from typing import List, Optional

from common_code.firestore import get_db, log_audit_event
from common_code.firebase_auth import require_role
from common_code.config import settings
from patient_service.reminders.reminders_model import (
    BatchReminderCreateRequest,
    BatchReminderCreateResponse,
    DeleteReminderResponse,
    FollowUpReminderDetails,
    MedicineReminderDetails,
    PubSubEnvelope,
    RecurrenceType,
    ReminderCreateRequest,
    ReminderResponse,
    ReminderSchedule,
    ReminderStatus,
    ReminderType,
    ReminderUpdateRequest,
    TriggerPayload,
)
from patient_service.reminders.reminders_func import (
    batch_create_reminders,
    create_reminder,
    delete_reminder,
    get_reminder,
    get_reminders,
    handle_trigger,
    update_reminder,
)

logger = logging.getLogger(__name__)

router = APIRouter()
patient_gate = require_role(["patient"])


# ── Patient CRUD ───────────────────────────────────────────────────────────────

@router.post("", response_model=ReminderResponse, status_code=status.HTTP_201_CREATED)
async def create_reminder_endpoint(
    req: ReminderCreateRequest,
    current_user: dict = Depends(patient_gate),
    db: firestore.AsyncClient = Depends(get_db),
):
    """Creates a new medicine or follow-up reminder with a self-chaining Cloud Task schedule."""
    uid = current_user["uid"]
    try:
        reminder = await create_reminder(uid, req, db)
        await log_audit_event(actor=uid, action="CREATE_REMINDER", target=reminder.id,
                              details={"type": req.type, "title": req.title})
        return reminder
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/batch", response_model=BatchReminderCreateResponse, status_code=status.HTTP_201_CREATED)
async def batch_create_endpoint(
    req: BatchReminderCreateRequest,
    current_user: dict = Depends(patient_gate),
    db: firestore.AsyncClient = Depends(get_db),
):
    """Creates multiple reminders at once. Partial success is allowed — failed items are reported."""
    uid = current_user["uid"]
    try:
        result = await batch_create_reminders(uid, req.reminders, db)
        await log_audit_event(actor=uid, action="BATCH_CREATE_REMINDERS", target=uid,
                              details={"created": len(result.created), "failed": len(result.failed)})
        return result
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("", response_model=List[ReminderResponse])
async def list_reminders_endpoint(
    status_filter: Optional[ReminderStatus] = Query(None, alias="status",
                                                    description="Filter by status (active/paused/cancelled/expired)"),
    type_filter:   Optional[ReminderType]   = Query(None, alias="type",
                                                    description="Filter by type (medicine/follow_up)"),
    current_user: dict = Depends(patient_gate),
    db: firestore.AsyncClient = Depends(get_db),
):
    """Lists all reminders for the authenticated patient, ordered by next_trigger_at."""
    uid = current_user["uid"]
    try:
        return await get_reminders(uid, db, status=status_filter, reminder_type=type_filter)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/{reminder_id}", response_model=ReminderResponse)
async def get_reminder_endpoint(
    reminder_id: str,
    current_user: dict = Depends(patient_gate),
    db: firestore.AsyncClient = Depends(get_db),
):
    """Gets a single reminder by ID."""
    uid = current_user["uid"]
    try:
        return await get_reminder(uid, reminder_id, db)
    except PermissionError as e:
        raise HTTPException(status_code=403, detail=str(e))
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.put("/{reminder_id}", response_model=ReminderResponse)
async def update_reminder_endpoint(
    reminder_id: str,
    req: ReminderUpdateRequest,
    current_user: dict = Depends(patient_gate),
    db: firestore.AsyncClient = Depends(get_db),
):
    """
    Updates a reminder. Pass `status: paused` to pause, `status: active` to resume.
    Resuming recomputes next_trigger_at from now and restarts the Cloud Task chain.
    """
    uid = current_user["uid"]
    try:
        reminder = await update_reminder(uid, reminder_id, req, db)
        await log_audit_event(actor=uid, action="UPDATE_REMINDER", target=reminder_id,
                              details={"status": req.status})
        return reminder
    except PermissionError as e:
        raise HTTPException(status_code=403, detail=str(e))
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.delete("/{reminder_id}", response_model=DeleteReminderResponse, status_code=status.HTTP_200_OK)
async def delete_reminder_endpoint(
    reminder_id: str,
    current_user: dict = Depends(patient_gate),
    db: firestore.AsyncClient = Depends(get_db),
):
    """
    Marks the reminder as cancelled. The next Cloud Task that fires will see the
    status and stop the chain — no Cloud Task cancellation API call needed.
    """
    uid = current_user["uid"]
    try:
        await delete_reminder(uid, reminder_id, db)
        await log_audit_event(actor=uid, action="DELETE_REMINDER", target=reminder_id, details={})
        return DeleteReminderResponse(id=reminder_id, message="Reminder cancelled successfully.")
    except PermissionError as e:
        raise HTTPException(status_code=403, detail=str(e))
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# ── Cloud Tasks callback — no Firebase Auth ────────────────────────────────────

@router.post("/trigger", status_code=status.HTTP_200_OK)
async def trigger_endpoint(
    payload: TriggerPayload,
    x_cloud_tasks_secret: Optional[str] = Header(None, alias="X-Cloud-Tasks-Secret"),
    db: firestore.AsyncClient = Depends(get_db),
):
    """
    Cloud Tasks callback. Authenticates via X-Cloud-Tasks-Secret header.
    Handles both relay (reschedule silently) and notify (send notification + chain) types.
    """
    expected = settings.CLOUD_TASKS_SECRET or "local-tasks-secret"
    if settings.ENVIRONMENT == "production":
        if not x_cloud_tasks_secret or x_cloud_tasks_secret != expected:
            raise HTTPException(status_code=401, detail="Unauthorized Cloud Task request.")
    try:
        await handle_trigger(payload, db)
        return {"status": "ok"}
    except Exception as e:
        logger.error(f"Trigger handler error for reminder {payload.reminder_id}: {e}")
        raise HTTPException(status_code=500, detail=str(e))


# ── Keep legacy trigger-notification endpoint for backward compat ──────────────

@router.post("/trigger-notification", status_code=status.HTTP_200_OK)
async def legacy_trigger_notification(
    x_cloud_tasks_secret: Optional[str] = Header(None, alias="X-Cloud-Tasks-Secret"),
    db: firestore.AsyncClient = Depends(get_db),
):
    """Legacy endpoint kept for in-flight Cloud Tasks. New tasks use /reminders/trigger."""
    return {"status": "ok", "note": "Legacy endpoint — no-op for in-flight tasks."}


@router.post("/pubsub-handler", status_code=status.HTTP_200_OK)
async def pubsub_handler(
    envelope: PubSubEnvelope,
    db: firestore.AsyncClient = Depends(get_db),
):
    """
    Pub/Sub push handler for consultation-published events.
    Auto-creates medicine and follow-up reminders from consultation suggestion payloads.
    """
    try:
        decoded  = base64.b64decode(envelope.message.data)
        payload  = json.loads(decoded.decode("utf-8"))
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Invalid Pub/Sub payload: {e}")

    patient_id      = payload.get("patient_id")
    consultation_id = payload.get("consultation_id")
    suggestions     = payload.get("reminder_suggestions", [])

    if not patient_id or not consultation_id:
        raise HTTPException(status_code=400, detail="Missing patient_id or consultation_id.")

    if not suggestions:
        return {"status": "ok", "created": 0}

    from datetime import date as _date

    today = _date.today()
    reqs  = []
    for s in suggestions:
        try:
            sched_raw = s.get("suggested_schedule", {})
            recurrence = sched_raw.get("recurrence", "daily")
            time_of_day = sched_raw.get("time_of_day", "09:00")
            days_of_week = sched_raw.get("days_of_week")

            sched = ReminderSchedule(
                recurrence=RecurrenceType(recurrence),
                start_date=today,
                end_date=None,
                time_of_day=time_of_day,
                days_of_week=days_of_week,
            )
            rtype = ReminderType.medicine if s.get("type") == "medicine" else ReminderType.follow_up
            med_raw = s.get("medicine_details")
            fup_raw = s.get("follow_up_details")
            reqs.append(ReminderCreateRequest(
                type=rtype,
                title=s.get("title", "Reminder"),
                notes=s.get("notes"),
                schedule=sched,
                medicine_details=MedicineReminderDetails(**med_raw) if med_raw else None,
                follow_up_details=FollowUpReminderDetails(**fup_raw) if fup_raw else None,
                consultation_id=consultation_id,
            ))
        except Exception as e:
            logger.warning(f"Skipping malformed reminder suggestion: {e}")

    result = await batch_create_reminders(patient_id, reqs, db)
    await log_audit_event(actor="pubsub", action="PUBSUB_CREATE_REMINDERS",
                          target=consultation_id,
                          details={"created": len(result.created), "failed": len(result.failed)})
    return {"status": "ok", "created": len(result.created), "failed": len(result.failed)}
