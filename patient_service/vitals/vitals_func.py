import datetime
from datetime import timezone, timedelta
from typing import List, Optional, Tuple
from google.cloud import firestore

from common_code.config import settings
from patient_service.vitals.vitals_model import (
    DeviceSource,
    VitalFlag,
    VitalLatestResponse,
    VitalTrendPoint,
    VitalTrendResponse,
    VitalType,
    VitalsListResponse,
    VitalsLogRequest,
    VitalsLogResponse,
)


# ── Reference range checkers (Indian clinical standards) ──────────────────────

def _flag(vital: str, value: float, status: str, message: str) -> VitalFlag:
    return VitalFlag(vital=vital, value=value, status=status, message=message)


def _check_bp(systolic: Optional[int], diastolic: Optional[int]) -> list[VitalFlag]:
    flags = []
    if systolic is not None:
        if systolic >= 180:
            flags.append(_flag("systolic", systolic, "critical_high",
                               "Hypertensive crisis (≥180 mmHg) — seek immediate medical attention"))
        elif systolic >= 140:
            flags.append(_flag("systolic", systolic, "high",
                               "Stage 2 hypertension (140–179 mmHg)"))
        elif systolic >= 130:
            flags.append(_flag("systolic", systolic, "elevated",
                               "Stage 1 hypertension (130–139 mmHg)"))
        elif systolic >= 120:
            flags.append(_flag("systolic", systolic, "elevated",
                               "Elevated blood pressure (120–129 mmHg)"))
        else:
            flags.append(_flag("systolic", systolic, "normal", "Normal (<120 mmHg)"))
    if diastolic is not None:
        if diastolic >= 120:
            flags.append(_flag("diastolic", diastolic, "critical_high",
                               "Hypertensive crisis diastolic (≥120 mmHg)"))
        elif diastolic >= 90:
            flags.append(_flag("diastolic", diastolic, "high",
                               "Stage 2 hypertension diastolic (≥90 mmHg)"))
        elif diastolic >= 80:
            flags.append(_flag("diastolic", diastolic, "elevated",
                               "Stage 1 hypertension diastolic (80–89 mmHg)"))
        else:
            flags.append(_flag("diastolic", diastolic, "normal", "Normal (<80 mmHg)"))
    return flags


def _check_heart_rate(hr: int) -> VitalFlag:
    if hr < 40:
        return _flag("heart_rate", hr, "critical_low", "Severe bradycardia (<40 bpm)")
    elif hr < 60:
        return _flag("heart_rate", hr, "low", "Bradycardia (<60 bpm)")
    elif hr > 150:
        return _flag("heart_rate", hr, "critical_high", "Severe tachycardia (>150 bpm)")
    elif hr > 100:
        return _flag("heart_rate", hr, "high", "Tachycardia (>100 bpm)")
    return _flag("heart_rate", hr, "normal", "Normal (60–100 bpm)")


def _check_spo2(spo2: float) -> VitalFlag:
    if spo2 < 90:
        return _flag("spo2", spo2, "critical_low", "Severe hypoxaemia (<90%) — seek immediate care")
    elif spo2 < 95:
        return _flag("spo2", spo2, "low", "Low oxygen saturation (90–94%)")
    return _flag("spo2", spo2, "normal", "Normal (≥95%)")


def _check_temperature(temp: float) -> VitalFlag:
    if temp < 35:
        return _flag("temperature", temp, "critical_low", "Hypothermia (<35°C)")
    elif temp < 36.1:
        return _flag("temperature", temp, "low", "Below normal (normal: 36.1–37.2°C)")
    elif temp <= 37.2:
        return _flag("temperature", temp, "normal", "Normal (36.1–37.2°C)")
    elif temp <= 38:
        return _flag("temperature", temp, "elevated", "Low-grade fever (37.3–38°C)")
    elif temp <= 39:
        return _flag("temperature", temp, "high", "Fever (38–39°C)")
    return _flag("temperature", temp, "critical_high", "High fever (>39°C)")


def _check_respiratory(rr: int) -> VitalFlag:
    if rr < 10:
        return _flag("respiratory_rate", rr, "critical_low", "Critically low (<10 breaths/min)")
    elif rr < 12:
        return _flag("respiratory_rate", rr, "low", "Below normal (normal: 12–20)")
    elif rr <= 20:
        return _flag("respiratory_rate", rr, "normal", "Normal (12–20 breaths/min)")
    elif rr <= 25:
        return _flag("respiratory_rate", rr, "elevated", "Mildly elevated (21–25 breaths/min)")
    return _flag("respiratory_rate", rr, "high", "Elevated (>25 breaths/min)")


