import logging
import uuid
from datetime import datetime, date, timedelta, UTC
from typing import Optional

from google.cloud import firestore

from common_code.cloud_tasks import create_cloud_task
from common_code.config import settings
from common_code.notification_dispatcher import dispatch_notification
from patient_service.reminders.reminders_model import (
    BatchReminderCreateResponse,
    ReminderCreateRequest,
    ReminderResponse,
    ReminderSchedule,
    ReminderStatus,
    ReminderType,
    ReminderUpdateRequest,
    RecurrenceType,
    TriggerPayload,
    MedicineReminderDetails,
    FollowUpReminderDetails,
)

logger = logging.getLogger(__name__)

MAX_TASK_DAYS    = 30
RELAY_BUFFER_DAYS = 27


# ══════════════════════════════════════════════════════════════
#  Schedule helpers
# ══════════════════════════════════════════════════════════════

def compute_next_trigger(schedule: ReminderSchedule, after: datetime) -> Optional[datetime]:
    """Returns the next UTC datetime this schedule should fire after `after`, or None if exhausted."""
    hour, minute = map(int, schedule.time_of_day.split(":"))
    end = schedule.end_date

    def at_time(d: date) -> datetime:
        return datetime(d.year, d.month, d.day, hour, minute, tzinfo=UTC)

    def past_end(d: date) -> bool:
        return end is not None and d > end

    if schedule.recurrence == RecurrenceType.once:
        target = at_time(schedule.start_date)
        return target if target > after else None

    if schedule.recurrence == RecurrenceType.daily:
        candidate = max(schedule.start_date, after.date())
        for _ in range(366 * 10):
            if past_end(candidate):
                return None
            dt = at_time(candidate)
            if dt > after:
                return dt
            candidate += timedelta(days=1)
        return None

    if schedule.recurrence == RecurrenceType.weekly:
        if not schedule.days_of_week:
            return None
        candidate = max(schedule.start_date, after.date())
        for _ in range(366 * 10):
            if past_end(candidate):
                return None
            if candidate.weekday() in schedule.days_of_week:
                dt = at_time(candidate)
                if dt > after:
                    return dt
            candidate += timedelta(days=1)
        return None

    if schedule.recurrence == RecurrenceType.monthly:
        dom = min(schedule.day_of_month or schedule.start_date.day, 28)
        year, month = after.year, after.month
        for _ in range(12 * 50):
            try:
                target_date = date(year, month, dom)
            except ValueError:
                target_date = date(year, month, 28)
            if target_date >= schedule.start_date and not past_end(target_date):
                dt = at_time(target_date)
                if dt > after:
                    return dt
            month += 1
            if month > 12:
                month = 1
                year += 1
            if year > after.year + 50:
                return None
        return None

    if schedule.recurrence == RecurrenceType.custom_dates:
        if not schedule.specific_dates:
            return None
        for d in sorted(schedule.specific_dates):
            if d >= schedule.start_date:
                dt = at_time(d)
                if dt > after:
                    return dt
        return None

    return None


def schedule_next_task(reminder_id: str, patient_id: str, target_at: datetime) -> str:
    """
    Creates a Cloud Task for target_at. If target_at is beyond the 30-day ceiling,
    a silent relay task is inserted at RELAY_BUFFER_DAYS days from now.
    Task names are deterministic so Cloud Tasks deduplicates retries automatically.
    """
    now    = datetime.now(UTC)
    delta  = (target_at - now).total_seconds()
    max_s  = MAX_TASK_DAYS * 86400
    base   = (settings.SERVICE_URL or "http://localhost:8001").rstrip("/")
    url    = f"{base}/reminders/trigger"

    if delta <= max_s:
        return create_cloud_task(
            url=url,
            payload={"reminder_id": reminder_id, "patient_id": patient_id,
                     "target_at": target_at.isoformat(), "type": "notify"},
            schedule_at=target_at,
            task_name=f"reminder-{reminder_id}-{int(target_at.timestamp())}",
        )
    else:
        relay_at = now + timedelta(days=RELAY_BUFFER_DAYS)
        return create_cloud_task(
            url=url,
            payload={"reminder_id": reminder_id, "patient_id": patient_id,
                     "target_at": target_at.isoformat(), "type": "relay"},
            schedule_at=relay_at,
            task_name=f"relay-{reminder_id}-{int(target_at.timestamp())}",
        )


# ══════════════════════════════════════════════════════════════
#  Firestore serialisation helpers
# ══════════════════════════════════════════════════════════════

def _schedule_to_dict(schedule: ReminderSchedule) -> dict:
    """Convert ReminderSchedule to a Firestore-safe dict (dates as ISO strings)."""
    return schedule.model_dump(mode="json")


