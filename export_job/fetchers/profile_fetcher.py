from google.cloud import firestore
from common_code.config import settings


async def fetch_profile_data(
    patient_id: str,
    db: firestore.AsyncClient,
) -> tuple[dict, dict]:
    """Fetches user account and clinical patient profile documents."""
    user_doc = await db.collection(settings.USERS_COLLECTION).document(patient_id).get()
    patient_doc = await db.collection(settings.PATIENTS_COLLECTION).document(patient_id).get()

    user_data = user_doc.to_dict() if user_doc.exists else {}
    patient_data = patient_doc.to_dict() if patient_doc.exists else {}

    return user_data, patient_data
