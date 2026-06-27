import datetime
from typing import Optional
from google.cloud import firestore

from common_code.config import settings
from patient_service.vitals.vitals_model import (
    DeviceSource,
    VitalFlag,
    VitalLatestResponse,
    VitalTrendPoint,
    VitalTrendResponse,
    VitalType,
    VitalsLogRequest,
    VitalsLogResponse,
)


# ── Reference range checkers (Indian standards) ────────────────────────────────

def _flag(vital: str, value: float, status: str, message: str) -> VitalFlag:
    return VitalFlag(vital=vital, value=value, status=status, message=message)


def _check_bp(systolic: Optional[int], diastolic: Optional[int]) -> list[VitalFlag]:
    flags = []
    if systolic is not None:
        if systolic >= 180:
            flags.append(_flag("systolic", systolic, "critical_high",
                               "Hypertensive crisis (≥180 mmHg) — seek immediate care"))
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
        return _flag("glucose_fasting", g, "critical_low", "Hypoglycaemia (<70 mg/dL) — act immediately")
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


def _check_hba1c(h: float) -> VitalFlag:
    if h < 5.7:
        return _flag("hba1c", h, "normal", "Normal HbA1c (<5.7%)")
    elif h < 6.5:
        return _flag("hba1c", h, "elevated", "Pre-diabetic HbA1c (5.7–6.4%)")
    return _flag("hba1c", h, "high", "Diabetic range HbA1c (≥6.5%)")


def _check_cholesterol_total(c: float) -> VitalFlag:
    if c < 200:
        return _flag("cholesterol_total", c, "normal", "Desirable total cholesterol (<200 mg/dL)")
    elif c < 240:
        return _flag("cholesterol_total", c, "elevated", "Borderline high cholesterol (200–239 mg/dL)")
    return _flag("cholesterol_total", c, "high", "High cholesterol (≥240 mg/dL)")


def _check_ldl(ldl: float) -> VitalFlag:
    if ldl < 100:
        return _flag("cholesterol_ldl", ldl, "normal", "Optimal LDL (<100 mg/dL)")
    elif ldl < 130:
        return _flag("cholesterol_ldl", ldl, "normal", "Near-optimal LDL (100–129 mg/dL)")
    elif ldl < 160:
        return _flag("cholesterol_ldl", ldl, "elevated", "Borderline high LDL (130–159 mg/dL)")
    elif ldl < 190:
        return _flag("cholesterol_ldl", ldl, "high", "High LDL (160–189 mg/dL)")
    return _flag("cholesterol_ldl", ldl, "critical_high", "Very high LDL (≥190 mg/dL)")


def _check_hdl(hdl: float) -> VitalFlag:
    if hdl < 40:
        return _flag("cholesterol_hdl", hdl, "low", "Low HDL — increased cardiac risk (<40 mg/dL)")
    elif hdl >= 60:
        return _flag("cholesterol_hdl", hdl, "normal", "Protective HDL (≥60 mg/dL)")
    return _flag("cholesterol_hdl", hdl, "normal", "Acceptable HDL (40–59 mg/dL)")


def _check_triglycerides(tg: float) -> VitalFlag:
    if tg < 150:
        return _flag("triglycerides", tg, "normal", "Normal triglycerides (<150 mg/dL)")
    elif tg < 200:
        return _flag("triglycerides", tg, "elevated", "Borderline high triglycerides (150–199 mg/dL)")
    elif tg < 500:
        return _flag("triglycerides", tg, "high", "High triglycerides (200–499 mg/dL)")
    return _flag("triglycerides", tg, "critical_high", "Very high triglycerides (≥500 mg/dL)")


def _check_uric_acid(ua: float) -> VitalFlag:
    if ua > 7.0:
        return _flag("uric_acid", ua, "high", "High uric acid — gout risk (>7.0 mg/dL)")
    elif ua < 3.0:
        return _flag("uric_acid", ua, "low", "Low uric acid (<3.0 mg/dL)")
    return _flag("uric_acid", ua, "normal", "Normal uric acid (3.0–7.0 mg/dL)")


