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
    Log one or more vitals in a single reading session.

    - Provide any combination of vital fields — at least one is required.
    - BMI is auto-computed when both `weight` and `height` are supplied.
    - Each value is evaluated against Indian reference ranges and flagged accordingly.
    - `measured_at` supports backdating (e.g. entering yesterday's glucometer reading).
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
    Returns the single most recent reading for each vital type that has ever been logged.
    Use this to populate dashboard vital summary cards.
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
    respiratory_rate, hba1c, cholesterol, uric_acid, creatinine, hemoglobin,
    waist_circumference
    """
    uid = current_user["uid"]
    try:
        return await get_vital_trend(uid, vital_type, days, db)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.get("", response_model=List[VitalsLogResponse])
async def list_vital_logs(
    vital_type: Optional[str] = Query(
        None,
        description=f"Filter by vital type. One of: {_VALID_TYPES}",
    ),
    limit: int = Query(50, ge=1, le=200, description="Maximum entries to return"),
    current_user: dict = Depends(patient_gate),
    db: firestore.AsyncClient = Depends(get_db),
):
    """
    Returns all vitals entries for the patient, newest first.
    Filter by `vital_type` to fetch entries for a specific measurement category.
    """
    uid = current_user["uid"]
    try:
        return await list_vitals(uid, vital_type, limit, db)
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