def _check_glucose_fasting(g: float) -> VitalFlag:
    if g < 70:
        return _flag("glucose_fasting", g, "critical_low", "Hypoglycaemia (<70 mg/dL) — take fast-acting sugar")
    elif g <= 100:
        return _flag("glucose_fasting", g, "normal", "Normal fasting glucose (70–100 mg/dL)")
    elif g <= 125:
        return _flag("glucose_fasting", g, "elevated", "Pre-diabetic fasting glucose (101–125 mg/dL)")
    return _flag("glucose_fasting", g, "high", "Diabetic range fasting glucose (≥126 mg/dL)")


def _check_glucose_post_meal(g: float) -> VitalFlag:
    if g < 70:
        return _flag("glucose_post_meal", g, "critical_low", "Hypoglycaemia (<70 mg/dL)")
    elif g < 140:
        return _flag("glucose_post_meal", g, "normal", "Normal post-meal glucose (<140 mg/dL)")
    elif g < 200:
        return _flag("glucose_post_meal", g, "elevated", "Pre-diabetic post-meal glucose (140–199 mg/dL)")
    return _flag("glucose_post_meal", g, "high", "Diabetic range post-meal glucose (≥200 mg/dL)")


def _check_glucose_random(g: float) -> VitalFlag:
    if g < 70:
        return _flag("glucose_random", g, "critical_low", "Hypoglycaemia (<70 mg/dL)")
    elif g < 140:
        return _flag("glucose_random", g, "normal", "Normal random glucose (<140 mg/dL)")
    elif g < 200:
        return _flag("glucose_random", g, "elevated", "Borderline random glucose (140–199 mg/dL)")
    return _flag("glucose_random", g, "high", "Diabetic range random glucose (≥200 mg/dL)")


# ── BMI (Indian cut-offs) ──────────────────────────────────────────────────────

def _compute_bmi(weight: float, height: float) -> tuple[float, str]:
    bmi = round(weight / (height / 100) ** 2, 1)
    if bmi < 18.5:
        category = "Underweight"
    elif bmi < 23.0:
        category = "Normal"
    elif bmi < 25.0:
        category = "Overweight"
    else:
        category = "Obese"
    return bmi, category


# ── Vital type mappings ────────────────────────────────────────────────────────

VITAL_FIELD_MAP: dict[str, list[str]] = {
    VitalType.blood_pressure:   ["systolic", "diastolic"],
    VitalType.blood_glucose:    ["glucose_fasting", "glucose_post_meal", "glucose_random"],
    VitalType.heart_rate:       ["heart_rate"],
    VitalType.spo2:             ["spo2"],
    VitalType.temperature:      ["temperature"],
    VitalType.weight_bmi:       ["weight", "height", "bmi", "bmi_category"],
    VitalType.respiratory_rate: ["respiratory_rate"],
}

VITAL_UNITS: dict[str, str] = {
    VitalType.blood_pressure:   "mmHg",
    VitalType.blood_glucose:    "mg/dL",
    VitalType.heart_rate:       "bpm",
    VitalType.spo2:             "%",
    VitalType.temperature:      "°C",
    VitalType.weight_bmi:       "kg / BMI",
    VitalType.respiratory_rate: "breaths/min",
}


def _infer_vital_types(req: VitalsLogRequest) -> list[str]:
    req_dict = req.model_dump(exclude_none=True)
    return [
        vtype for vtype, fields in VITAL_FIELD_MAP.items()
        if any(f in req_dict for f in fields if f not in ("bmi", "bmi_category"))
    ]


def _compute_flags(req: VitalsLogRequest) -> list[VitalFlag]:
    flags: list[VitalFlag] = []
    if req.systolic is not None or req.diastolic is not None:
        flags.extend(_check_bp(req.systolic, req.diastolic))
    if req.heart_rate is not None:
        flags.append(_check_heart_rate(req.heart_rate))
    if req.spo2 is not None:
        flags.append(_check_spo2(req.spo2))
    if req.temperature is not None:
        flags.append(_check_temperature(req.temperature))
    if req.respiratory_rate is not None:
        flags.append(_check_respiratory(req.respiratory_rate))
    if req.glucose_fasting is not None:
        flags.append(_check_glucose_fasting(req.glucose_fasting))
    if req.glucose_post_meal is not None:
        flags.append(_check_glucose_post_meal(req.glucose_post_meal))
    if req.glucose_random is not None:
        flags.append(_check_glucose_random(req.glucose_random))
    return flags