def _check_creatinine(cr: float) -> VitalFlag:
    if cr > 1.2:
        return _flag("creatinine", cr, "high", "Elevated creatinine — possible kidney strain (>1.2 mg/dL)")
    elif cr < 0.5:
        return _flag("creatinine", cr, "low", "Low creatinine (<0.5 mg/dL)")
    return _flag("creatinine", cr, "normal", "Normal creatinine (0.5–1.2 mg/dL)")


def _check_egfr(egfr: float) -> VitalFlag:
    if egfr >= 90:
        return _flag("egfr", egfr, "normal", "Normal kidney function (≥90 mL/min/1.73m²)")
    elif egfr >= 60:
        return _flag("egfr", egfr, "elevated", "Mildly reduced kidney function (60–89)")
    elif egfr >= 30:
        return _flag("egfr", egfr, "high", "Moderately reduced kidney function (30–59)")
    return _flag("egfr", egfr, "critical_low", "Severely reduced kidney function (<30)")


def _check_hemoglobin(hb: float) -> VitalFlag:
    if hb < 8:
        return _flag("hemoglobin", hb, "critical_low",
                     "Severe anaemia (<8 g/dL) — consult doctor urgently")
    elif hb < 12:
        return _flag("hemoglobin", hb, "low",
                     "Anaemia (<12 g/dL) — common in India; check iron and B12 levels")
    elif hb <= 17:
        return _flag("hemoglobin", hb, "normal", "Normal haemoglobin (12–17 g/dL)")
    return _flag("hemoglobin", hb, "high", "Elevated haemoglobin (>17 g/dL)")


def _check_waist(wc: float) -> VitalFlag:
    # Indian cut-offs: men >90 cm, women >80 cm
    if wc > 90:
        return _flag("waist_circumference", wc, "high",
                     "High abdominal obesity (Indian cut-off: >90 cm men, >80 cm women)")
    elif wc > 80:
        return _flag("waist_circumference", wc, "elevated",
                     "Borderline abdominal obesity (Indian cut-off: >80 cm women)")
    return _flag("waist_circumference", wc, "normal", "Within healthy waist range")


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


# ── Vital type → field mapping ─────────────────────────────────────────────────

VITAL_FIELD_MAP: dict[str, list[str]] = {
    VitalType.blood_pressure:      ["systolic", "diastolic"],
    VitalType.blood_glucose:       ["glucose_fasting", "glucose_post_meal", "glucose_random"],
    VitalType.heart_rate:          ["heart_rate"],
    VitalType.spo2:                ["spo2"],
    VitalType.temperature:         ["temperature"],
    VitalType.weight_bmi:          ["weight", "height", "bmi", "bmi_category"],
    VitalType.respiratory_rate:    ["respiratory_rate"],
    VitalType.hba1c:               ["hba1c"],
    VitalType.cholesterol:         ["cholesterol_total", "cholesterol_ldl", "cholesterol_hdl", "triglycerides"],
    VitalType.uric_acid:           ["uric_acid"],
    VitalType.creatinine:          ["creatinine", "egfr"],
    VitalType.hemoglobin:          ["hemoglobin"],
    VitalType.waist_circumference: ["waist_circumference"],
}

