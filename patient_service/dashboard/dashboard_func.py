import asyncio
import logging
from datetime import datetime, UTC
from typing import Optional

from google.cloud import firestore

from common_code.config import settings
from patient_service.dashboard.dashboard_model import (
    DashboardAbnormalFlag,
    DashboardConsultation,
    DashboardDocument,
    DashboardHealthAlert,
    DashboardNextReminder,
    DashboardStats,
    DashboardVitalsSnapshot,
)

logger = logging.getLogger(__name__)

# ── Firestore field projections ───────────────────────────────────────────────

_REMINDER_FIELDS     = ["type", "status", "title", "next_trigger_at", "medicine_details"]
_DOCUMENT_FIELDS     = ["type", "title", "doctor_name", "document_date", "warnings",
                        "red_flags", "createdAt", "status"]
_CONSULTATION_FIELDS = ["status", "title", "summary", "doctor_name",
                        "key_diagnoses", "created_at"]
_VITALS_FIELDS       = ["logged_at", "flags"]
_PROFILE_FIELDS      = ["name"]


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


async def _fetch_recent_documents(uid: str, db: firestore.AsyncClient) -> list[dict]:
    docs = await (
        db.collection(settings.DOCUMENTS_COLLECTION)
        .where("patientId", "==", uid)
        .order_by("createdAt", direction=firestore.Query.DESCENDING)
        .limit(5)
        .select(_DOCUMENT_FIELDS)
        .get()
    )
    return [{"id": d.id, **d.to_dict()} for d in docs]


async def _fetch_recent_consultation(uid: str, db: firestore.AsyncClient) -> Optional[dict]:
    docs = await (
        db.collection(settings.AUDIO_CONSULTATIONS_COLLECTION)
        .where("patientId", "==", uid)
        .where("status", "==", "completed")
        .order_by("created_at", direction=firestore.Query.DESCENDING)
        .limit(1)
        .select(_CONSULTATION_FIELDS)
        .get()
    )
    if not docs:
        return None
    d = docs[0]
    return {"id": d.id, **d.to_dict()}


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
    reminders_raw, documents_raw, consultation_raw, vitals_raw, patient_name = (
        await asyncio.gather(
            _fetch_reminders(uid, db),
            _fetch_recent_documents(uid, db),
            _fetch_recent_consultation(uid, db),
            _fetch_latest_vitals(uid, db),
            _fetch_profile_name(uid, db),
            return_exceptions=True,
        )
    )

    # Treat any individual query failure gracefully — degrade, don't crash
    reminders_raw    = reminders_raw    if isinstance(reminders_raw, list)       else []
    documents_raw    = documents_raw    if isinstance(documents_raw, list)        else []
    consultation_raw = consultation_raw if isinstance(consultation_raw, dict)     else None
    vitals_raw       = vitals_raw       if isinstance(vitals_raw, dict)           else None
    patient_name     = patient_name     if isinstance(patient_name, str)          else None

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

    # ── Documents ────────────────────────────────────────────────────────────
    recent_documents: list[DashboardDocument] = []
    health_alerts: list[DashboardHealthAlert] = []

    for raw in documents_raw:
        doc_id    = raw.get("id", "")
        created   = raw.get("createdAt")
        if not created:
            continue
        try:
            doc_item = DashboardDocument(
                id=doc_id,
                title=raw.get("title"),
                type=raw.get("type", "other"),
                doctor_name=raw.get("doctor_name"),
                document_date=raw.get("document_date"),
                warnings=raw.get("warnings") or [],
                created_at=created,
            )
            recent_documents.append(doc_item)
        except Exception as e:
            logger.warning(f"[dashboard] Skipping malformed document {doc_id}: {e}")
            continue

        # Surface document warnings as health alerts
        for w in (raw.get("warnings") or []):
            health_alerts.append(DashboardHealthAlert(
                source_type="document",
                source_id=doc_id,
                message=w,
                severity="warning",
            ))

        # Surface red flags as health alerts
        for rf in (raw.get("red_flags") or []):
            if isinstance(rf, str):
                health_alerts.append(DashboardHealthAlert(
                    source_type="document",
                    source_id=doc_id,
                    message=rf,
                    severity="red_flag",
                ))

    # ── Consultation ─────────────────────────────────────────────────────────
    recent_consultation: Optional[DashboardConsultation] = None
    if consultation_raw:
        try:
            recent_consultation = DashboardConsultation(
                id=consultation_raw["id"],
                title=consultation_raw.get("title"),
                summary=consultation_raw.get("summary"),
                doctor_name=consultation_raw.get("doctor_name"),
                key_diagnoses=consultation_raw.get("key_diagnoses") or [],
                created_at=consultation_raw["created_at"],
            )
        except Exception as e:
            logger.warning(f"[dashboard] Could not build consultation card: {e}")

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

        # Critical vitals → health alerts
        for f in abnormal:
            if f.status.startswith("critical"):
                health_alerts.append(DashboardHealthAlert(
                    source_type="vitals",
                    source_id=uid,
                    message=f.message,
                    severity="critical",
                ))

    return DashboardStats(
        patient_name=patient_name,
        active_reminders_count=len(reminders_raw),
        medicine_reminders_count=len(medicine_reminders),
        followup_reminders_count=len(followup_reminders),
        next_reminder=next_reminder,
        active_medicine_names=active_medicine_names,
        recent_documents=recent_documents,
        recent_consultation=recent_consultation,
        health_alerts=health_alerts,
        latest_vitals=latest_vitals,
        generated_at=datetime.now(UTC),
    )
