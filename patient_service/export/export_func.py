import asyncio
import datetime
import logging
import uuid
from typing import List, Optional
from google.cloud import firestore

from common_code.config import settings
from common_code.firestore import get_db, log_audit_event
from common_code.gcp_clients import generate_signed_download_url
from export_job.export_engine import execute_export
from patient_service.export.export_model import (
    ExportCategory,
    ExportCreateRequest,
    ExportHistoryResponse,
    ExportItemCounts,
    ExportStatus,
    ExportStatusResponse,
)

logger = logging.getLogger(__name__)


def _doc_to_export_response(doc_id: str, d: dict) -> ExportStatusResponse:
    counts_data = d.get("exported_counts")
    counts = ExportItemCounts(**counts_data) if counts_data else None

    download_url = d.get("download_url")
    url_expires = d.get("download_url_expires_at")

    # If the 24-hour window has expired, download_url becomes None and user must re-request export
    if isinstance(url_expires, datetime.datetime) and url_expires <= datetime.datetime.now(datetime.UTC):
        download_url = None

    start_date_val = None
    if d.get("start_date"):
        try:
            start_date_val = datetime.date.fromisoformat(str(d["start_date"]))
        except Exception:
            pass

    end_date_val = None
    if d.get("end_date"):
        try:
            end_date_val = datetime.date.fromisoformat(str(d["end_date"]))
        except Exception:
            pass

    return ExportStatusResponse(
        export_id=doc_id,
        patient_id=d.get("patient_id", ""),
        status=ExportStatus(d.get("status", "pending")),
        categories=d.get("categories", ["all"]),
        start_date=start_date_val,
        end_date=end_date_val,
        created_at=d.get("created_at") or datetime.datetime.now(datetime.UTC),
        completed_at=d.get("completed_at"),
        download_url=download_url,
        download_url_expires_at=url_expires,
        file_size_bytes=d.get("file_size_bytes"),
        file_name=d.get("file_name"),
        exported_counts=counts,
        error_message=d.get("error_message"),
    )


def _run_cloud_run_job_sync(export_id: str, patient_id: str) -> str:
    from google.cloud import run_v2
    client = run_v2.JobsClient()
    job_name = f"projects/{settings.GCP_PROJECT_ID}/locations/{settings.CLOUD_RUN_JOB_REGION}/jobs/{settings.CLOUD_RUN_EXPORT_JOB_NAME}"

    request = run_v2.RunJobRequest(
        name=job_name,
        overrides=run_v2.RunJobRequest.Overrides(
            container_overrides=[
                run_v2.RunJobRequest.Overrides.ContainerOverride(
                    env=[
                        run_v2.EnvVar(name="EXPORT_ID", value=export_id),
                        run_v2.EnvVar(name="PATIENT_ID", value=patient_id),
                    ]
                )
            ]
        ),
    )
    operation = client.run_job(request=request)
    return operation.operation.name


async def _trigger_export_job_async(export_id: str, patient_id: str) -> None:
    """
    Triggers Cloud Run Job execution in GCP environment or executes asynchronously
    via native background coroutine with Firestore state management.
    """
    # 1. Attempt Cloud Run Jobs execution via Google Cloud Client if in GCP environment
    if settings.ENVIRONMENT == "production":
        try:
            op_name = await asyncio.to_thread(_run_cloud_run_job_sync, export_id, patient_id)
            logger.info(f"Cloud Run Job triggered for export {export_id}: {op_name}")
            return
        except Exception as e:
            logger.warning(f"Cloud Run Job client trigger failed ({e}). Running via async worker fallback.")

    # 2. Resilient async worker execution
    async def _runner():
        try:
            db = get_db()
            await execute_export(export_id=export_id, patient_id=patient_id, db=db)
        except Exception as err:
            logger.error(f"Async export runner failed for {export_id}: {err}", exc_info=True)

    asyncio.create_task(_runner())


async def request_data_export(
    patient_id: str,
    req: ExportCreateRequest,
    db: firestore.AsyncClient,
) -> ExportStatusResponse:
    """
    Initiates a new medical data export job for the patient.
    """
    if req.start_date and req.end_date and req.start_date > req.end_date:
        raise ValueError("start_date cannot be later than end_date.")

    # Concurrency / Rate Limiting Guard: Max 3 active export requests at a time
    active_snaps = await (
        db.collection(settings.EXPORTS_COLLECTION)
        .where("patient_id", "==", patient_id)
        .where("status", "in", ["pending", "processing"])
        .get()
    )
    if len(active_snaps) >= 3:
        raise ValueError("You already have 3 export requests in progress. Please wait for them to complete.")

    now = datetime.datetime.now(datetime.UTC)
    export_id = f"exp_{uuid.uuid4().hex[:12]}"
    categories_str = [c.value if isinstance(c, ExportCategory) else str(c) for c in req.categories]

    export_doc = {
        "export_id": export_id,
        "patient_id": patient_id,
        "status": ExportStatus.pending.value,
        "categories": categories_str,
        "start_date": req.start_date.isoformat() if req.start_date else None,
        "end_date": req.end_date.isoformat() if req.end_date else None,
        "include_raw_files": req.include_raw_files,
        "created_at": now,
        "updated_at": now,
        "completed_at": None,
        "download_url": None,
        "download_url_expires_at": None,
        "file_size_bytes": None,
        "file_name": None,
        "gcs_blob_name": None,
        "exported_counts": None,
        "error_message": None,
    }

    await db.collection(settings.EXPORTS_COLLECTION).document(export_id).set(export_doc)
    logger.info(f"Created export request {export_id} for user {patient_id}")

    # Trigger async processor / Cloud Run Job
    await _trigger_export_job_async(export_id=export_id, patient_id=patient_id)

    await log_audit_event(
        actor=patient_id,
        action="REQUEST_DATA_EXPORT",
        target=patient_id,
        details={"export_id": export_id, "categories": categories_str},
    )

    return _doc_to_export_response(export_id, export_doc)


async def get_export_status(
    export_id: str,
    patient_id: str,
    db: firestore.AsyncClient,
) -> ExportStatusResponse:
    """
    Retrieves status and download metadata for a specific export job.
    Enforces patient ownership.
    """
    doc_snap = await db.collection(settings.EXPORTS_COLLECTION).document(export_id).get()
    if not doc_snap.exists:
        raise ValueError(f"Export request '{export_id}' not found.")

    data = doc_snap.to_dict() or {}
    if data.get("patient_id") != patient_id:
        raise PermissionError("Access denied to this export record.")

    return _doc_to_export_response(export_id, data)


async def list_user_exports(
    patient_id: str,
    db: firestore.AsyncClient,
    limit: int = 20,
) -> ExportHistoryResponse:
    """
    Returns list of export requests initiated by the authenticated patient, newest first.
    """
    snaps = await (
        db.collection(settings.EXPORTS_COLLECTION)
        .where("patient_id", "==", patient_id)
        .order_by("created_at", direction=firestore.Query.DESCENDING)
        .limit(limit)
        .get()
    )

    exports = [_doc_to_export_response(s.id, s.to_dict()) for s in snaps]
    return ExportHistoryResponse(exports=exports, total_count=len(exports))
