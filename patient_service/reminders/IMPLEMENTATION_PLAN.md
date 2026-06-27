# Reminders & Consultations — Implementation Plan

## Architecture Decision: Self-Chaining Cloud Tasks

No Cloud Scheduler sweeper. Each reminder schedules its own successor via a single helper `schedule_next_task()`. Cloud Tasks 30-day ceiling is handled transparently with a relay task.

```
CREATE reminder
  └─ schedule_next_task(target_at)
        ├─ ≤ 30 days → Cloud Task type="notify"
        └─ > 30 days → Cloud Task type="relay" at 27 days
                            └─ relay fires → schedule_next_task(same target_at)
                                  └─ ... until direct task is created

Cloud Task fires → POST /reminders/trigger
  ├─ type="relay" → check not cancelled → schedule_next_task(target_at)
  └─ type="notify"
        ├─ status=cancelled/paused → stop (chain ends)
        └─ status=active
              ├─ send FCM notification
              ├─ compute next_dt
              │     ├─ has next → schedule_next_task(next_dt) → chain continues
              │     └─ none    → status = expired
```

Task name deduplication: `reminder-{id}-{unix_timestamp}` — Cloud Tasks rejects duplicates, preventing double-fire on retry.

Delete → set `status = cancelled` in Firestore. Next Cloud Task fires, sees cancelled, stops. No API cancellation needed.

---

## Pass 1: Consultation Pipeline

### Files to implement:

**`patient_service/consultations/consultations_model.py`**
```python
class ConsultationStatus(str, Enum): pending, in_progress, completed, failed
class ConsultationSource(str, Enum): app_upload, doctor_recorded
class DiarizedSegment(BaseModel): speaker, text, start_time, end_time
class ExtractedMedicine(BaseModel): name, dosage, frequency, instructions, duration
class FollowUpSuggestion(BaseModel): specialty, reason, urgency, suggested_within_days
class ReminderSuggestion(BaseModel): title, type, notes, medicine_details (optional), follow_up_details (optional), suggested_schedule
class ConsultationUploadResponse(BaseModel): id, status, file_path, created_at
class ConsultationResponse(BaseModel): id, status, source, transcript, segments, medicines, follow_ups, reminder_suggestions, summary, language, created_at
```

**`patient_service/consultations/consultations_func.py`**

- `upload_consultation(uid, filename, file_bytes, mime_type, language, db)` → GCS upload + create Firestore doc (status=pending) → return ConsultationUploadResponse
- `background_process_consultation(consultation_id, uid, file_path, mime_type, language, db)`:
  1. Update status → in_progress
  2. Download audio from GCS
  3. ElevenLabs STT (primary) → if fail → GCP STT Chirp (fallback)
  4. Gemini: extract medicines, follow_ups, reminder_suggestions, summary from transcript
  5. Generate embedding (Vertex AI) → store in Firestore
  6. Update status → completed
  7. Publish to `consultation-published` Pub/Sub topic

**`patient_service/consultations/consultations_router.py`**
```
POST   /consultations/upload          → 201 ConsultationUploadResponse
GET    /consultations                 → List[ConsultationResponse]
GET    /consultations/{id}            → ConsultationResponse
POST   /consultations/{id}/translate  → TranslateSummaryResponse
GET    /consultations/{id}/listen     → audio/mpeg
DELETE /consultations/{id}            → DeleteConsultationResponse
```

---

## Pass 2: Reminders System

### Files to implement:

**`patient_service/reminders/reminders_model.py`**
```python
class RecurrenceType(str, Enum): once, daily, weekly, monthly, custom_dates
class ReminderType(str, Enum): medicine, follow_up
class ReminderStatus(str, Enum): active, paused, cancelled, expired

class ReminderSchedule(BaseModel):
    recurrence: RecurrenceType
    start_date: date
    end_date: Optional[date]       # None = indefinite (recurring only)
    time_of_day: time              # HH:MM local time
    days_of_week: Optional[List[int]]   # 0=Mon..6=Sun, for weekly
    day_of_month: Optional[int]         # 1-28, for monthly
    specific_dates: Optional[List[date]] # for custom_dates

class MedicineReminderDetails(BaseModel): name, dosage, frequency, instructions
class FollowUpReminderDetails(BaseModel): specialty, reason, urgency

class ReminderCreateRequest(BaseModel):
    type: ReminderType
    title: str
    notes: Optional[str]
    schedule: ReminderSchedule
    medicine_details: Optional[MedicineReminderDetails]
    follow_up_details: Optional[FollowUpReminderDetails]
    consultation_id: Optional[str]   # links reminder to source consultation

class BatchReminderCreateRequest(BaseModel): reminders: List[ReminderCreateRequest]
class BatchReminderCreateResponse(BaseModel): created: List[ReminderResponse], failed: List[dict]

class ReminderUpdateRequest(BaseModel):
    title: Optional[str]
    notes: Optional[str]
    status: Optional[ReminderStatus]  # for pause/resume
    schedule: Optional[ReminderSchedule]

class ReminderResponse(BaseModel):
    id, patientId, type, status, title, notes
    schedule: ReminderSchedule
    medicine_details, follow_up_details, consultation_id
    next_trigger_at: Optional[datetime]
    last_triggered_at: Optional[datetime]
    trigger_count: int
    created_at: datetime

class TriggerPayload(BaseModel):
    reminder_id: str
    patient_id: str
    target_at: str    # ISO datetime string
    type: str         # "notify" | "relay"

class DeleteReminderResponse(BaseModel): id: str, message: str
```