def _doc_to_response(doc_id: str, d: dict) -> ReminderResponse:
    """Convert a Firestore document dict to ReminderResponse."""
    schedule_raw = d.get("schedule", {})
    # Convert any Firestore date/datetime objects in schedule back to strings for Pydantic
    for key in ("start_date", "end_date"):
        val = schedule_raw.get(key)
        if val is not None and hasattr(val, "strftime"):
            schedule_raw[key] = val.strftime("%Y-%m-%d")
    if schedule_raw.get("specific_dates"):
        schedule_raw["specific_dates"] = [
            v.strftime("%Y-%m-%d") if hasattr(v, "strftime") else v
            for v in schedule_raw["specific_dates"]
        ]

    med = d.get("medicine_details")
    fup = d.get("follow_up_details")

    return ReminderResponse(
        id=doc_id,
        patientId=d["patientId"],
        type=d.get("type", ReminderType.medicine),
        status=d.get("status", ReminderStatus.active),
        title=d.get("title", ""),
        notes=d.get("notes"),
        schedule=ReminderSchedule(**schedule_raw),
        medicine_details=MedicineReminderDetails(**med) if med else None,
        follow_up_details=FollowUpReminderDetails(**fup) if fup else None,
        consultation_id=d.get("consultation_id"),
        notification_enabled=d.get("notification_enabled", True),
        next_trigger_at=d.get("next_trigger_at"),
        last_triggered_at=d.get("last_triggered_at"),
        trigger_count=d.get("trigger_count", 0),
        created_at=d.get("created_at", datetime.now(UTC)),
    )


# ══════════════════════════════════════════════════════════════
#  CRUD
# ══════════════════════════════════════════════════════════════

async def create_reminder(
    uid: str,
    req: ReminderCreateRequest,
    db: firestore.AsyncClient,
) -> ReminderResponse:
    now     = datetime.now(UTC)
    next_dt = compute_next_trigger(req.schedule, after=now)
    if not next_dt:
        raise ValueError("Schedule produces no future occurrences.")

    doc_id = str(uuid.uuid4())
    doc = {
        "id":                   doc_id,
        "patientId":            uid,
        "type":                 req.type.value,
        "status":               ReminderStatus.active.value,
        "title":                req.title,
        "notes":                req.notes,
        "schedule":             _schedule_to_dict(req.schedule),
        "medicine_details":     req.medicine_details.model_dump() if req.medicine_details else None,
        "follow_up_details":    req.follow_up_details.model_dump() if req.follow_up_details else None,
        "consultation_id":      req.consultation_id,
        "notification_enabled": req.notification_enabled,
        "next_trigger_at":      next_dt,
        "last_triggered_at":    None,
        "trigger_count":        0,
        "created_at":           now,
    }
    await db.collection(settings.REMINDERS_COLLECTION).document(doc_id).set(doc)
    schedule_next_task(doc_id, uid, next_dt)
    return _doc_to_response(doc_id, doc)


async def batch_create_reminders(
    uid: str,
    req_list: list[ReminderCreateRequest],
    db: firestore.AsyncClient,
) -> BatchReminderCreateResponse:
    created, failed = [], []
    for req in req_list:
        try:
            reminder = await create_reminder(uid, req, db)
            created.append(reminder)
        except Exception as e:
            failed.append({"title": req.title, "error": str(e)})
    return BatchReminderCreateResponse(created=created, failed=failed)


_FAR_FUTURE = datetime(9999, 12, 31, tzinfo=UTC)


async def get_reminders(
    uid: str,
    db: firestore.AsyncClient,
    status: Optional[ReminderStatus] = None,
    reminder_type: Optional[ReminderType] = None,
) -> list[ReminderResponse]:
    query = db.collection(settings.REMINDERS_COLLECTION).where("patientId", "==", uid)
    if status:
        query = query.where("status", "==", status.value)
    # No order_by here — composite indexes may not be deployed yet.
    # Sorting is done in Python after fetch.
    docs = await query.get()
    results = []
    for doc in docs:
        d = doc.to_dict()
        if reminder_type and d.get("type") != reminder_type.value:
            continue
        try:
            results.append(_doc_to_response(doc.id, d))
        except Exception as e:
            logger.warning(f"Skipping malformed reminder {doc.id}: {e}")
    results.sort(key=lambda r: r.next_trigger_at or _FAR_FUTURE)
    return results


async def get_reminder(
    uid: str,
    reminder_id: str,
    db: firestore.AsyncClient,
) -> ReminderResponse:
    doc = await db.collection(settings.REMINDERS_COLLECTION).document(reminder_id).get()
    if not doc.exists:
        raise ValueError("Reminder not found.")
    d = doc.to_dict()
    if d.get("patientId") != uid:
        raise PermissionError("Access is unauthorized.")
    return _doc_to_response(doc.id, d)


