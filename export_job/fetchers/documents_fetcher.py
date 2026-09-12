import datetime
import logging
import zipfile
from typing import Any, Dict, List, Optional, Tuple
from google.cloud import firestore

from common_code.config import settings
from export_job.gcs_storage import clean_gcs_blob_name, download_file_bytes, is_safe_patient_blob_path, sanitize_filename

logger = logging.getLogger(__name__)


async def fetch_and_bundle_documents(
    patient_id: str,
    db: firestore.AsyncClient,
    zip_file: zipfile.ZipFile,
    start_dt: Optional[datetime.datetime] = None,
    end_dt: Optional[datetime.datetime] = None,
    include_raw_files: bool = True,
) -> Tuple[List[Dict[str, Any]], int]:
    """
    Fetches medical documents and lab reports, and bundles original user-uploaded files into Documents/.
    Enforces strict GCS blob path isolation and Zip Slip protection.
    Returns (documents_list, downloaded_files_count).
    """
    docs_q = db.collection(settings.DOCUMENTS_COLLECTION).where("patientId", "==", patient_id)
    docs_snaps = await docs_q.get()
    docs_list = []

    for ds in docs_snaps:
        dd = ds.to_dict()
        dd["id"] = ds.id
        d_at = dd.get("createdAt") or dd.get("created_at")
        if isinstance(d_at, datetime.datetime):
            if start_dt and d_at < start_dt:
                continue
            if end_dt and d_at > end_dt:
                continue
        docs_list.append(dd)

    docs_list.sort(
        key=lambda x: str(x.get("createdAt") or x.get("created_at") or ""),
        reverse=True,
    )

    # Download original document blobs from GCS into Documents/ folder
    downloaded_files_count = 0
    if include_raw_files:
        for doc in docs_list:
            file_ref = (
                doc.get("fileRef")
                or doc.get("file_path")
                or doc.get("filePath")
                or doc.get("gcs_uri")
                or doc.get("file_url")
            )
            if not file_ref:
                continue

            # Security Guard 1: Cross-Tenant Isolation
            if not is_safe_patient_blob_path(file_ref, patient_id):
                logger.warning(f"Security Alert: Blocked unauthorized blob path '{file_ref}' for patient '{patient_id}'")
                continue

            file_bytes = await download_file_bytes(file_ref)
            if file_bytes:
                clean_blob = clean_gcs_blob_name(file_ref)
                raw_fname = clean_blob.split("/")[-1] if "/" in clean_blob else clean_blob
                safe_fname = sanitize_filename(raw_fname)
                doc_id = sanitize_filename(doc.get("id", "doc"))

                zip_file.writestr(f"Documents/{doc_id}_{safe_fname}", file_bytes)
                downloaded_files_count += 1
                logger.info(f"Bundled document file Documents/{doc_id}_{safe_fname} ({len(file_bytes)} bytes)")

    return docs_list, downloaded_files_count
