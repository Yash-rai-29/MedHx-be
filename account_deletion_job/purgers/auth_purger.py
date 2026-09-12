import logging
from firebase_admin import auth

logger = logging.getLogger(__name__)


def purge_firebase_auth_user(uid: str) -> bool:
    """Permanently deletes the patient identity from Firebase Authentication."""
    logger.info(f"[Auth Purger] Deleting Firebase Auth user: {uid}...")
    try:
        auth.delete_user(uid)
        logger.info(f"Firebase Auth user {uid} deleted successfully.")
        return True
    except Exception as e:
        logger.warning(f"Firebase Auth delete_user for {uid}: {e}")
        return False
