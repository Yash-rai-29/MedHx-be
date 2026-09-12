import logging
from google.cloud import firestore
from common_code.config import settings

logger = logging.getLogger(__name__)


async def purge_collection_by_field(
    collection_name: str,
    field_name: str,
    uid: str,
    db: firestore.AsyncClient,
) -> int:
    """Deletes all documents in a collection matching field_name == uid using high-performance batch commits."""
    deleted_count = 0
    try:
        snaps = await db.collection(collection_name).where(field_name, "==", uid).get()
        if not snaps:
            return 0

        # Delete in batches of up to 450 (Firestore limit is 500 operations per batch)
        batch = db.batch()
        batch_ops = 0

        for s in snaps:
            # Handle subcollection for chat sessions
            if collection_name == settings.CHAT_SESSIONS_COLLECTION:
                try:
                    msgs = await db.collection(collection_name).document(s.id).collection("messages").get()
                    for m in msgs:
                        batch.delete(db.collection(collection_name).document(s.id).collection("messages").document(m.id))
                        batch_ops += 1
                        if batch_ops >= 450:
                            await batch.commit()
                            batch = db.batch()
                            batch_ops = 0
                except Exception as sub_err:
                    logger.debug(f"Subcollection purge note for chat {s.id}: {sub_err}")

            batch.delete(db.collection(collection_name).document(s.id))
            batch_ops += 1
            deleted_count += 1

            if batch_ops >= 450:
                await batch.commit()
                batch = db.batch()
                batch_ops = 0

        if batch_ops > 0:
            await batch.commit()

        logger.info(f"Purged {deleted_count} record(s) from collection '{collection_name}' for patient {uid}")
    except Exception as e:
        logger.warning(f"Error purging collection '{collection_name}' for patient {uid}: {e}")
    return deleted_count


async def purge_firestore_patient_data(uid: str, db: firestore.AsyncClient) -> dict:
    """
    Purges all Firestore medical records, profile data, and conversation history
    for the specified patient UID one by one.
    """
    logger.info(f"[Firestore Purger] Starting Firestore cleanup for UID: {uid}...")
    counts = {}

    # 1. Purge query-based collections
    counts["vitals"] = await purge_collection_by_field(settings.VITALS_COLLECTION, "patientId", uid, db)
    counts["documents"] = await purge_collection_by_field(settings.DOCUMENTS_COLLECTION, "patientId", uid, db)
    counts["consultations"] = await purge_collection_by_field(settings.CONSULTATIONS_COLLECTION, "patientId", uid, db)
    counts["audio_consultations"] = await purge_collection_by_field(settings.AUDIO_CONSULTATIONS_COLLECTION, "patientId", uid, db)
    counts["reminders"] = await purge_collection_by_field(settings.REMINDERS_COLLECTION, "patientId", uid, db)
    counts["notifications"] = await purge_collection_by_field(settings.NOTIFICATIONS_COLLECTION, "patient_id", uid, db)
    counts["exports"] = await purge_collection_by_field(settings.EXPORTS_COLLECTION, "patient_id", uid, db)
    counts["chats"] = await purge_collection_by_field(settings.CHAT_SESSIONS_COLLECTION, "patientId", uid, db)

    # 2. Delete direct profile documents
    try:
        await db.collection(settings.PATIENTS_COLLECTION).document(uid).delete()
        counts["patients_profile"] = 1
        logger.info(f"Deleted 'patients' document for {uid}")
    except Exception as e:
        counts["patients_profile"] = 0
        logger.warning(f"Error deleting 'patients' document for {uid}: {e}")

    try:
        await db.collection(settings.USERS_COLLECTION).document(uid).delete()
        counts["users_profile"] = 1
        logger.info(f"Deleted 'users' document for {uid}")
    except Exception as e:
        counts["users_profile"] = 0
        logger.warning(f"Error deleting 'users' document for {uid}: {e}")

    return counts