def _doc_to_response(doc_id: str, d: dict) -> VitalsLogResponse:
    measured_at = d.get("measured_at") or d.get("logged_at")
    logged_at   = d.get("logged_at")   or d.get("measured_at")
    return VitalsLogResponse(
        id=doc_id,
        patient_id=d.get("patientId", ""),
        vital_types=d.get("vital_types", []),
        systolic=d.get("systolic"),
        diastolic=d.get("diastolic"),
        heart_rate=d.get("heart_rate"),
        spo2=d.get("spo2"),
        temperature=d.get("temperature"),
        weight=d.get("weight"),
        height=d.get("height"),
        bmi=d.get("bmi"),
        bmi_category=d.get("bmi_category"),
        respiratory_rate=d.get("respiratory_rate"),
        glucose_fasting=d.get("glucose_fasting"),
        glucose_post_meal=d.get("glucose_post_meal"),
        glucose_random=d.get("glucose_random"),
        notes=d.get("notes"),
        device_source=d.get("device_source"),
        measured_at=measured_at,
        logged_at=logged_at,
        flags=[VitalFlag(**f) for f in d.get("flags", [])],
    )


# ── CRUD Operations ────────────────────────────────────────────────────────────

async def log_vitals(uid: str, req: VitalsLogRequest, db: firestore.AsyncClient) -> VitalsLogResponse:
    now = datetime.datetime.now(timezone.utc)
    measured_at = req.measured_at or now
    vital_types = _infer_vital_types(req)
    flags       = _compute_flags(req)

    effective_weight = req.weight
    effective_height = req.height

    # If only weight or only height is provided, fetch the other from patient profile to compute BMI
    if (effective_weight and not effective_height) or (effective_height and not effective_weight):
        try:
            pat_snap = await db.collection(settings.PATIENTS_COLLECTION).document(uid).get()
            if pat_snap.exists:
                pat_data = pat_snap.to_dict() or {}
                if not effective_height and pat_data.get("height"):
                    effective_height = float(pat_data["height"])
                if not effective_weight and pat_data.get("weight"):
                    effective_weight = float(pat_data["weight"])
        except Exception:
            pass

    bmi, bmi_category = None, None
    if effective_weight and effective_height:
        bmi, bmi_category = _compute_bmi(effective_weight, effective_height)

    # Sync patient profile
    profile_update: dict = {}
    if req.height is not None:
        profile_update["height"] = req.height
    if req.weight is not None:
        profile_update["weight"] = req.weight
    if bmi is not None:
        profile_update["bmi"] = bmi
        profile_update["bmi_category"] = bmi_category

    if profile_update:
        await db.collection(settings.PATIENTS_COLLECTION).document(uid).set(
            profile_update,
            merge=True,
        )

    vital_data: dict = {}
    for field in [
        "systolic", "diastolic", "heart_rate", "spo2", "temperature",
        "weight", "height", "respiratory_rate",
        "glucose_fasting", "glucose_post_meal", "glucose_random",
    ]:
        val = getattr(req, field)
        if val is not None:
            vital_data[field] = val

    # Include effective height if weight was logged and height is known
    if "height" not in vital_data and effective_height is not None:
        vital_data["height"] = effective_height

    if bmi is not None:
        vital_data["bmi"]          = bmi
        vital_data["bmi_category"] = bmi_category
        vital_data["category"]     = bmi_category

    data = {
        "patientId":     uid,
        "vital_types":   vital_types,
        "measured_at":   measured_at,
        "recordedAt":    measured_at,  # ensures legacy /profile/vitals/history compatibility
        "logged_at":     now,
        "device_source": (req.device_source or DeviceSource.manual).value,
        "flags":         [f.model_dump() for f in flags],
        **vital_data,
    }
    if req.notes:
        data["notes"] = req.notes

    doc_ref = await db.collection(settings.VITALS_COLLECTION).add(data)

    return VitalsLogResponse(
        id=doc_ref[1].id,
        patient_id=uid,
        vital_types=vital_types,
        bmi=bmi,
        bmi_category=bmi_category,
        notes=req.notes,
        device_source=(req.device_source or DeviceSource.manual).value,
        measured_at=measured_at,
        logged_at=now,
        flags=flags,
        **{k: v for k, v in vital_data.items() if k not in ("bmi", "bmi_category", "category")},
    )


