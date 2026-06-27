import datetime
import json
import logging
from google.cloud import tasks_v2
from google.protobuf import timestamp_pb2
from common_code.config import settings

logger = logging.getLogger(__name__)


def create_cloud_task(
    url: str,
    payload: dict,
    schedule_at: datetime.datetime,
    task_name: str,
    queue: str | None = None,
) -> str:
    """
    Creates a named Cloud Task for deterministic deduplication.
    task_name is the short ID (e.g. 'reminder-{id}-{ts}') — the full resource path is built here.
    In development returns a mock string without making a GCP call.
    """
    if settings.ENVIRONMENT == "development":
        logger.info(f"[DEV] Mock Cloud Task '{task_name}' → {url} at {schedule_at}")
        return f"mock/{task_name}"

    try:
        client = tasks_v2.CloudTasksClient()
        project  = settings.GCP_PROJECT_ID
        location = settings.GCP_REGION or "asia-south1"
        q_name   = queue or settings.CLOUD_TASKS_QUEUE_NAME or "notification-queue"
        parent   = client.queue_path(project, location, q_name)
        full_name = f"{parent}/tasks/{task_name}"

        if schedule_at.tzinfo is None:
            schedule_at = schedule_at.replace(tzinfo=datetime.UTC)
        ts = timestamp_pb2.Timestamp()
        ts.FromDatetime(schedule_at)

        task = {
            "name": full_name,
            "http_request": {
                "http_method": tasks_v2.HttpMethod.POST,
                "url": url,
                "headers": {
                    "Content-Type": "application/json",
                    "X-Cloud-Tasks-Secret": settings.CLOUD_TASKS_SECRET or "local-tasks-secret",
                },
                "body": json.dumps(payload).encode("utf-8"),
            },
            "schedule_time": ts,
        }

        response = client.create_task(request={"parent": parent, "task": task})
        logger.info(f"Cloud Task created: {response.name}")
        return response.name
    except Exception as e:
        logger.error(f"Failed to create Cloud Task '{task_name}': {e}")
        return f"error/{task_name}"


def schedule_notification_task(
    reminder_id: str,
    patient_id: str,
    trigger_time: datetime.datetime,
) -> str | None:
    """
    Legacy helper — schedules a one-shot Cloud Task for the old trigger-notification endpoint.
    New code should use create_cloud_task() directly.
    """
    if settings.ENVIRONMENT == "development":
        logger.info(f"[DEV] Mock task for reminder {reminder_id} at {trigger_time}")
        return f"mock-task-{reminder_id}"

    try:
        client = tasks_v2.CloudTasksClient()
        project  = settings.GCP_PROJECT_ID
        location = settings.GCP_REGION or "us-central1"
        queue    = settings.CLOUD_TASKS_QUEUE_NAME or "notification-queue"
        parent   = client.queue_path(project, location, queue)

        service_url = settings.SERVICE_URL or "https://patient-service-302860899707.asia-south1.run.app"
        url = f"{service_url.rstrip('/')}/reminders/trigger-notification"

        payload = {"reminder_id": reminder_id, "patient_id": patient_id}

        task = {
            "http_request": {
                "http_method": tasks_v2.HttpMethod.POST,
                "url": url,
                "headers": {
                    "Content-Type": "application/json",
                    "X-Cloud-Tasks-Secret": settings.CLOUD_TASKS_SECRET or "local-tasks-secret",
                },
                "body": json.dumps(payload).encode("utf-8"),
            }
        }

        timestamp = timestamp_pb2.Timestamp()
        if trigger_time.tzinfo is None:
            trigger_time = trigger_time.replace(tzinfo=datetime.UTC)
        timestamp.FromDatetime(trigger_time)
        task["schedule_time"] = timestamp

        response = client.create_task(request={"parent": parent, "task": task})
        logger.info(f"Scheduled legacy Cloud Task: {response.name}")
        return response.name
    except Exception as e:
        logger.error(f"Failed to schedule Cloud Task for reminder {reminder_id}: {e}")
        return None
