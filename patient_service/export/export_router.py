import logging
from typing import Optional
from fastapi import APIRouter, Depends, Header, HTTPException, Query, status
from google.cloud import firestore

from common_code.config import settings
from common_code.firebase_auth import require_role
from common_code.firestore import get_db
from export_job.export_engine import execute_export
from patient_service.export.export_func import (
    get_export_status,
    list_user_exports,
    request_data_export,
)
from patient_service.export.export_model import (
    ExportCreateRequest,
    ExportHistoryResponse,
    ExportStatusResponse,
)

logger = logging.getLogger(__name__)

router = APIRouter()
patient_gate = require_role(["patient"])


@router.post(
    "/request",
    response_model=ExportStatusResponse,
    status_code=status.HTTP_202_ACCEPTED,
    summary="Request Medical Data Export",
    description=(
        "Initiates an asynchronous export of the patient's medical records (Consultations, Documents, "
        "Vitals with CSV spreadsheet, Reminders, and Profile). Filters by date range and categories are supported. "
        "Triggers a background Cloud Run Job which packages the data into a ZIP archive, generates a 24-hour V4 "
        "Signed Download URL, and sends an FCM push notification upon completion."
    ),
    responses={
        202: {"description": "Export job initiated and queued for background processing."},
        400: {"description": "Validation error in date range or category filters."},
    },
)
async def create_export(
    req: ExportCreateRequest,
    current_user: dict = Depends(patient_gate),
    db: firestore.AsyncClient = Depends(get_db),
):
    uid = current_user.get("uid")
    try:
        return await request_data_export(patient_id=uid, req=req, db=db)
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))
    except Exception as e:
        logger.error(f"Failed to initiate export for user {uid}: {e}", exc_info=True)
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Failed to initiate export.")


@router.get(
    "/status/{export_id}",
    response_model=ExportStatusResponse,
    summary="Get Export Status & Download URL",
    description=(
        "Retrieves the current execution status of an export request. When status is 'completed', "
        "the response includes the 24-hour secure V4 signed download URL and file size metadata."
    ),
    responses={
        200: {"description": "Export status and download metadata."},
        403: {"description": "Access forbidden: export belongs to another patient."},
        404: {"description": "Export request not found."},
    },
)
async def check_export_status(
    export_id: str,
    current_user: dict = Depends(patient_gate),
    db: firestore.AsyncClient = Depends(get_db),
):
    uid = current_user.get("uid")
    try:
        return await get_export_status(export_id=export_id, patient_id=uid, db=db)
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(e))
    except PermissionError as e:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(e))
    except Exception as e:
        logger.error(f"Error fetching export status {export_id}: {e}")
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Internal server error.")


@router.get(
    "/history",
    response_model=ExportHistoryResponse,
    summary="List Patient Export History",
    description="Retrieves a list of previous medical data export requests initiated by the patient, newest first.",
)
async def get_export_history(
    limit: int = Query(20, ge=1, le=100, description="Maximum number of export history records to return"),
    current_user: dict = Depends(patient_gate),
    db: firestore.AsyncClient = Depends(get_db),
):
    uid = current_user.get("uid")
    return await list_user_exports(patient_id=uid, db=db, limit=limit)


@router.post(
    "/internal/process",
    summary="Internal Export Job Webhook",
    description="Secure internal processor endpoint called by Cloud Tasks or Cloud Run Job dispatcher.",
    include_in_schema=False,
)
async def internal_process_export(
    export_id: str = Query(..., description="Export ID"),
    patient_id: str = Query(..., description="Patient UID"),
    x_cloud_tasks_secret: Optional[str] = Header(None, alias="X-Cloud-Tasks-Secret"),
    db: firestore.AsyncClient = Depends(get_db),
):
    # Validate secret header
    if settings.CLOUD_TASKS_SECRET:
        if not x_cloud_tasks_secret or x_cloud_tasks_secret != settings.CLOUD_TASKS_SECRET:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Invalid internal task secret.")

    try:
        res = await execute_export(export_id=export_id, patient_id=patient_id, db=db)
        return {"status": "success", "file_size_bytes": res.get("file_size_bytes")}
    except Exception as e:
        logger.error(f"Internal export process failed for {export_id}: {e}", exc_info=True)
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=str(e))