async def update_reminder(
    uid: str,
    reminder_id: str,
    req: ReminderUpdateRequest,
    db: firestore.AsyncClient,
) -> ReminderResponse:
    doc_ref = db.collection(settings.REMINDERS_COLLECTION).document(reminder_id)
    snap    = await doc_ref.get()
    if not snap.exists:
        raise ValueError("Reminder not found.")
    d = snap.to_dict()
    if d.get("patientId") != uid:
        raise PermissionError("Access is unauthorized.")

    update: dict = {}
    if req.title is not None:
        update["title"] = req.title
    if req.notes is not None:
        update["notes"] = req.notes
    if req.notification_enabled is not None:
        update["notification_enabled"] = req.notification_enabled

    if req.status == ReminderStatus.paused:
        update["status"] = ReminderStatus.paused.value

    elif req.status == ReminderStatus.active:
        # Resume: compute next occurrence from now and restart chain
        existing_schedule = ReminderSchedule(**d["schedule"])
        next_dt = compute_next_trigger(existing_schedule, after=datetime.now(UTC))
        if not next_dt:
            raise ValueError("No future occurrences — reminder has expired.")
        update["status"]          = ReminderStatus.active.value
        update["next_trigger_at"] = next_dt
        schedule_next_task(reminder_id, uid, next_dt)

    if req.schedule is not None:
        next_dt = compute_next_trigger(req.schedule, after=datetime.now(UTC))
        if not next_dt:
            raise ValueError("New schedule produces no future occurrences.")
        update["schedule"]         = _schedule_to_dict(req.schedule)
        update["next_trigger_at"]  = next_dt
        schedule_next_task(reminder_id, uid, next_dt)

    if update:
        await doc_ref.update(update)

    updated = await doc_ref.get()
    return _doc_to_response(reminder_id, updated.to_dict())


async def delete_reminder(
    uid: str,
    reminder_id: str,
    db: firestore.AsyncClient,
) -> None:
    doc_ref = db.collection(settings.REMINDERS_COLLECTION).document(reminder_id)
    snap    = await doc_ref.get()
    if not snap.exists:
        raise ValueError("Reminder not found.")
    if snap.to_dict().get("patientId") != uid:
        raise PermissionError("Access is unauthorized.")
    # Set cancelled — the next Cloud Task will see this and stop the chain.
    # No need to cancel tasks via Cloud Tasks API.
    await doc_ref.update({"status": ReminderStatus.cancelled.value})


# ══════════════════════════════════════════════════════════════
#  Cloud Tasks trigger handler
# ══════════════════════════════════════════════════════════════

async def handle_trigger(payload: TriggerPayload, db: firestore.AsyncClient) -> None:
    """
    Called by Cloud Tasks at /reminders/trigger.
    relay  → check not cancelled → reschedule same target_at (no notification)
    notify → check status → if active: notify + chain; else: stop
    """
    reminder_id = payload.reminder_id
    patient_id  = payload.patient_id
    target_at   = datetime.fromisoformat(payload.target_at)
    if target_at.tzinfo is None:
        target_at = target_at.replace(tzinfo=UTC)

    doc_ref = db.collection(settings.REMINDERS_COLLECTION).document(reminder_id)

    # ── Relay: reschedule silently ──────────────────────────────
    if payload.type == "relay":
        snap = await doc_ref.get()
        if not snap.exists:
            return
        status = snap.to_dict().get("status")
        if status in (ReminderStatus.cancelled.value, ReminderStatus.expired.value):
            return
        schedule_next_task(reminder_id, patient_id, target_at)
        return

    # ── Notify ─────────────────────────────────────────────────
    snap = await doc_ref.get()
    if not snap.exists:
        return

    d      = snap.to_dict()
    status = d.get("status")

    if status in (ReminderStatus.cancelled.value, ReminderStatus.expired.value, ReminderStatus.paused.value):
        return

    # Send FCM + in-app notification
    await dispatch_notification(
        patient_id=patient_id,
        title=None,
        body=None,
        notification_type=d.get("type", "medicine"),
        extra_data={"reminder_id": reminder_id, "title": d.get("title", "")},
    )

    # Chain to next occurrence
    schedule  = ReminderSchedule(**d["schedule"])
    next_dt   = compute_next_trigger(schedule, after=target_at)

    if next_dt:
        schedule_next_task(reminder_id, patient_id, next_dt)
        await doc_ref.update({
            "next_trigger_at":    next_dt,
            "last_triggered_at":  target_at,
            "trigger_count":      firestore.Increment(1),
        })
    else:
        await doc_ref.update({
            "status":             ReminderStatus.expired.value,
            "last_triggered_at":  target_at,
            "trigger_count":      firestore.Increment(1),
        })
