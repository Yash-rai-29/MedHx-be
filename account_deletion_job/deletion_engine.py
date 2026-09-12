import datetime
import logging
from typing import Any, Dict, Optional
from google.cloud import firestore

from common_code.config import settings
from common_code.firestore import get_db, log_audit_event
from account_deletion_job.purgers.auth_purger import purge_firebase_auth_user
from account_deletion_job.purgers.firestore_purger import purge_firestore_patient_data
from account_deletion_job.purgers.storage_purger import purge_storage_patient_data

logger = logging.getLogger(__name__)


async def permanently_delete_patient(
    uid: str,
    db: Optional[firestore.AsyncClient] = None,
) -> Dict[str, Any]:
    """
    Orchestrates complete and irreversible account deletion for a patient:
    1. Purges all health records, chats, and profile documents from Firestore.
    2. Purges all uploaded files, documents, and export ZIP packages from Cloud Storage.
    3. Deletes user account from Firebase Authentication.
    4. Updates the 'account_deletion_requests' tracking document to 'completed'.
    5. Logs final audit event for compliance.
    """
    if db is None:
        db = get_db()

    logger.warning(f"Starting permanent account purge for patient UID: {uid}")
    now = datetime.datetime.now(datetime.UTC)

    # 1. Purge Firestore records one by one
    counts = await purge_firestore_patient_data(uid=uid, db=db)

    # 2. Purge Cloud Storage blobs
    await purge_storage_patient_data(uid=uid)

    # 3. Delete Firebase Auth User
    purge_firebase_auth_user(uid=uid)

    # 4. Mark deletion request as completed in account_deletion_requests collection
    try:
        del_ref = db.collection(settings.ACCOUNT_DELETIONS_COLLECTION).document(uid)
        del_snap = await del_ref.get()
        if del_snap.exists:
            await del_ref.update({
                "status": "completed",
                "completed_at": now,
                "purged_counts": counts,
            })
    except Exception as e:
        logger.warning(f"Error updating deletion request status for {uid}: {e}")

    # 5. Audit log
    await log_audit_event(
        actor="account_deletion_job",
        action="PERMANENT_ACCOUNT_PURGED",
        target=uid,
        status="success",
        details={"patient_id": uid, "completed_at": now.isoformat(), "purged_counts": counts},
    )

    logger.info(f"Permanent purge completed successfully for UID: {uid}")
    return {
        "status": "completed",
        "message": "Account and all associated medical data permanently deleted.",
        "patient_id": uid,
        "completed_at": now,
        "purged_counts": counts,
    }
