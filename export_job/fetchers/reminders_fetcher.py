from typing import Any, Dict, List
from google.cloud import firestore

from common_code.config import settings


async def fetch_reminders_data(
    patient_id: str,
    db: firestore.AsyncClient,
) -> List[Dict[str, Any]]:
    """Fetches medication and follow-up reminders list."""
    rem_q = db.collection(settings.REMINDERS_COLLECTION).where("patientId", "==", patient_id)
    rem_snaps = await rem_q.get()
    return [r.to_dict() for r in rem_snaps]
