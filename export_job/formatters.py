import csv
import datetime
import io
from typing import Any, Dict, List


def to_json_safe(obj: Any) -> Any:
    """Recursively converts Firestore types (datetime, geopoint, etc.) to JSON serializable objects."""
    if isinstance(obj, (datetime.datetime, datetime.date)):
        return obj.isoformat()
    if isinstance(obj, dict):
        return {k: to_json_safe(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [to_json_safe(v) for v in obj]
    return obj


def format_vitals_csv(vitals_list: List[Dict[str, Any]]) -> str:
    """Generates a human-friendly CSV spreadsheet from vital logs."""
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow([
        "Timestamp (UTC)",
        "Date",
        "Time",
        "Vital Types",
        "Systolic (mmHg)",
        "Diastolic (mmHg)",
        "Heart Rate (bpm)",
        "SpO2 (%)",
        "Temperature (°C)",
        "Fasting Glucose (mg/dL)",
        "Post-Meal Glucose (mg/dL)",
        "Random Glucose (mg/dL)",
        "Weight (kg)",
        "Height (cm)",
        "BMI",
        "BMI Category",
        "Device Source",
        "Notes",
    ])

    for v in vitals_list:
        measured_at = v.get("measured_at") or v.get("recordedAt") or v.get("logged_at")
        date_str, time_str, ts_str = "", "", ""
        if isinstance(measured_at, datetime.datetime):
            ts_str = measured_at.isoformat()
            date_str = measured_at.strftime("%Y-%m-%d")
            time_str = measured_at.strftime("%H:%M:%S")
        elif isinstance(measured_at, str):
            ts_str = measured_at
            date_str = measured_at[:10]

        vtypes = v.get("vital_types", [])
        if isinstance(vtypes, list):
            vtypes_str = ", ".join(vtypes)
        else:
            vtypes_str = str(vtypes)

        writer.writerow([
            ts_str,
            date_str,
            time_str,
            vtypes_str,
            v.get("systolic") or "",
            v.get("diastolic") or "",
            v.get("heart_rate") or "",
            v.get("spo2") or "",
            v.get("temperature") or "",
            v.get("glucose_fasting") or "",
            v.get("glucose_post_meal") or "",
            v.get("glucose_random") or "",
            v.get("weight") or "",
            v.get("height") or "",
            v.get("bmi") or "",
            v.get("bmi_category") or v.get("category") or "",
            v.get("device_source") or "",
            v.get("notes") or "",
        ])

    return output.getvalue()


def format_reminders_csv(reminders_list: List[Dict[str, Any]]) -> str:
    """Generates a human-friendly CSV spreadsheet from medication and follow-up reminders."""
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow([
        "Title",
        "Type",
        "Status",
        "Schedule Recurrence",
        "Time of Day",
        "Meal Relation",
        "Start Date",
        "End Date",
        "Medicine Name",
        "Dosage",
        "Next Fire Time",
        "Notes",
    ])

    for r in reminders_list:
        sched = r.get("schedule") or {}
        med = r.get("medicineDetails") or r.get("medicine_details") or {}
        writer.writerow([
            r.get("title") or "",
            r.get("type") or "",
            r.get("status") or "",
            sched.get("recurrence") or r.get("recurrence") or "",
            sched.get("time_of_day") or r.get("schedule") or "",
            r.get("mealRelativeTiming") or "",
            r.get("startDate") or sched.get("start_date") or "",
            r.get("endDate") or sched.get("end_date") or "",
            med.get("medicine_name") or r.get("medicineName") or "",
            med.get("dosage") or "",
            r.get("next_trigger_at") or r.get("nextTriggerTime") or "",
            r.get("notes") or "",
        ])

    return output.getvalue()
