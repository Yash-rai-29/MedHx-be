import logging
from common_code.gcp_clients import async_delete_gcs_prefix

logger = logging.getLogger(__name__)


async def purge_storage_patient_data(uid: str) -> None:
    """
    Deletes all Cloud Storage files belonging to the patient:
    - Raw uploaded documents, prescriptions, and lab scans: patients/{uid}/*
    - Generated export ZIP packages: exports/{uid}/*
    """
    logger.info(f"[Storage Purger] Deleting Cloud Storage files for UID: {uid}...")
    try:
        await async_delete_gcs_prefix(f"patients/{uid}/")
        logger.info(f"Purged GCS prefix 'patients/{uid}/'")
    except Exception as e:
        logger.warning(f"Error purging GCS prefix 'patients/{uid}/': {e}")

    try:
        await async_delete_gcs_prefix(f"exports/{uid}/")
        logger.info(f"Purged GCS prefix 'exports/{uid}/'")
    except Exception as e:
        logger.warning(f"Error purging GCS prefix 'exports/{uid}/': {e}")
