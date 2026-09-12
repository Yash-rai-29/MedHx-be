import datetime
from typing import Any, Dict, List, Optional
from google.cloud import firestore

from common_code.config import settings


async def fetch_vitals_data(
    patient_id: str,
    db: firestore.AsyncClient,
    start_dt: Optional[datetime.datetime] = None,
    end_dt: Optional[datetime.datetime] = None,
) -> List[Dict[str, Any]]:
    """Fetches and filters patient vitals history, newest first."""
    vitals_q = db.collection(settings.VITALS_COLLECTION).where("patientId", "==", patient_id)
    vitals_snaps = await vitals_q.get()
    vitals_list = []

    for vs in vitals_snaps:
        vd = vs.to_dict()
        vd["id"] = vs.id
        m_at = vd.get("measured_at") or vd.get("recordedAt") or vd.get("logged_at")
        if isinstance(m_at, datetime.datetime):
            if start_dt and m_at < start_dt:
                continue
            if end_dt and m_at > end_dt:
                continue
        vitals_list.append(vd)

    vitals_list.sort(
        key=lambda x: str(x.get("measured_at") or x.get("recordedAt") or ""),
        reverse=True,
    )
    return vitals_list
