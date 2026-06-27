import datetime
import uuid
from google.cloud import firestore
from common_code.config import settings
from patient_service.reminders.reminders_model import ReminderResponse, ReminderUpdateRequest, ReminderCreateRequest
from common_code.cloud_tasks import schedule_notification_task

async def get_patient_reminders(uid: str, db: firestore.AsyncClient) -> list[ReminderResponse]:
    """Retrieves all active reminders configured for the patient."""
    docs = await db.collection(settings.REMINDERS_COLLECTION) \
        .where("patientId", "==", uid) \
        .get()
        
    reminders = []
    for doc in docs:
        d = doc.to_dict()
        target_date_val = d.get("targetDate")
        target_date_str = None
        if target_date_val:
            if hasattr(target_date_val, "strftime"):
                target_date_str = target_date_val.strftime("%Y-%m-%d")
            elif isinstance(target_date_val, str):
                target_date_str = target_date_val[:10]
                
        reminders.append(ReminderResponse(
            id=doc.id,
            patientId=d["patientId"],
            type=d.get("type", "medicine"),
            title=d.get("title", ""),
            schedule_time=d.get("schedule", "09:00"),
            meal_relation=d.get("mealRelativeTiming", "NONE"),
            notification_enabled=d.get("notificationEnabled", True),
            status=d.get("status", "active"),
            consultationId=d.get("consultationId"),
            target_date=target_date_str
        ))
    return reminders

async def update_patient_reminder(
    uid: str,
    reminder_id: str,
    req: ReminderUpdateRequest,
    db: firestore.AsyncClient
) -> ReminderResponse:
    """Updates status or toggles notifications for a specific reminder."""
    doc_ref = db.collection(settings.REMINDERS_COLLECTION).document(reminder_id)
    doc_snap = await doc_ref.get()
    
    if not doc_snap.exists:
        raise ValueError("Reminder not found.")
        
    d = doc_snap.to_dict()
    if d.get("patientId") != uid:
        raise PermissionError("Access is unauthorized.")
        
    update_data = {}
    if req.notification_enabled is not None:
        update_data["notificationEnabled"] = req.notification_enabled
    if req.status is not None:
        update_data["status"] = req.status
    if req.schedule_time is not None:
        update_data["schedule"] = req.schedule_time
        
    if update_data:
        await doc_ref.update(update_data)
        
    updated_snap = await doc_ref.get()
    u = updated_snap.to_dict()
    
    target_date_val = u.get("targetDate")
    target_date_str = None
    if target_date_val:
        if hasattr(target_date_val, "strftime"):
            target_date_str = target_date_val.strftime("%Y-%m-%d")
        elif isinstance(target_date_val, str):
            target_date_str = target_date_val[:10]
            
    return ReminderResponse(
        id=reminder_id,
        patientId=u["patientId"],
        type=u.get("type", "medicine"),
        title=u.get("title", ""),
        schedule_time=u.get("schedule", "09:00"),
        meal_relation=u.get("mealRelativeTiming", "NONE"),
        notification_enabled=u.get("notificationEnabled", True),
        status=u.get("status", "active"),
        consultationId=u.get("consultationId"),
        target_date=target_date_str
    )

def add_minutes_to_time(time_str: str, minutes_to_add: int) -> str:
    """Helper to add/subtract minutes to time string formatted as HH:MM."""
    try:
        t = datetime.datetime.strptime(time_str, "%H:%M")
        t_new = t + datetime.timedelta(minutes=minutes_to_add)
        return t_new.strftime("%H:%M")
    except Exception:
        return time_str

def calculate_trigger_time(schedule_time_str: str, target_date: datetime.date | None = None) -> datetime.datetime:
    """Combines a schedule time string (HH:MM) and an optional date to produce a timezone-aware UTC datetime."""
    try:
        hour, minute = map(int, schedule_time_str.split(":"))
    except Exception:
        hour, minute = 9, 0
        
    now = datetime.datetime.now(datetime.UTC)
    if target_date:
        trigger_dt = datetime.datetime(
            year=target_date.year,
            month=target_date.month,
            day=target_date.day,
            hour=hour,
            minute=minute,
            tzinfo=datetime.UTC
        )
    else:
        trigger_dt = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
        if trigger_dt < now:
            trigger_dt += datetime.timedelta(days=1)
            
    return trigger_dt

