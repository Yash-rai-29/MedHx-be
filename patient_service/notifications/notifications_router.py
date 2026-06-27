from fastapi import APIRouter, Depends, HTTPException, Query
from google.cloud import firestore
from typing import Optional

from common_code.firestore import get_db, log_audit_event
from common_code.firebase_auth import require_role
from patient_service.notifications.notifications_model import (
    NotificationListResponse,
    MarkReadResponse,
)
from patient_service.notifications.notifications_func import (
    get_notifications,
    mark_notification_as_read,
    mark_all_notifications_as_read,
)

router = APIRouter()
patient_gate = require_role(["patient"])


@router.get("", response_model=NotificationListResponse)
async def list_notifications(
    limit: int = Query(20, ge=1, le=100, description="Number of notifications per page (max 100)"),
    before: Optional[str] = Query(
        None,
        description=(
            "Cursor for pagination. Pass the 'next_cursor' value from a previous response "
            "to retrieve the next (older) page of notifications. "
            "This is an ISO-8601 UTC timestamp string."
        )
    ),
    current_user: dict = Depends(patient_gate),
    db: firestore.AsyncClient = Depends(get_db),
):
    """
    Retrieves a paginated list of in-app notifications for the authenticated patient,
    sorted newest-first. Use the returned ``next_cursor`` as the ``before`` query
    parameter to fetch older pages.
    """
    uid = current_user.get("uid")
    notifications, next_cursor = await get_notifications(
        patient_id=uid,
        db=db,
        limit=limit,
        before_cursor=before,
    )
    return NotificationListResponse(notifications=notifications, next_cursor=next_cursor)


@router.post("/{notification_id}/read", response_model=MarkReadResponse)
async def read_notification(
    notification_id: str,
    current_user: dict = Depends(patient_gate),
    db: firestore.AsyncClient = Depends(get_db),
):
    """Marks a specific in-app notification as read. Validates user ownership of the document."""
    uid = current_user.get("uid")
    try:
        success = await mark_notification_as_read(
            patient_id=uid, notification_id=notification_id, db=db
        )
        await log_audit_event(
            actor=uid,
            action="MARK_NOTIFICATION_READ",
            target=notification_id,
            details={"success": success},
        )
        return MarkReadResponse(success=success)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except PermissionError as e:
        raise HTTPException(status_code=403, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.post("/read-all", response_model=MarkReadResponse)
async def read_all_notifications(
    current_user: dict = Depends(patient_gate),
    db: firestore.AsyncClient = Depends(get_db),
):
    """Marks all unread in-app notifications for the authenticated patient as read."""
    uid = current_user.get("uid")
    try:
        success = await mark_all_notifications_as_read(patient_id=uid, db=db)
        await log_audit_event(
            actor=uid,
            action="MARK_ALL_NOTIFICATIONS_READ",
            target=uid,
            details={"success": success},
        )
        return MarkReadResponse(success=success)
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))
