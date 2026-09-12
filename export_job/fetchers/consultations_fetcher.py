import datetime
import logging
import zipfile
from typing import Any, Dict, List, Optional, Tuple
from google.cloud import firestore

from common_code.config import settings
from export_job.gcs_storage import clean_gcs_blob_name, download_file_bytes, is_safe_patient_blob_path, sanitize_filename

logger = logging.getLogger(__name__)


async def fetch_and_bundle_consultations(
    patient_id: str,
    db: firestore.AsyncClient,
    zip_file: zipfile.ZipFile,
    start_dt: Optional[datetime.datetime] = None,
    end_dt: Optional[datetime.datetime] = None,
    include_audio_files: bool = True,
) -> Tuple[List[Dict[str, Any]], int]:
    """
    Fetches audio and doctor consultations, and bundles raw audio recordings into Consultation_Audio/.
    Enforces strict GCS blob path isolation and Zip Slip protection.
    Returns (consultations_list, audio_files_count).
    """
    # 1. Audio consultations
    audio_q = db.collection(settings.AUDIO_CONSULTATIONS_COLLECTION).where("patientId", "==", patient_id)
    audio_snaps = await audio_q.get()

    # 2. Doctor consultations
    doc_q = db.collection(settings.CONSULTATIONS_COLLECTION).where("patientId", "==", patient_id)
    doc_snaps = await doc_q.get()

    consults_list = []
    for s in audio_snaps:
        cd = s.to_dict()
        cd["id"] = s.id
        cd["type"] = "audio_consultation"
        c_at = cd.get("created_at") or cd.get("createdAt")
        if isinstance(c_at, datetime.datetime):
            if start_dt and c_at < start_dt:
                continue
            if end_dt and c_at > end_dt:
                continue
        consults_list.append(cd)

    for s in doc_snaps:
        cd = s.to_dict()
        cd["id"] = s.id
        cd["type"] = "doctor_consultation"
        c_at = cd.get("createdAt") or cd.get("created_at")
        if isinstance(c_at, datetime.datetime):
            if start_dt and c_at < start_dt:
                continue
            if end_dt and c_at > end_dt:
                continue
        consults_list.append(cd)

    consults_list.sort(
        key=lambda x: str(x.get("created_at") or x.get("createdAt") or ""),
        reverse=True,
    )

    # Download Audio Recordings from GCS if requested
    audio_files_count = 0
    if include_audio_files:
        for c in consults_list:
            audio_path = (
                c.get("file_path")
                or c.get("gcs_uri")
                or c.get("audio_url")
                or c.get("audioUrl")
                or c.get("recording_path")
            )
            if not audio_path:
                continue

            # Security Guard 1: Cross-Tenant Isolation
            if not is_safe_patient_blob_path(audio_path, patient_id):
                logger.warning(f"Security Alert: Blocked unauthorized audio blob path '{audio_path}' for patient '{patient_id}'")
                continue

            audio_bytes = await download_file_bytes(audio_path)
            if audio_bytes:
                cid = sanitize_filename(c.get("id", "audio"))
                clean_p = clean_gcs_blob_name(audio_path)
                raw_ext = clean_p.split(".")[-1] if "." in clean_p else "mp3"
                safe_ext = sanitize_filename(raw_ext, max_length=5) or "mp3"

                zip_file.writestr(f"Consultation_Audio/{cid}.{safe_ext}", audio_bytes)
                audio_files_count += 1
                logger.info(f"Bundled audio file for consultation Consultation_Audio/{cid}.{safe_ext} ({len(audio_bytes)} bytes)")

    return consults_list, audio_files_count
