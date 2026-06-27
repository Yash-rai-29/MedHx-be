from fastapi import APIRouter, Depends, HTTPException, status
from google.cloud import firestore
from common_code.firestore import get_db
from common_code.firebase_auth import require_role
from doctor_service.analytics.analytics_model import AnalyticsTrendsResponse
from doctor_service.analytics.analytics_func import get_public_health_trends

router = APIRouter()
doctor_gate = require_role(["doctor"])

@router.get("/trends", response_model=AnalyticsTrendsResponse)
async def get_trends(
    current_user: dict = Depends(doctor_gate),
    db: firestore.AsyncClient = Depends(get_db)
):
    """Retrieves aggregated, de-identified public health trends for regional analysis dashboards."""
    try:
        trends = await get_public_health_trends(db)
        return trends
    except Exception as e:
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=str(e))
