from fastapi import APIRouter, Depends, HTTPException
from google.cloud import firestore

from common_code.firestore import get_db
from common_code.firebase_auth import require_role
from patient_service.dashboard.dashboard_model import DashboardStats
from patient_service.dashboard.dashboard_func import get_dashboard_stats

router = APIRouter()
patient_gate = require_role(["patient"])


@router.get("", response_model=DashboardStats)
async def get_dashboard(
    current_user: dict = Depends(patient_gate),
    db: firestore.AsyncClient = Depends(get_db),
):
    """
    Returns a complete dashboard snapshot in a single call.

    All data sources (reminders, documents, consultations, vitals, profile)
    are fetched in parallel using asyncio.gather with Firestore field projections,
    so this endpoint is fast regardless of how much data the patient has.

    Individual source failures are caught and degraded gracefully — a missing
    vitals record or failed consultation query will not fail the whole response.
    """
    try:
        return await get_dashboard_stats(current_user["uid"], db)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
