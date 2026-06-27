"""Pub/Sub publisher helper for async event-driven pipelines.

Topics:
  consultation-audio-uploaded  → triggers STT transcription
  consultation-transcribed     → triggers Gemini extraction
  consultation-published       → triggers reminder creation + FCM push
  document-uploaded            → triggers OCR + summary generation
"""

import json
import logging
from google.cloud import pubsub_v1
from common_code.config import settings

logger = logging.getLogger(__name__)

# ── Singleton publisher ───────────────────────────────────────
_publisher: pubsub_v1.PublisherClient | None = None


def _get_publisher() -> pubsub_v1.PublisherClient:
    global _publisher
    if _publisher is None:
        _publisher = pubsub_v1.PublisherClient()
    return _publisher


def _topic_path(topic_name: str) -> str:
    return _get_publisher().topic_path(settings.GCP_PROJECT_ID, topic_name)


def publish_event(topic_name: str, data: dict) -> str | None:
    """Publishes a JSON message to a Pub/Sub topic.

    Args:
        topic_name: Short topic name (e.g. 'consultation-audio-uploaded').
        data: Dict payload, serialised to JSON.
    Returns:
        Published message ID on success, None on failure.
    """
    try:
        publisher = _get_publisher()
        topic = _topic_path(topic_name)
        message_bytes = json.dumps(data).encode("utf-8")
        future = publisher.publish(topic, message_bytes)
        message_id = future.result(timeout=10)
        logger.info(f"Published to {topic_name}: message_id={message_id}")
        return message_id
    except Exception as e:
        logger.warning(f"Pub/Sub publish failed for topic={topic_name}: {e}")
        return None


# ── Convenience helpers for each pipeline event ───────────────

def publish_audio_uploaded(consultation_id: str, patient_id: str, audio_ref: str, language: str = "en-IN") -> str | None:
    """Fires when consultation audio is uploaded to GCS."""
    return publish_event(settings.PUBSUB_TOPIC_AUDIO_UPLOADED, {
        "consultation_id": consultation_id,
        "patient_id": patient_id,
        "audio_ref": audio_ref,
        "language": language,
    })


def publish_transcription_complete(consultation_id: str, patient_id: str) -> str | None:
    """Fires when STT transcription finishes."""
    return publish_event(settings.PUBSUB_TOPIC_TRANSCRIBED, {
        "consultation_id": consultation_id,
        "patient_id": patient_id,
    })


def publish_consultation_published(
    consultation_id: str,
    patient_id: str,
    doctor_id: str,
    medicines: list[dict] | None = None,
    follow_up_days: int = 0,
) -> str | None:
    """Fires when doctor publishes the consultation report."""
    return publish_event(settings.PUBSUB_TOPIC_PUBLISHED, {
        "consultation_id": consultation_id,
        "patient_id": patient_id,
        "doctor_id": doctor_id,
        "medicines": medicines or [],
        "follow_up_days": follow_up_days,
    })


def publish_document_uploaded(patient_id: str, file_path: str, mime_type: str) -> str | None:
    """Fires when patient uploads a medical document."""
    return publish_event(settings.PUBSUB_TOPIC_DOCUMENT_UPLOADED, {
        "patient_id": patient_id,
        "file_path": file_path,
        "mime_type": mime_type,
    })
