from datetime import date
from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from google.cloud import firestore
from typing import List, Optional

from common_code.firebase_auth import require_role
from common_code.firestore import get_db, log_audit_event
from patient_service.vitals.vitals_func import (
    VITAL_FIELD_MAP,
    delete_vital_entry,
    get_latest_vitals,
    get_vital_trend,
    list_vitals,
    log_vitals,
)
from patient_service.vitals.vitals_model import (
    VitalLatestResponse,
    VitalTrendResponse,
    VitalsListResponse,
    VitalsLogRequest,
    VitalsLogResponse,
)

router       = APIRouter()
patient_gate = require_role(["patient"])

_VALID_TYPES = ", ".join(VITAL_FIELD_MAP.keys())


@router.post("", response_model=VitalsLogResponse, status_code=status.HTTP_201_CREATED)
async def log_vital(
    req: VitalsLogRequest,
    request: Request,
    current_user: dict = Depends(patient_gate),
    db: firestore.AsyncClient = Depends(get_db),
):
    """
    Log one or more core vitals in a single reading session.

    - Provide any combination of core vitals (BP, Heart Rate, Glucose, SpO2, Temperature, Weight/Height, Respiratory Rate).
    - BMI & category are auto-computed when both `weight` and `height` are supplied.
    - Temperature auto-converts from Fahrenheit to Celsius if value > 45 (e.g. 98.6°F -> 37.0°C).
    - Each value is evaluated against Indian clinical reference ranges and flagged.
    - `measured_at` supports backdating (e.g. entering yesterday's reading).
    """
    uid = current_user["uid"]
    try:
        result = await log_vitals(uid, req, db)
        await log_audit_event(
            actor=uid, action="LOG_VITALS", target=uid,
            details={"vital_types": result.vital_types}, request=request,
        )
        return result
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/latest", response_model=List[VitalLatestResponse])
async def latest_per_type(
    current_user: dict = Depends(patient_gate),
    db: firestore.AsyncClient = Depends(get_db),
):
    """
    Returns the single most recent reading for each vital category ever logged.
    Use this to populate dashboard summary cards.
    """
    uid = current_user["uid"]
    return await get_latest_vitals(uid, db)


@router.get("/trend/{vital_type}", response_model=VitalTrendResponse)
async def vital_trend(
    vital_type: str,
    days: int = Query(30, ge=1, le=365, description="Number of past days to include in the trend"),
    current_user: dict = Depends(patient_gate),
    db: firestore.AsyncClient = Depends(get_db),
):
    """
    Returns time-series data for a single vital type, ordered oldest → newest.
    Use this to render trend charts on the frontend.

    Valid `vital_type` values:
    blood_pressure, blood_glucose, heart_rate, spo2, temperature, weight_bmi,
    respiratory_rate
    """
    uid = current_user["uid"]
    try:
        return await get_vital_trend(uid, vital_type, days, db)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.get("", response_model=VitalsListResponse)
async def list_vital_logs(
    vital_type: Optional[str] = Query(
        None,
        description=f"Filter by vital type. One of: {_VALID_TYPES}",
    ),
    from_date: Optional[date] = Query(
        None,
        description="Filter readings on or after this date (inclusive, YYYY-MM-DD)"
    ),
    to_date: Optional[date] = Query(
        None,
        description="Filter readings on or before this date (inclusive, YYYY-MM-DD)"
    ),
    cursor: Optional[str] = Query(
        None,
        description="Pagination cursor. Pass the 'next_cursor' value from the previous page to fetch older readings."
    ),
    limit: int = Query(50, ge=1, le=200, description="Maximum entries per page (default 50, max 200)"),
    current_user: dict = Depends(patient_gate),
    db: firestore.AsyncClient = Depends(get_db),
):
    """
    Returns paginated vitals entries for the patient, newest first.
    Supports filtering by `vital_type`, date boundaries, and cursor pagination.
    """
    uid = current_user["uid"]
    try:
        return await list_vitals(
            uid=uid,
            vital_type=vital_type,
            from_date=from_date,
            to_date=to_date,
            cursor=cursor,
            limit=limit,
            db=db,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.delete("/{entry_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_vital(
    entry_id: str,
    request: Request,
    current_user: dict = Depends(patient_gate),
    db: firestore.AsyncClient = Depends(get_db),
):
    """Permanently deletes a specific vitals entry. Only the owning patient may delete."""
    uid = current_user["uid"]
    try:
        await delete_vital_entry(uid, entry_id, db)
        await log_audit_event(
            actor=uid, action="DELETE_VITAL", target=entry_id, request=request,
        )
    except PermissionError as e:
        raise HTTPException(status_code=403, detail=str(e))
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