async def list_vitals(
    uid: str,
    vital_type: Optional[str] = None,
    from_date: Optional[datetime.date] = None,
    to_date: Optional[datetime.date] = None,
    cursor: Optional[str] = None,
    limit: int = 50,
    db: Optional[firestore.AsyncClient] = None,
) -> VitalsListResponse:
    query = db.collection(settings.VITALS_COLLECTION).where("patientId", "==", uid)
    if vital_type:
        if vital_type not in VITAL_FIELD_MAP:
            raise ValueError(f"Unknown vital_type: '{vital_type}'. Valid values: {list(VITAL_FIELD_MAP)}")
        query = query.where("vital_types", "array_contains", vital_type)

    if from_date:
        from_dt = datetime.datetime.combine(from_date, datetime.time.min, tzinfo=timezone.utc)
        query = query.where("measured_at", ">=", from_dt)

    if to_date:
        to_dt = datetime.datetime.combine(to_date, datetime.time.max, tzinfo=timezone.utc)
        query = query.where("measured_at", "<=", to_dt)

    if cursor:
        try:
            cursor_dt = datetime.datetime.fromtimestamp(float(cursor), tz=timezone.utc) - timedelta(microseconds=1)
            query = query.where("measured_at", "<=", cursor_dt)
        except Exception:
            pass

    query = query.order_by("measured_at", direction=firestore.Query.DESCENDING).limit(limit + 1)
    docs  = await query.get()

    has_more = len(docs) > limit
    page_docs = docs[:limit]
    items = [_doc_to_response(doc.id, doc.to_dict()) for doc in page_docs]

    next_cursor = None
    if has_more and items:
        last_dt = items[-1].measured_at
        if last_dt.tzinfo is None:
            last_dt = last_dt.replace(tzinfo=timezone.utc)
        next_cursor = str(last_dt.timestamp())

    return VitalsListResponse(
        items=items,
        next_cursor=next_cursor,
        has_more=has_more,
        total_count=len(items),
    )


async def get_latest_vitals(uid: str, db: firestore.AsyncClient) -> list[VitalLatestResponse]:
    """One latest reading per vital type — scans the most recent entries."""
    docs = await (
        db.collection(settings.VITALS_COLLECTION)
        .where("patientId", "==", uid)
        .order_by("measured_at", direction=firestore.Query.DESCENDING)
        .limit(100)
        .get()
    )

    seen:   set[str]               = set()
    latest: list[VitalLatestResponse] = []

    for doc in docs:
        d = doc.to_dict()
        for vtype in d.get("vital_types", []):
            if vtype in seen:
                continue
            seen.add(vtype)
            fields = VITAL_FIELD_MAP.get(vtype, [])
            values = {f: d[f] for f in fields if f in d}
            if not values:
                continue
            point_flags = [
                VitalFlag(**f) for f in d.get("flags", [])
                if f.get("vital") in values
            ]
            measured_at = d.get("measured_at") or d.get("logged_at")
            logged_at   = d.get("logged_at")   or d.get("measured_at")
            latest.append(VitalLatestResponse(
                vital_type=vtype,
                measured_at=measured_at,
                logged_at=logged_at,
                values=values,
                flags=point_flags,
            ))

    return latest


async def get_vital_trend(
    uid: str,
    vital_type: str,
    days: int,
    db: firestore.AsyncClient,
) -> VitalTrendResponse:
    if vital_type not in VITAL_FIELD_MAP:
        raise ValueError(f"Unknown vital_type: '{vital_type}'. Valid values: {list(VITAL_FIELD_MAP)}")

    since = datetime.datetime.now(timezone.utc) - datetime.timedelta(days=days)

    docs = await (
        db.collection(settings.VITALS_COLLECTION)
        .where("patientId", "==", uid)
        .where("vital_types", "array_contains", vital_type)
        .where("measured_at", ">=", since)
        .order_by("measured_at", direction=firestore.Query.ASCENDING)
        .get()
    )

    fields = VITAL_FIELD_MAP[vital_type]
    points: list[VitalTrendPoint] = []
    for doc in docs:
        d      = doc.to_dict()
        values = {f: d[f] for f in fields if f in d}
        if not values:
            continue
        point_flags = [
            VitalFlag(**f) for f in d.get("flags", [])
            if f.get("vital") in values
        ]
        points.append(VitalTrendPoint(
            measured_at=d.get("measured_at") or d.get("logged_at"),
            values=values,
            flags=point_flags,
        ))

    return VitalTrendResponse(
        vital_type=vital_type,
        unit=VITAL_UNITS.get(vital_type, ""),
        points=points,
    )


async def delete_vital_entry(uid: str, entry_id: str, db: firestore.AsyncClient) -> None:
    snap = await db.collection(settings.VITALS_COLLECTION).document(entry_id).get()
    if not snap.exists:
        raise ValueError(f"Vitals entry '{entry_id}' not found.")
    if snap.to_dict().get("patientId") != uid:
        raise PermissionError("You do not have permission to delete this vitals entry.")
    await db.collection(settings.VITALS_COLLECTION).document(entry_id).delete()
