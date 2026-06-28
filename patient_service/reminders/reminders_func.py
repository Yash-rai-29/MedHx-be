import asyncio
import logging
import uuid
from datetime import datetime, date, timedelta, UTC
from typing import Optional

from google.cloud import firestore


class ReminderNotFoundError(Exception):
    """Raised when a reminder document does not exist or the caller does not own it."""

from common_code.cloud_tasks import cancel_reminder_task, create_cloud_task
from common_code.config import settings
from common_code.notification_dispatcher import dispatch_notification
from patient_service.reminders.reminders_model import (
    BatchReminderCreateResponse,
    MealTiming,
    ReminderCreateRequest,
    ReminderListItem,
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

from zoneinfo import ZoneInfo

logger = logging.getLogger(__name__)

MAX_TASK_DAYS     = 30
RELAY_BUFFER_DAYS = 27
MEAL_OFFSET_MIN   = 15   # minutes before/after the meal time

DEFAULT_MEAL_TIMES = {
    "breakfast": "08:00",
    "lunch":     "13:00",
    "dinner":    "20:00",
}

_MEAL_TIMING_MAP = {
    MealTiming.before_breakfast: ("breakfast", -MEAL_OFFSET_MIN),
    MealTiming.after_breakfast:  ("breakfast",  MEAL_OFFSET_MIN),
    MealTiming.before_lunch:     ("lunch",     -MEAL_OFFSET_MIN),
    MealTiming.after_lunch:      ("lunch",      MEAL_OFFSET_MIN),
    MealTiming.before_dinner:    ("dinner",    -MEAL_OFFSET_MIN),
    MealTiming.after_dinner:     ("dinner",     MEAL_OFFSET_MIN),
}


# ══════════════════════════════════════════════════════════════
#  Meal-time resolution helpers
# ══════════════════════════════════════════════════════════════

async def _get_patient_meal_times(uid: str, db: firestore.AsyncClient) -> dict:
    """Returns the patient's meal times from their profile, falling back to defaults."""
    try:
        snap = await db.collection(settings.PATIENTS_COLLECTION).document(uid).get()
        if snap.exists:
            profile_meals = (snap.to_dict() or {}).get("meal_times") or {}
            return {
                "breakfast": profile_meals.get("breakfast") or DEFAULT_MEAL_TIMES["breakfast"],
                "lunch":     profile_meals.get("lunch")     or DEFAULT_MEAL_TIMES["lunch"],
                "dinner":    profile_meals.get("dinner")    or DEFAULT_MEAL_TIMES["dinner"],
            }
    except Exception as e:
        logger.warning(f"Could not read meal times for {uid}: {e}")
    return DEFAULT_MEAL_TIMES.copy()


def _meal_timing_to_hhmm(meal_timing: MealTiming, meal_times: dict) -> str:
    """Converts a MealTiming value + profile meal times to a concrete HH:MM string."""
    meal_key, offset = _MEAL_TIMING_MAP[meal_timing]
    base = meal_times.get(meal_key) or DEFAULT_MEAL_TIMES[meal_key]
    h, m = map(int, base.split(":"))
    total = h * 60 + m + offset
    total = max(0, min(23 * 60 + 59, total))   # clamp within a single day
    return f"{total // 60:02d}:{total % 60:02d}"


async def _resolve_schedule(
    schedule: ReminderSchedule, uid: str, db: firestore.AsyncClient
) -> ReminderSchedule:
    """Returns a schedule copy with time_of_day resolved from the patient's meal times.

    For specific_time or no meal_timing, the original schedule is returned unchanged.
    For meal-relative timings the patient's profile is read and ±15 min is applied.
    """
    if not schedule.meal_timing or schedule.meal_timing == MealTiming.specific_time:
        return schedule
    meal_times = await _get_patient_meal_times(uid, db)
    resolved_time = _meal_timing_to_hhmm(schedule.meal_timing, meal_times)
    return schedule.model_copy(update={"time_of_day": resolved_time})


# ══════════════════════════════════════════════════════════════
#  Schedule helpers
# ══════════════════════════════════════════════════════════════

def compute_next_trigger(schedule: ReminderSchedule, after: datetime) -> Optional[datetime]:
    """Returns the next UTC datetime this schedule should fire after `after`, or None if exhausted.

    All time_of_day values are interpreted in the Asia/Kolkata (IST) timezone.
    For meal-relative schedules, call _resolve_schedule() first so time_of_day is populated.
    """
    if not schedule.time_of_day:
        raise ValueError(
            "ReminderSchedule.time_of_day is not set. "
            "Call _resolve_schedule() before compute_next_trigger() for meal-relative reminders."
        )
    kolkata_tz = ZoneInfo("Asia/Kolkata")
    hour, minute = map(int, schedule.time_of_day.split(":"))
    end = schedule.end_date

    # Convert the after UTC datetime to Asia/Kolkata to perform correct date checks
    after_local = after.astimezone(kolkata_tz)
    local_date = after_local.date()

    def at_time(d: date) -> datetime:
        # Create localized datetime in Asia/Kolkata, then convert to UTC
        local_dt = datetime(d.year, d.month, d.day, hour, minute, tzinfo=kolkata_tz)
        return local_dt.astimezone(UTC)

    def past_end(d: date) -> bool:
        return end is not None and d > end

    if schedule.recurrence == RecurrenceType.once:
        target = at_time(schedule.start_date)
        return target if target > after else None

    if schedule.recurrence == RecurrenceType.daily:
        candidate = max(schedule.start_date, local_date)
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
        candidate = max(schedule.start_date, local_date)
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
        year, month = after_local.year, after_local.month
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
            if year > after_local.year + 50:
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
    base   = (settings.SERVICE_URL or "https://patient-service-302860899707.asia-south1.run.app").rstrip("/")
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


def _parse_schedule(d: dict) -> ReminderSchedule:
    """Parse a Firestore doc dict into a ReminderSchedule, normalising date types."""
    raw = d.get("schedule", {})
    if not isinstance(raw, dict):
        raw = {"recurrence": "daily", "time_of_day": "09:00"}
    for key in ("start_date", "end_date"):
        val = raw.get(key)
        if val is not None and hasattr(val, "strftime"):
            raw[key] = val.strftime("%Y-%m-%d")
    if raw.get("specific_dates"):
        raw["specific_dates"] = [
            v.strftime("%Y-%m-%d") if hasattr(v, "strftime") else v
            for v in raw["specific_dates"]
        ]
    return ReminderSchedule(**raw)


def _parse_med(d: dict) -> "MedicineReminderDetails | None":
    med = d.get("medicine_details")
    return MedicineReminderDetails(**med) if isinstance(med, dict) else None


def _parse_fup(d: dict) -> "FollowUpReminderDetails | None":
    fup = d.get("follow_up_details")
    return FollowUpReminderDetails(**fup) if isinstance(fup, dict) else None


def _doc_to_response(doc_id: str, d: dict) -> ReminderResponse:
    return ReminderResponse(
        id=doc_id,
        patientId=d["patientId"],
        type=d.get("type", ReminderType.medicine),
        status=d.get("status", ReminderStatus.active),
        title=d.get("title", ""),
        notes=d.get("notes"),
        schedule=_parse_schedule(d),
        medicine_details=_parse_med(d),
        follow_up_details=_parse_fup(d),
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
    now = datetime.now(UTC)

    # For follow-up reminders, appointment_time overrides schedule time_of_day
    schedule = req.schedule
    if (
        req.type == ReminderType.follow_up
        and req.follow_up_details
        and req.follow_up_details.appointment_time
    ):
        schedule = schedule.model_copy(
            update={"time_of_day": req.follow_up_details.appointment_time, "meal_timing": None}
        )

    resolved = await _resolve_schedule(schedule, uid, db)
    next_dt  = compute_next_trigger(resolved, after=now)
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


_REMINDER_LIST_FIELDS = [
    "patientId", "type", "status", "title", "notes",
    "schedule", "medicine_details", "follow_up_details",
    "notification_enabled", "next_trigger_at", "created_at",
]


def _to_list_item(doc_id: str, d: dict) -> ReminderListItem:
    return ReminderListItem(
        id=doc_id,
        patientId=d["patientId"],
        type=d.get("type", ReminderType.medicine),
        status=d.get("status", ReminderStatus.active),
        title=d.get("title", ""),
        notes=d.get("notes"),
        schedule=_parse_schedule(d),
        medicine_details=_parse_med(d),
        follow_up_details=_parse_fup(d),
        notification_enabled=d.get("notification_enabled", True),
        next_trigger_at=d.get("next_trigger_at"),
        created_at=d.get("created_at", datetime.now(UTC)),
    )


async def get_reminders(
    uid: str,
    db: firestore.AsyncClient,
    status: Optional[ReminderStatus] = None,
    reminder_type: Optional[ReminderType] = None,
) -> list[ReminderListItem]:
    query = (
        db.collection(settings.REMINDERS_COLLECTION)
        .where("patientId", "==", uid)
        .select(_REMINDER_LIST_FIELDS)
    )
    if status:
        query = query.where("status", "==", status.value)
    docs = await query.get()
    results = []
    for doc in docs:
        d = doc.to_dict()
        if reminder_type and d.get("type") != reminder_type.value:
            continue
        try:
            results.append(_to_list_item(doc.id, d))
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
        raise ReminderNotFoundError("Reminder not found.")
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
        raise ReminderNotFoundError("Reminder not found.")
    d = snap.to_dict()
    if d.get("patientId") != uid:
        raise PermissionError("Access is unauthorized.")

    # Read the existing trigger time once — used to cancel the old Cloud Task
    # before any rescheduling so we never get a 409 on the new task name.
    old_trigger = d.get("next_trigger_at")

    update: dict = {}
    if req.title is not None:
        update["title"] = req.title
    if req.notes is not None:
        update["notes"] = req.notes
    if req.notification_enabled is not None:
        update["notification_enabled"] = req.notification_enabled

    if req.status == ReminderStatus.paused:
        update["status"] = ReminderStatus.paused.value
        # Cancel the pending Cloud Task so it doesn't fire while paused
        if old_trigger:
            await asyncio.to_thread(cancel_reminder_task, reminder_id, old_trigger)

    elif req.status == ReminderStatus.active:
        # Resume: re-resolve meal timing in case profile changed, then rechain
        schedule_raw = d.get("schedule", {})
        if not isinstance(schedule_raw, dict):
            schedule_raw = {"recurrence": "daily", "time_of_day": "09:00"}
        existing_schedule = ReminderSchedule(**schedule_raw)
        resolved = await _resolve_schedule(existing_schedule, uid, db)
        next_dt  = compute_next_trigger(resolved, after=datetime.now(UTC))
        if not next_dt:
            raise ValueError("Reminder has no future occurrences and cannot be resumed.")
        # Cancel old task before creating the new one to avoid 409
        if old_trigger:
            await asyncio.to_thread(cancel_reminder_task, reminder_id, old_trigger)
        update["status"]          = ReminderStatus.active.value
        update["next_trigger_at"] = next_dt
        schedule_next_task(reminder_id, uid, next_dt)

    if req.schedule is not None:
        resolved = await _resolve_schedule(req.schedule, uid, db)
        next_dt  = compute_next_trigger(resolved, after=datetime.now(UTC))
        if not next_dt:
            raise ValueError("New schedule produces no future occurrences.")
        # Cancel old task before creating the new one to avoid 409
        if old_trigger:
            await asyncio.to_thread(cancel_reminder_task, reminder_id, old_trigger)
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
        raise ReminderNotFoundError("Reminder not found.")
    d = snap.to_dict()
    if d.get("patientId") != uid:
        raise PermissionError("Access is unauthorized.")

    # Mark cancelled in Firestore — this is the authoritative stop signal
    await doc_ref.update({"status": ReminderStatus.cancelled.value})

    # Best-effort: also delete the pending Cloud Task immediately so it
    # never fires even if the handler hasn't restarted yet.
    next_trigger = d.get("next_trigger_at")
    if next_trigger:
        await asyncio.to_thread(cancel_reminder_task, reminder_id, next_trigger)


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

    # Chain to next occurrence — re-resolve meal timing so profile changes take effect
    schedule  = ReminderSchedule(**d["schedule"])
    resolved  = await _resolve_schedule(schedule, patient_id, db)
    next_dt   = compute_next_trigger(resolved, after=target_at)

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
