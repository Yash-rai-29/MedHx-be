import asyncio
import logging
from datetime import datetime, timezone
from typing import List, Optional, Tuple

from google.cloud import firestore

from common_code.config import settings
from patient_service.notifications.notifications_model import (
    NotificationResponse,
    NotificationType,
    PushStatus,
)

logger = logging.getLogger(__name__)

_PAGE_SIZE_MAX = 100
_PAGE_SIZE_DEFAULT = 20


async def get_notifications(
    patient_id: str,
    db: firestore.AsyncClient,
    limit: int = _PAGE_SIZE_DEFAULT,
    before_cursor: Optional[str] = None,
) -> Tuple[List[NotificationResponse], Optional[str]]:
    """
    Fetches a page of notifications for a patient, sorted newest-first.

    Cursor Pagination
    -----------------
    Pass the ``next_cursor`` value returned by a previous response as the
    ``before`` query parameter to retrieve the *next* (older) page.
    The cursor is a Unix epoch timestamp string (float seconds since epoch)
    corresponding to the ``createdAt`` of the *last* document returned.
    Using a numeric cursor avoids URL-encoding issues with ISO-8601 timezone
    offset characters.

    Returns
    -------
    (notifications, next_cursor)
        ``next_cursor`` is None when there are no more pages.
    """
    limit = min(max(1, limit), _PAGE_SIZE_MAX)

    query = (
        db.collection(settings.NOTIFICATIONS_COLLECTION)
        .where("patientId", "==", patient_id)
        .order_by("createdAt", direction=firestore.Query.DESCENDING)
    )

    # Apply cursor if provided
    if before_cursor:
        try:
            cursor_ts = float(before_cursor)
            cursor_dt = datetime.fromtimestamp(cursor_ts, tz=timezone.utc)
            query = query.start_after({"createdAt": cursor_dt})
        except (ValueError, TypeError) as e:
            logger.warning(f"Invalid before_cursor value '{before_cursor}': {e}. Ignoring cursor.")

    # Fetch one extra doc to detect whether a next page exists
    docs = await query.limit(limit + 1).get()

    has_next_page = len(docs) > limit
    page_docs = docs[:limit]

    results: List[NotificationResponse] = []
    for doc in page_docs:
        data = doc.to_dict()
        results.append(
            NotificationResponse(
                id=doc.id,
                patient_id=data.get("patientId"),
                title=data.get("title", ""),
                body=data.get("body", ""),
                deeplink=data.get("deeplink"),
                is_read=data.get("isRead", False),
                created_at=data.get("createdAt"),
                type=data.get("type", NotificationType.general),
                extra_data=data.get("extraData", {}),
                push_status=data.get("pushStatus", PushStatus.pending),
                push_message_id=data.get("pushMessageId"),
            )
        )

    # Build next cursor from the createdAt of the last item in this page
    next_cursor: Optional[str] = None
    if has_next_page and results:
        last_ts: datetime = results[-1].created_at
        # Encode as Unix epoch float — safe for URL query parameters,
        # no encoding issues unlike ISO-8601 strings with + timezone offsets.
        if last_ts.tzinfo is None:
            last_ts = last_ts.replace(tzinfo=timezone.utc)
        next_cursor = str(last_ts.timestamp())

    return results, next_cursor


async def mark_notification_as_read(
    patient_id: str,
    notification_id: str,
    db: firestore.AsyncClient,
) -> bool:
    """Marks a single notification as read, ensuring it belongs to the authenticated patient."""
    doc_ref = db.collection(settings.NOTIFICATIONS_COLLECTION).document(notification_id)
    snap = await doc_ref.get()
    if not snap.exists:
        raise ValueError("Notification not found")

    data = snap.to_dict()
    if data.get("patientId") != patient_id:
        raise PermissionError("Access denied: Notification belongs to a different patient")

    await doc_ref.update({"isRead": True})
    return True


async def mark_all_notifications_as_read(
    patient_id: str,
    db: firestore.AsyncClient,
) -> bool:
    """Marks all unread notifications of the patient as read in parallel."""
    unread_query = (
        db.collection(settings.NOTIFICATIONS_COLLECTION)
        .where("patientId", "==", patient_id)
        .where("isRead", "==", False)
    )

    docs = await unread_query.get()
    if not docs:
        return True

    coros = [
        db.collection(settings.NOTIFICATIONS_COLLECTION)
        .document(doc.id)
        .update({"isRead": True})
        for doc in docs
    ]
    await asyncio.gather(*coros)
    return True