async def create_consultation_reminders(
    patient_id: str,
    consultation_id: str,
    medicines: list[dict],
    follow_up_days: int,
    db: firestore.AsyncClient
):
    """
    Automated generation of reminders relative to meal schedules.
    Calculates schedules relative to:
    - breakfast (e.g. 08:30)
    - lunch (e.g. 13:30)
    - dinner (e.g. 20:30)
    """
    # 1. Fetch patient profile details to retrieve meal timings
    pat_doc = await db.collection(settings.PATIENTS_COLLECTION).document(patient_id).get()
    meal_times = {
        "breakfast": "08:30",
        "lunch": "13:30",
        "dinner": "20:30"
    }
    if pat_doc.exists:
        pat_data = pat_doc.to_dict()
        user_meals = pat_data.get("meal_times", {})
        if user_meals:
            meal_times.update(user_meals)
            
    # Batch write reminders
    batch = db.batch()
    
    for med in medicines:
        med_name = med.get("name", "Prescribed Medicine")
        dosage = med.get("dosage", "Take as directed")
        meal_rel = med.get("meal_relation", "AFTER_FOOD") # BEFORE_FOOD, AFTER_FOOD, WITH_FOOD, NONE
        
        # Decide which slots the medicine needs (based on parsed frequency or generic slots)
        slots = []
        dosage_lower = dosage.lower()
        if "three" in dosage_lower or "3 times" in dosage_lower or "tid" in dosage_lower:
            slots = ["breakfast", "lunch", "dinner"]
        elif "twice" in dosage_lower or "2 times" in dosage_lower or "bid" in dosage_lower:
            slots = ["breakfast", "dinner"]
        elif "morning" in dosage_lower or "breakfast" in dosage_lower:
            slots = ["breakfast"]
        elif "night" in dosage_lower or "dinner" in dosage_lower:
            slots = ["dinner"]
        else:
            # Default to morning slot
            slots = ["breakfast"]
            
        for slot in slots:
            base_time = meal_times.get(slot, "09:00")
            
            # Compute timing offset relative to meals
            if meal_rel == "BEFORE_FOOD":
                # 30 mins before meal
                alarm_time = add_minutes_to_time(base_time, -30)
            elif meal_rel == "AFTER_FOOD":
                # 30 mins after meal
                alarm_time = add_minutes_to_time(base_time, 30)
            else:
                # With food or None (same as base time)
                alarm_time = base_time
                
            reminder_id = str(uuid.uuid4())
            reminder_doc = {
                "patientId": patient_id,
                "type": "medicine",
                "title": f"Take {med_name} — {dosage}",
                "schedule": alarm_time,
                "mealRelativeTiming": meal_rel,
                "notificationEnabled": True,
                "status": "active",
                "consultationId": consultation_id,
                "createdAt": datetime.datetime.utcnow()
            }
            
            trigger_dt = calculate_trigger_time(alarm_time)
            task_name = schedule_notification_task(reminder_id, patient_id, trigger_dt)
            if task_name:
                reminder_doc["cloud_task_name"] = task_name
            
            doc_ref = db.collection(settings.REMINDERS_COLLECTION).document(reminder_id)
            batch.set(doc_ref, reminder_doc)
            
    # Add a follow-up reminder if days are specified
    if follow_up_days and follow_up_days > 0:
        followup_id = str(uuid.uuid4())
        followup_date = datetime.datetime.utcnow() + datetime.timedelta(days=follow_up_days)
        followup_doc = {
            "patientId": patient_id,
            "type": "follow-up",
            "title": f"Follow-up visit with Doctor (Consultation Ref: {consultation_id})",
            "schedule": "10:00", # default morning slot
            "mealRelativeTiming": "NONE",
            "notificationEnabled": True,
            "status": "active",
            "consultationId": consultation_id,
            "createdAt": datetime.datetime.utcnow(),
            "targetDate": followup_date
        }
        
        trigger_dt = calculate_trigger_time("10:00", followup_date.date())
        task_name = schedule_notification_task(followup_id, patient_id, trigger_dt)
        if task_name:
            followup_doc["cloud_task_name"] = task_name
            
        doc_ref = db.collection(settings.REMINDERS_COLLECTION).document(followup_id)
        batch.set(doc_ref, followup_doc)
        
    await batch.commit()


async def create_manual_reminder(
    uid: str,
    req: ReminderCreateRequest,
    db: firestore.AsyncClient
) -> ReminderResponse:
    """Manually creates a reminder for the patient and schedules Cloud Tasks alert."""
    reminder_id = str(uuid.uuid4())
    
    target_date = None
    target_date_dt = None
    if req.target_date:
        try:
            target_date_dt = datetime.datetime.strptime(req.target_date, "%Y-%m-%d")
            target_date = target_date_dt.date()
        except ValueError:
            raise ValueError("Invalid target_date format. Must be YYYY-MM-DD.")
            
    # Calculate scheduled UTC datetime for Cloud Task
    trigger_dt = calculate_trigger_time(req.schedule_time, target_date)
    
    # Schedule Cloud Task
    task_name = schedule_notification_task(reminder_id, uid, trigger_dt)
    
    reminder_doc = {
        "patientId": uid,
        "type": req.type,
        "title": req.title,
        "schedule": req.schedule_time,
        "mealRelativeTiming": req.meal_relation,
        "notificationEnabled": req.notification_enabled,
        "status": "active",
        "consultationId": None,
        "createdAt": datetime.datetime.utcnow(),
    }
    if target_date_dt:
        reminder_doc["targetDate"] = target_date_dt
        
    if task_name:
        reminder_doc["cloud_task_name"] = task_name
        
    await db.collection(settings.REMINDERS_COLLECTION).document(reminder_id).set(reminder_doc)
    
    return ReminderResponse(
        id=reminder_id,
        patientId=uid,
        type=req.type,
        title=req.title,
        schedule_time=req.schedule_time,
        meal_relation=req.meal_relation,
        notification_enabled=req.notification_enabled,
        status="active",
        consultationId=None,
        target_date=req.target_date
    )