VITAL_UNITS: dict[str, str] = {
    VitalType.blood_pressure:      "mmHg",
    VitalType.blood_glucose:       "mg/dL",
    VitalType.heart_rate:          "bpm",
    VitalType.spo2:                "%",
    VitalType.temperature:         "°C",
    VitalType.weight_bmi:          "kg / BMI",
    VitalType.respiratory_rate:    "breaths/min",
    VitalType.hba1c:               "%",
    VitalType.cholesterol:         "mg/dL",
    VitalType.uric_acid:           "mg/dL",
    VitalType.creatinine:          "mg/dL",
    VitalType.hemoglobin:          "g/dL",
    VitalType.waist_circumference: "cm",
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
    if req.hba1c is not None:
        flags.append(_check_hba1c(req.hba1c))
    if req.cholesterol_total is not None:
        flags.append(_check_cholesterol_total(req.cholesterol_total))
    if req.cholesterol_ldl is not None:
        flags.append(_check_ldl(req.cholesterol_ldl))
    if req.cholesterol_hdl is not None:
        flags.append(_check_hdl(req.cholesterol_hdl))
    if req.triglycerides is not None:
        flags.append(_check_triglycerides(req.triglycerides))
    if req.uric_acid is not None:
        flags.append(_check_uric_acid(req.uric_acid))
    if req.creatinine is not None:
        flags.append(_check_creatinine(req.creatinine))
    if req.egfr is not None:
        flags.append(_check_egfr(req.egfr))
    if req.hemoglobin is not None:
        flags.append(_check_hemoglobin(req.hemoglobin))
    if req.waist_circumference is not None:
        flags.append(_check_waist(req.waist_circumference))
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
        hba1c=d.get("hba1c"),
        cholesterol_total=d.get("cholesterol_total"),
        cholesterol_ldl=d.get("cholesterol_ldl"),
        cholesterol_hdl=d.get("cholesterol_hdl"),
        triglycerides=d.get("triglycerides"),
        uric_acid=d.get("uric_acid"),
        creatinine=d.get("creatinine"),
        egfr=d.get("egfr"),
        hemoglobin=d.get("hemoglobin"),
        waist_circumference=d.get("waist_circumference"),
        notes=d.get("notes"),
        device_source=d.get("device_source"),
        measured_at=measured_at,
        logged_at=logged_at,
        flags=[VitalFlag(**f) for f in d.get("flags", [])],
    )


# ── CRUD ──────────────────────────────────────────────────────────────────────

async def log_vitals(uid: str, req: VitalsLogRequest, db: firestore.AsyncClient) -> VitalsLogResponse:
    now         = datetime.datetime.now(datetime.UTC)
    measured_at = req.measured_at or now
    vital_types = _infer_vital_types(req)
    flags       = _compute_flags(req)

    bmi, bmi_category = None, None
    if req.weight and req.height:
        bmi, bmi_category = _compute_bmi(req.weight, req.height)
        await db.collection(settings.PATIENTS_COLLECTION).document(uid).update({
            "height": req.height,
            "weight": req.weight,
        })

    # Only store non-None vital values — keeps documents lean
    vital_data: dict = {}
    for field in [
        "systolic", "diastolic", "heart_rate", "spo2", "temperature",
        "weight", "height", "respiratory_rate",
        "glucose_fasting", "glucose_post_meal", "glucose_random",
        "hba1c", "cholesterol_total", "cholesterol_ldl", "cholesterol_hdl",
        "triglycerides", "uric_acid", "creatinine", "egfr",
        "hemoglobin", "waist_circumference",
    ]:
        val = getattr(req, field)
        if val is not None:
            vital_data[field] = val

    if bmi is not None:
        vital_data["bmi"]          = bmi
        vital_data["bmi_category"] = bmi_category

    data = {
        "patientId":     uid,
        "vital_types":   vital_types,
        "measured_at":   measured_at,
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
        **{k: v for k, v in vital_data.items() if k not in ("bmi", "bmi_category")},
    )


async def list_vitals(
    uid: str,
    vital_type: Optional[str],
    limit: int,
    db: firestore.AsyncClient,
) -> list[VitalsLogResponse]:
    query = db.collection(settings.VITALS_COLLECTION).where("patientId", "==", uid)
    if vital_type:
        if vital_type not in VITAL_FIELD_MAP:
            raise ValueError(f"Unknown vital_type: {vital_type}")
        query = query.where("vital_types", "array_contains", vital_type)
    query = query.order_by("measured_at", direction=firestore.Query.DESCENDING).limit(limit)
    docs  = await query.get()
    return [_doc_to_response(doc.id, doc.to_dict()) for doc in docs]


async def get_latest_vitals(uid: str, db: firestore.AsyncClient) -> list[VitalLatestResponse]:
    """One latest reading per vital type — scans the most recent 200 entries."""
    docs = await (
        db.collection(settings.VITALS_COLLECTION)
        .where("patientId", "==", uid)
        .order_by("measured_at", direction=firestore.Query.DESCENDING)
        .limit(200)
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

    since = datetime.datetime.now(datetime.UTC) - datetime.timedelta(days=days)

    # Compound query: array_contains + range filter requires a Firestore composite index
    # Index: (patientId ASC, vital_types ARRAY, measured_at ASC)
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
