from datetime import date
from typing import Optional, Union
from fastapi import APIRouter, Depends, HTTPException, Query, status
from google.cloud import firestore

from common_code.firebase_auth import require_role
from common_code.firestore import get_db
from patient_service.timeline.timeline_func import get_patient_timeline
from patient_service.timeline.timeline_model import (
    TimelineFilterType,
    TimelineResponse,
)

router = APIRouter()
patient_gate = require_role(["patient"])


@router.get("", response_model=TimelineResponse, status_code=status.HTTP_200_OK)
async def get_timeline(
    type: TimelineFilterType = Query(
        TimelineFilterType.all,
        description="Filter by item type: 'all', 'consult_record', or 'document'"
    ),
    year: Optional[int] = Query(
        None,
        ge=2000,
        le=2100,
        description="Filter by calendar year (e.g. 2026). Can be combined with 'month'."
    ),
    month: Optional[str] = Query(
        None,
        description="Filter by month number (1-12), two-digit month ('08'), or 'YYYY-MM' (e.g. '2026-08')"
    ),
    from_date: Optional[date] = Query(
        None,
        description="Filter events starting from this date (inclusive, YYYY-MM-DD)"
    ),
    to_date: Optional[date] = Query(
        None,
        description="Filter events up to this date (inclusive, YYYY-MM-DD)"
    ),
    cursor: Optional[str] = Query(
        None,
        description="Pagination cursor. Pass the 'next_cursor' value from the previous page to fetch older events."
    ),
    limit: int = Query(
        20,
        ge=1,
        le=100,
        description="Number of timeline items per page (default 20, max 100)"
    ),
    current_user: dict = Depends(patient_gate),
    db: firestore.AsyncClient = Depends(get_db),
):
    """
    Unified Medical History Timeline Feed.

    Returns an activity timeline combining both:
    - **Medical Documents** (Prescriptions, Lab Reports, Discharge Summaries, Imaging)
    - **Consultation Records** (Audio consultations & Doctor Visit Summaries)

    Features:
    - **Month-Grouped Hierarchy**: Returns `groups` array (`[{"month_group": "August 2026", "items": [...]}, ...]`)
      for simple rendering of monthly timeline sections on the frontend, plus a flat `items` list.
    - **Year & Month Filtering**: Pass `year=2026` and/or `month=8` (or `month=2026-08`) to view specific time periods.
    - **Chronological ordering**: Most recent items on top across all categories.
    - **Cursor-based pagination**: High-performance scrolling feed using `cursor` & `next_cursor`.
    - **Rich summary badges**: Key diagnosis, abnormal lab count, medicine counts, and audio/file presence.
    """
    uid = current_user.get("uid")
    try:
        return await get_patient_timeline(
            patient_id=uid,
            db=db,
            type_filter=type,
            year=year,
            month=month,
            from_date=from_date,
            to_date=to_date,
            cursor=cursor,
            limit=limit,
        )
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Error loading medical timeline: {str(e)}"
        )
