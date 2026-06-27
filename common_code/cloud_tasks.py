import datetime
import json
import logging
from google.cloud import tasks_v2
from google.protobuf import timestamp_pb2
from common_code.config import settings

logger = logging.getLogger(__name__)

def schedule_notification_task(
    reminder_id: str,
    patient_id: str,
    trigger_time: datetime.datetime,
) -> str | None:
    """
    Schedules a Google Cloud Task to hit the FastAPI trigger-notification endpoint.
    If the environment is development, it prints a console log and returns a mock task reference.
    """
    if settings.ENVIRONMENT == "development":
        logger.info(f"[DEV MODE] Mock scheduling Cloud Task for reminder: {reminder_id} at {trigger_time}")
        return f"mock-task-{reminder_id}"

    try:
        client = tasks_v2.CloudTasksClient()
        
        # Build paths
        project = settings.GCP_PROJECT_ID
        location = settings.GCP_REGION or "us-central1"
        queue = settings.CLOUD_TASKS_QUEUE_NAME or "notification-queue"
        parent = client.queue_path(project, location, queue)
        
        # Build service endpoint callback URL
        service_url = settings.SERVICE_URL or "https://patient-service-302860899707.asia-south1.run.app"
        url = f"{service_url.rstrip('/')}/reminders/trigger-notification"
        
        payload = {
            "reminder_id": reminder_id,
            "patient_id": patient_id
        }
        
        # Construct HTTP task payload
        task = {
            "http_request": {
                "http_method": tasks_v2.HttpMethod.POST,
                "url": url,
                "headers": {
                    "Content-Type": "application/json",
                    "X-Cloud-Tasks-Secret": settings.CLOUD_TASKS_SECRET or "local-tasks-secret"
                },
                "body": json.dumps(payload).encode("utf-8"),
            }
        }
        
        # Convert scheduling trigger time to Protobuf Timestamp format
        timestamp = timestamp_pb2.Timestamp()
        # Ensure trigger_time is aware and normalized to UTC
        if trigger_time.tzinfo is None:
            trigger_time = trigger_time.replace(tzinfo=datetime.UTC)
        timestamp.FromDatetime(trigger_time)
        task["schedule_time"] = timestamp
        
        # Create Cloud Task
        response = client.create_task(request={"parent": parent, "task": task})
        logger.info(f"Successfully scheduled Google Cloud Task: {response.name} targeting URL callback: {url}")
        return response.name
        
    except Exception as e:
        logger.error(f"Failed to schedule Cloud Task for reminder {reminder_id}: {e}")
        return None
