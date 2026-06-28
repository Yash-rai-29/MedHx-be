import asyncio
import logging
from datetime import datetime, UTC
from typing import Optional

from google.cloud import firestore

from common_code.config import settings
from patient_service.dashboard.dashboard_model import (
    DashboardAbnormalFlag,
    DashboardNextReminder,
    DashboardStats,
    DashboardVitalsSnapshot,
)

logger = logging.getLogger(__name__)

# ── Firestore field projections ───────────────────────────────────────────────

_REMINDER_FIELDS = ["type", "status", "title", "next_trigger_at", "medicine_details"]
_VITALS_FIELDS   = ["logged_at", "flags"]
_PROFILE_FIELDS  = ["name"]


# ── Individual fetchers ───────────────────────────────────────────────────────

async def _fetch_reminders(uid: str, db: firestore.AsyncClient) -> list[dict]:
    docs = await (
        db.collection(settings.REMINDERS_COLLECTION)
        .where("patientId", "==", uid)
        .where("status", "==", "active")
        .select(_REMINDER_FIELDS)
        .get()
    )
    return [{"id": d.id, **d.to_dict()} for d in docs]



async def _fetch_latest_vitals(uid: str, db: firestore.AsyncClient) -> Optional[dict]:
    docs = await (
        db.collection(settings.VITALS_COLLECTION)
        .where("patientId", "==", uid)
        .order_by("logged_at", direction=firestore.Query.DESCENDING)
        .limit(1)
        .select(_VITALS_FIELDS)
        .get()
    )
    if not docs:
        return None
    return docs[0].to_dict()


async def _fetch_profile_name(uid: str, db: firestore.AsyncClient) -> Optional[str]:
    snap = await db.collection(settings.PATIENTS_COLLECTION).document(uid).get()
    if snap.exists:
        return (snap.to_dict() or {}).get("name")
    return None


# ── Dashboard assembly ────────────────────────────────────────────────────────

async def get_dashboard_stats(uid: str, db: firestore.AsyncClient) -> DashboardStats:
    """
    Single function that fires all Firestore queries in parallel and assembles
    the dashboard response. Designed to complete in one network round-trip
    batch (all queries run concurrently via asyncio.gather).
    """
    reminders_raw, vitals_raw, patient_name = (
        await asyncio.gather(
            _fetch_reminders(uid, db),
            _fetch_latest_vitals(uid, db),
            _fetch_profile_name(uid, db),
            return_exceptions=True,
        )
    )

    # Treat any individual query failure gracefully — degrade, don't crash
    reminders_raw = reminders_raw if isinstance(reminders_raw, list) else []
    vitals_raw    = vitals_raw    if isinstance(vitals_raw, dict)    else None
    patient_name  = patient_name  if isinstance(patient_name, str)   else None

    # ── Reminders ────────────────────────────────────────────────────────────
    medicine_reminders = [r for r in reminders_raw if r.get("type") == "medicine"]
    followup_reminders = [r for r in reminders_raw if r.get("type") == "follow_up"]

    # Next reminder — earliest next_trigger_at across all active reminders
    next_reminder: Optional[DashboardNextReminder] = None
    upcoming = [
        r for r in reminders_raw
        if r.get("next_trigger_at") and isinstance(r["next_trigger_at"], datetime)
    ]
    if upcoming:
        soonest = min(upcoming, key=lambda r: r["next_trigger_at"])
        next_reminder = DashboardNextReminder(
            id=soonest["id"],
            title=soonest.get("title", ""),
            type=soonest.get("type", "medicine"),
            next_trigger_at=soonest["next_trigger_at"],
        )

    # Active medicine names from medicine reminder titles / medicine_details
    active_medicine_names: list[str] = []
    seen_meds: set[str] = set()
    for r in medicine_reminders:
        med = r.get("medicine_details")
        name = (med.get("name") if isinstance(med, dict) else None) or r.get("title", "")
        name = name.strip()
        if name and name.lower() not in seen_meds:
            seen_meds.add(name.lower())
            active_medicine_names.append(name)

    # ── Vitals ───────────────────────────────────────────────────────────────
    latest_vitals: Optional[DashboardVitalsSnapshot] = None
    if vitals_raw and vitals_raw.get("logged_at"):
        all_flags = vitals_raw.get("flags") or []
        abnormal: list[DashboardAbnormalFlag] = []
        for f in all_flags:
            if isinstance(f, dict) and f.get("status", "normal") != "normal":
                try:
                    abnormal.append(DashboardAbnormalFlag(**f))
                except Exception:
                    pass
        try:
            latest_vitals = DashboardVitalsSnapshot(
                logged_at=vitals_raw["logged_at"],
                abnormal_flags=abnormal,
            )
        except Exception as e:
            logger.warning(f"[dashboard] Could not build vitals snapshot: {e}")

    return DashboardStats(
        patient_name=patient_name,
        active_reminders_count=len(reminders_raw),
        medicine_reminders_count=len(medicine_reminders),
        followup_reminders_count=len(followup_reminders),
        next_reminder=next_reminder,
        active_medicine_names=active_medicine_names,
        latest_vitals=latest_vitals,
        generated_at=datetime.now(UTC),
    )