**`patient_service/reminders/reminders_func.py`**

Core helpers:
```python
MAX_TASK_DAYS = 30
RELAY_BUFFER_DAYS = 27

def schedule_next_task(reminder_id, patient_id, target_at: datetime) -> str:
    # if delta <= 30 days: create notify task
    # else: create relay task at 27 days from now
    # task_name = f"reminder-{reminder_id}-{int(target_at.timestamp())}"

def compute_next_trigger(schedule: ReminderSchedule, after: datetime) -> Optional[datetime]:
    # RecurrenceType.once: return None if already past start_date
    # daily: next day at time_of_day, respecting end_date
    # weekly: next matching day_of_week, respecting end_date
    # monthly: next day_of_month, respecting end_date
    # custom_dates: next date from specific_dates list after `after`
```

API functions:
- `create_reminder(uid, req, db)` → compute next_dt → store Firestore → schedule_next_task()
- `batch_create_reminders(uid, req, db)` → loop with try/except per item
- `get_reminders(uid, status, type, db)` → Firestore query (patientId, status, next_trigger_at ASC)
- `get_reminder(uid, reminder_id, db)` → single doc with ownership check
- `update_reminder(uid, reminder_id, req, db)`:
  - title/notes: simple Firestore update
  - status=paused: set paused (Cloud Task will stop chain on next fire)
  - status=active (resume): set active → compute_next_trigger(after=now) → schedule_next_task()
  - schedule change: compute new next_dt → schedule_next_task() → update Firestore
- `delete_reminder(uid, reminder_id, db)` → set status=cancelled
- `handle_trigger(payload, db)`:
  - relay: check not cancelled → schedule_next_task(same target_at)
  - notify: check status → if active → notify + compute next + schedule_next_task or expire

**`patient_service/reminders/reminders_router.py`**
```
POST   /reminders                     → 201 ReminderResponse
POST   /reminders/batch               → 201 BatchReminderCreateResponse
GET    /reminders                     → List[ReminderResponse]
GET    /reminders/{id}                → ReminderResponse
PUT    /reminders/{id}                → ReminderResponse
DELETE /reminders/{id}                → 200 DeleteReminderResponse
POST   /reminders/trigger             → 200 (Cloud Tasks callback, no auth, verified by queue header)
```

---

## Pass 3: Cloud Tasks & Infrastructure

### `common_code/cloud_tasks.py`

```python
def create_cloud_task(
    url: str,
    payload: dict,
    schedule_at: datetime,
    task_name: str,
    queue: str = "notification-queue",
) -> str:
    client = tasks_v2.CloudTasksClient()
    # Build HttpRequest with OIDC token for Cloud Run auth
    # Set scheduleTime from schedule_at
    # Task name for deduplication
```

### `patient_service/notifications/notifications_func.py`

```python
async def dispatch_notification(patient_id: str, notification_type: str, extra_data: dict):
    # Fetch FCM token from Firestore patients/{id}
    # Send via firebase_admin.messaging
    # Write to Firestore notifications/{id} (in-app record)
```

### Pub/Sub handler (consultation → reminders auto-creation)

```python
# POST /reminders/pubsub-handler (internal, Pub/Sub push)
# Triggered by consultation-published topic
# Reads reminder_suggestions from consultation doc
# Calls batch_create_reminders() for all suggestions
```

### Firestore indexes to add to `firestore.indexes.json`

```json
{ "collectionGroup": "reminders",
  "fields": [
    { "fieldPath": "patientId", "order": "ASCENDING" },
    { "fieldPath": "status",    "order": "ASCENDING" },
    { "fieldPath": "next_trigger_at", "order": "ASCENDING" }
  ]
}
```

### GCP setup additions

```bash
# Cloud Tasks queue
gcloud tasks queues create notification-queue \
  --location=REGION \
  --max-attempts=3 \
  --min-backoff=10s \
  --max-backoff=300s

# Pub/Sub topic for consultation events
gcloud pubsub topics create consultation-published
gcloud pubsub subscriptions create consultation-published-sub \
  --topic=consultation-published \
  --push-endpoint=https://PATIENT_SERVICE_URL/reminders/pubsub-handler

# Service account needs roles/cloudtasks.enqueuer
gcloud projects add-iam-policy-binding PROJECT_ID \
  --member=serviceAccount:SA_EMAIL \
  --role=roles/cloudtasks.enqueuer
```

---

## State Transition Summary

| Action | Firestore update | Cloud Task effect |
|---|---|---|
| Create | status=active, next_trigger_at=next_dt | schedule_next_task(next_dt) |
| Delete | status=cancelled | Next task fires → sees cancelled → stops |
| Pause | status=paused | Next task fires → sees paused → stops |
| Resume | status=active, next_trigger_at=new_dt | schedule_next_task(new_dt) restarts chain |
| Trigger (active) | next_trigger_at updated, trigger_count++ | schedule_next_task(next_dt) or expire |
| Trigger (cancelled/paused) | no change | no next task created |
