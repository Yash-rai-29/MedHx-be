"""Firestore async client, audit logging, and FCM push notification helpers."""

import datetime
import logging
from google.cloud import firestore
from firebase_admin import messaging
from fastapi import Request
from common_code.config import settings

logger = logging.getLogger(__name__)

# ── Singleton Firestore client ────────────────────────────────
_db: firestore.AsyncClient | None = None


def get_db() -> firestore.AsyncClient:
    """Returns (and lazily creates) the Firestore AsyncClient singleton."""
    global _db
    if _db is None:
        _db = firestore.AsyncClient(project=settings.GCP_PROJECT_ID)
    return _db


# ── Audit logging ─────────────────────────────────────────────
async def log_audit_event(
    actor: str,
    action: str,
    target: str,
    details: dict | None = None,
    ip_address: str | None = None,
    user_agent: str | None = None,
    status: str = "success",
    request: Request | None = None,
) -> str:
    """Appends an immutable audit log entry into Firestore with client request context."""
    db = get_db()
    
    extracted_ip = ip_address
    extracted_ua = user_agent
    path = None
    method = None
    
    if request is not None:
        if not extracted_ip and request.client:
            extracted_ip = request.client.host
        if not extracted_ua:
            extracted_ua = request.headers.get("user-agent")
        path = request.url.path
        method = request.method

    log_data = {
        "actor": actor,
        "action": action,
        "target": target,
        "status": status,
        "ip_address": extracted_ip,
        "user_agent": extracted_ua,
        "timestamp": datetime.datetime.now(datetime.UTC),
        "details": details or {},
    }
    if path:
        log_data["path"] = path
    if method:
        log_data["method"] = method

    doc_ref = await db.collection(settings.AUDIT_LOGS_COLLECTION).add(log_data)
    return doc_ref[1].id


# ── FCM push notifications ───────────────────────────────────
def send_push_notification(
    token: str,
    title: str,
    body: str,
    data: dict | None = None,
) -> str | None:
    """Sends a push notification via Firebase Cloud Messaging.

    Args:
        token: The FCM device registration token.
        title: Notification title.
        body: Notification body.
        data: Optional key-value data payload.
    Returns:
        Message ID on success, None on failure.
    """
    try:
        message = messaging.Message(
            notification=messaging.Notification(title=title, body=body),
            data=data or {},
            token=token,
        )
        response = messaging.send(message)
        return response
    except Exception as e:
        logger.warning(f"FCM send failed (token={token[:12]}...): {e}")
        return None


async def notify_patient_report_ready(
    patient_id: str,
    consultation_id: str,
    db: firestore.AsyncClient | None = None,
) -> None:
    """Looks up the patient's FCM token and sends a 'report ready' push."""
    if db is None:
        db = get_db()

    user_doc = await db.collection(settings.USERS_COLLECTION).document(patient_id).get()
    if not user_doc.exists:
        return

    fcm_token = user_doc.to_dict().get("fcm_token")
    if not fcm_token:
        logger.info(f"No FCM token for patient {patient_id}, skipping push.")
        return

    send_push_notification(
        token=fcm_token,
        title="Your Report is Ready 📋",
        body="Your doctor has published your consultation report. Open the app to view it.",
        data={"type": "report_ready", "consultation_id": consultation_id},
    )
