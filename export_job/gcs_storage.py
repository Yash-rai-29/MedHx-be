import asyncio
import datetime
import logging
import re
from typing import Optional

import google.auth
from google.auth import impersonated_credentials
from google.cloud import storage

from common_code.config import settings

logger = logging.getLogger(__name__)

_storage_client: storage.Client | None = None
_signing_storage_client: storage.Client | None = None


def get_storage() -> storage.Client:
    """Lazy singleton for default Google Cloud Storage client."""
    global _storage_client
    if _storage_client is None:
        _storage_client = storage.Client(project=settings.GCP_PROJECT_ID)
    return _storage_client


def get_signing_storage() -> storage.Client:
    """
    Lazy singleton for Cloud Storage client with export-sa service account impersonation
    used to generate secure V4 signed download URLs.
    """
    global _signing_storage_client
    if _signing_storage_client is None:
        try:
            target_sa = (
                settings.GCS_SIGNING_SERVICE_ACCOUNT
                or f"export-sa@{settings.GCP_PROJECT_ID}.iam.gserviceaccount.com"
            )
            logger.info(f"Initializing GCS signing client with service account impersonation: {target_sa}")
            source_credentials, _ = google.auth.default()
            impersonated_creds = impersonated_credentials.Credentials(
                source_credentials=source_credentials,
                target_principal=target_sa,
                target_scopes=["https://www.googleapis.com/auth/cloud-platform"],
                lifetime=datetime.timedelta(seconds=3600),
            )
            _signing_storage_client = storage.Client(
                project=settings.GCP_PROJECT_ID,
                credentials=impersonated_creds,
            )
        except Exception as e:
            logger.warning(f"Could not initialize impersonated signing client: {e}. Falling back to default storage.")
            _signing_storage_client = get_storage()
    return _signing_storage_client


def clean_gcs_blob_name(path_or_uri: str) -> str:
    """
    Extracts clean relative blob name from gs:// URI, https:// storage URL, or full path.
    Strips query parameters (e.g. from signed URLs) and bucket prefixes.
    """
    if not path_or_uri:
        return ""
    # Strip any signed URL query strings
    cleaned = path_or_uri.split("?")[0].strip()

    # 1. gs://bucket/path
    prefix_gs = f"gs://{settings.STORAGE_BUCKET_NAME}/"
    if cleaned.startswith(prefix_gs):
        return cleaned[len(prefix_gs):].lstrip("/")
    if cleaned.startswith("gs://"):
        parts = cleaned[5:].split("/", 1)
        if len(parts) > 1:
            return parts[1].lstrip("/")

    # 2. https://storage.googleapis.com/... or https://storage.cloud.google.com/...
    for http_prefix in [
        f"https://storage.googleapis.com/{settings.STORAGE_BUCKET_NAME}/",
        f"https://storage.cloud.google.com/{settings.STORAGE_BUCKET_NAME}/",
        "https://storage.googleapis.com/",
        "https://storage.cloud.google.com/",
    ]:
        if cleaned.startswith(http_prefix):
            remainder = cleaned[len(http_prefix):]
            if "/" in remainder and not cleaned.startswith(f"https://storage.googleapis.com/{settings.STORAGE_BUCKET_NAME}/"):
                # remainder is {bucket}/{blob_path}
                return remainder.split("/", 1)[1].lstrip("/")
            return remainder.lstrip("/")

    return cleaned.lstrip("/")


def sanitize_filename(filename: str, max_length: int = 100) -> str:
    """
    Sanitizes filenames to prevent Zip Slip directory traversal, null-byte injection,
    and invalid path characters.
    """
    if not filename:
        return "unnamed_file"
    # Remove directory separators and null bytes
    cleaned = filename.replace("/", "_").replace("\\", "_").replace("\x00", "")
    # Remove traversal patterns
    cleaned = cleaned.replace("..", "_")
    # Only keep alphanumeric, dots, underscores, hyphens
    cleaned = re.sub(r"[^a-zA-Z0-9._-]", "_", cleaned)
    # Collapse consecutive underscores
    cleaned = re.sub(r"_+", "_", cleaned)
    # Strip leading/trailing dots/spaces/underscores
    cleaned = cleaned.strip(". _")
    if not cleaned:
        cleaned = "unnamed_file"
    return cleaned[:max_length]


def is_safe_patient_blob_path(blob_path: str, patient_id: str) -> bool:
    """
    Ensures that the requested GCS blob belongs strictly to the patient's
    designated prefix ('patients/{patient_id}/' or 'tts/audio_consultations/').
    Prevents cross-tenant IDOR attacks via manipulated Firestore file references.
    """
    if not blob_path or not patient_id:
        return False
    clean = clean_gcs_blob_name(blob_path)
    allowed_prefixes = (
        f"patients/{patient_id}/",
        f"tts/audio_consultations/",
    )
    return any(clean.startswith(prefix) for prefix in allowed_prefixes)


def download_bytes_from_gcs(blob_name: str) -> bytes | None:
    """Synchronous blob download from Cloud Storage."""
    try:
        blob = get_storage().bucket(settings.STORAGE_BUCKET_NAME).blob(blob_name)
        if not blob.exists():
            return None
        return blob.download_as_bytes()
    except Exception as e:
        logger.warning(f"GCS download error for {blob_name}: {e}")
        return None


async def download_file_bytes(blob_path: str, max_bytes: int = 50 * 1024 * 1024) -> bytes | None:
    """
    Downloads raw file bytes from Cloud Storage with size safety guard (max 50MB per file).
    """
    clean_path = clean_gcs_blob_name(blob_path)
    if not clean_path:
        return None
    try:
        data = await asyncio.to_thread(download_bytes_from_gcs, clean_path)
        if data and len(data) > max_bytes:
            logger.warning(f"File '{clean_path}' exceeds size limit ({len(data)} > {max_bytes} bytes). Skipping.")
            return None
        return data
    except Exception as e:
        logger.warning(f"Failed to download GCS blob '{clean_path}': {e}")
        return None


def upload_bytes_to_gcs(blob_name: str, data: bytes, content_type: str = "application/zip") -> str:
    """Synchronous raw bytes upload to Cloud Storage."""
    try:
        bucket = get_storage().bucket(settings.STORAGE_BUCKET_NAME)
        blob = bucket.blob(blob_name)
        blob.upload_from_string(data, content_type=content_type)
        return f"gs://{settings.STORAGE_BUCKET_NAME}/{blob_name}"
    except Exception as e:
        logger.error(f"GCS upload error for '{blob_name}': {e}")
        raise


async def upload_export_zip(blob_name: str, zip_bytes: bytes) -> str:
    """Uploads export ZIP package to Cloud Storage asynchronously."""
    return await asyncio.to_thread(upload_bytes_to_gcs, blob_name, zip_bytes, "application/zip")


def create_24h_signed_url(blob_name: str) -> str | None:
    """
    Generates a secure 24-hour V4 signed download URL for the exported ZIP package.
    24 hours = 1440 minutes.
    """
    clean_path = clean_gcs_blob_name(blob_name)
    try:
        bucket = get_signing_storage().bucket(settings.STORAGE_BUCKET_NAME)
        blob = bucket.blob(clean_path)
        return blob.generate_signed_url(
            version="v4",
            expiration=datetime.timedelta(minutes=1440),
            method="GET",
        )
    except Exception as e:
        logger.error(f"Failed to generate 24h signed URL for '{clean_path}': {e}")
        return None
