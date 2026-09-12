"""
Gemini function-calling tools for the chatbot.

Gemini decides autonomously (mode=AUTO) when a user prompt requires
a tool call vs. a plain RAG answer. Tools execute real backend logic
and return a text result that is fed back to Gemini for a natural
language response.
"""

import asyncio
import datetime
from typing import Optional
from zoneinfo import ZoneInfo

from google import genai
from google.genai import types as genai_types
from google.cloud import firestore

from common_code.config import settings

IST = ZoneInfo("Asia/Kolkata")

import logging
logger = logging.getLogger(__name__)


# ══════════════════════════════════════════════════════════════
#  Tool declarations (sent to Gemini)
# ══════════════════════════════════════════════════════════════

CHATBOT_TOOLS = genai_types.Tool(
    function_declarations=[

        # ── Vitals Tools ───────────────────────────────────────
        genai_types.FunctionDeclaration(
            name="log_vitals",
            description=(
                "Log one or more patient vital signs into their health profile. "
                "Supported vitals: weight (kg), height (cm), blood pressure (systolic/diastolic in mmHg), "
                "heart rate/pulse (bpm), blood glucose/sugar (fasting, post-meal, or random in mg/dL), "
                "oxygen saturation SpO2 (%), body temperature (°C or °F), and respiratory rate. "
                "Use when the patient provides a vital reading, e.g. 'Add weight 50kg', 'Log BP 120/80', "
                "'My fasting sugar is 95', 'Record temperature 98.6'."
            ),
            parameters=genai_types.Schema(
                type="OBJECT",
                properties={
                    "weight": genai_types.Schema(
                        type="NUMBER",
                        description="Body weight in kilograms (e.g. 50.0 or 72.5)",
                    ),
                    "height": genai_types.Schema(
                        type="NUMBER",
                        description="Height in centimeters (e.g. 165.0 or 175.0)",
                    ),
                    "systolic": genai_types.Schema(
                        type="INTEGER",
                        description="Systolic blood pressure in mmHg (e.g. 120)",
                    ),
                    "diastolic": genai_types.Schema(
                        type="INTEGER",
                        description="Diastolic blood pressure in mmHg (e.g. 80)",
                    ),
                    "heart_rate": genai_types.Schema(
                        type="INTEGER",
                        description="Heart rate / pulse in beats per minute (e.g. 72)",
                    ),
                    "glucose_fasting": genai_types.Schema(
                        type="NUMBER",
                        description="Fasting blood glucose in mg/dL (e.g. 95.0)",
                    ),
                    "glucose_post_meal": genai_types.Schema(
                        type="NUMBER",
                        description="Post-meal blood glucose in mg/dL (e.g. 140.0)",
                    ),
                    "glucose_random": genai_types.Schema(
                        type="NUMBER",
                        description="Random blood glucose in mg/dL",
                    ),
                    "spo2": genai_types.Schema(
                        type="NUMBER",
                        description="Oxygen saturation SpO2 percentage (e.g. 98.0)",
                    ),
                    "temperature": genai_types.Schema(
                        type="NUMBER",
                        description="Body temperature in °C or °F (e.g. 37.0 or 98.6)",
                    ),
                    "respiratory_rate": genai_types.Schema(
                        type="INTEGER",
                        description="Respiratory rate in breaths per minute (e.g. 16)",
                    ),
                    "notes": genai_types.Schema(
                        type="STRING",
                        description="Optional patient note or context (e.g. 'After morning walk')",
                    ),
                },
            ),
        ),

        genai_types.FunctionDeclaration(
            name="get_latest_vitals",
            description=(
                "Retrieve the patient's most recently recorded vital signs (blood pressure, "
                "blood sugar, heart rate, SpO2, temperature, weight/BMI). "
                "Use when the patient asks 'What was my last BP?', 'Show my recent vitals', etc."
            ),
            parameters=genai_types.Schema(type="OBJECT", properties={}),
        ),

        genai_types.FunctionDeclaration(
            name="get_vital_trend",
            description=(
                "Retrieve historical time-series trend data for a specific vital over the past N days. "
                "Use when the patient asks 'How has my blood pressure been this month?' or 'Show my glucose trend'."
            ),
            parameters=genai_types.Schema(
                type="OBJECT",
                properties={
                    "vital_type": genai_types.Schema(
                        type="STRING",
                        description="One of: 'blood_pressure', 'blood_glucose', 'heart_rate', 'spo2', 'temperature', 'weight_bmi', 'respiratory_rate'",
                    ),
                    "days": genai_types.Schema(
                        type="INTEGER",
                        description="Number of past days to query (default 30)",
                    ),
                },
                required=["vital_type"],
            ),
        ),

        # ── Document Tools ─────────────────────────────────────
        genai_types.FunctionDeclaration(
            name="list_documents",
            description=(
                "List all medical documents, reports, and lab results that the patient "
                "has uploaded. Use this when the patient asks to see, show, or list their "
                "documents, files, reports, or records."
            ),
            parameters=genai_types.Schema(type="OBJECT", properties={}),
        ),

        # ── Reminder Tools ─────────────────────────────────────
        genai_types.FunctionDeclaration(
            name="list_reminders",
            description=(
                "List the patient's medication or follow-up reminders. "
                "Use when the patient asks to see or list their reminders."
            ),
            parameters=genai_types.Schema(
                type="OBJECT",
                properties={
                    "status": genai_types.Schema(
                        type="STRING",
                        description="Optional filter: active, paused, expired, cancelled",
                    ),
                },
            ),
        ),

        genai_types.FunctionDeclaration(
            name="create_reminder",
            description=(
                "Create a medication or follow-up reminder for the patient. "
                "Use when the patient asks to add, set, or create a reminder. "
                "Infer today's date for start_date if not specified."
            ),
            parameters=genai_types.Schema(
                type="OBJECT",
                properties={
                    "title": genai_types.Schema(
                        type="STRING",
                        description="Short reminder title, e.g. 'Take Metformin'",
                    ),
                    "type": genai_types.Schema(
                        type="STRING",
                        description="'medicine' for a medication reminder, 'follow_up' for a doctor visit",
                    ),
                    "meal_timing": genai_types.Schema(
                        type="STRING",
                        description=(
                            "Meal-relative timing: 'before_breakfast', 'after_breakfast', "
                            "'before_lunch', 'after_lunch', 'before_dinner', 'after_dinner'. "
                            "Use this instead of time_of_day when the patient says things like "
                            "'after breakfast' or 'before dinner'. Omit for a specific clock time."
                        ),
                    ),
                    "time_of_day": genai_types.Schema(
                        type="STRING",
                        description="Time in HH:MM 24-hour format, e.g. '09:00'. Omit when meal_timing is set.",
                    ),
                    "recurrence": genai_types.Schema(
                        type="STRING",
                        description="Frequency: 'once', 'daily', 'weekly', or 'monthly'",
                    ),
                    "start_date": genai_types.Schema(
                        type="STRING",
                        description="Start date in YYYY-MM-DD format",
                    ),
                    "end_date": genai_types.Schema(
                        type="STRING",
                        description="Optional end date YYYY-MM-DD; omit for indefinite reminders",
                    ),
                    "notes": genai_types.Schema(
                        type="STRING",
                        description="Optional instructions, e.g. 'after meals', 'with water'",
                    ),
                    "medicine_name": genai_types.Schema(
                        type="STRING",
                        description="Medicine name if type is 'medicine'",
                    ),
                    "dosage": genai_types.Schema(
                        type="STRING",
                        description="Dosage string, e.g. '500mg', '1 tablet'",
                    ),
                },
                required=["title", "time_of_day", "recurrence", "start_date"],
            ),
        ),

        genai_types.FunctionDeclaration(
            name="delete_reminder",
            description=(
                "Cancel or delete a reminder by its ID. "
                "Use when the patient explicitly asks to remove or cancel a specific reminder. "
                "First call list_reminders to get the ID if not provided."
            ),
            parameters=genai_types.Schema(
                type="OBJECT",
                properties={
                    "reminder_id": genai_types.Schema(
                        type="STRING",
                        description="The reminder ID to cancel",
                    ),
                },
                required=["reminder_id"],
            ),
        ),

        genai_types.FunctionDeclaration(
            name="update_reminder",
            description=(
                "Pause, resume, or modify an existing reminder. "
                "Use when the patient asks to pause, resume, change time, or modify a reminder."
            ),
            parameters=genai_types.Schema(
                type="OBJECT",
                properties={
                    "reminder_id": genai_types.Schema(
                        type="STRING",
                        description="The reminder ID to update",
                    ),
                    "status": genai_types.Schema(
                        type="STRING",
                        description="'paused' to pause, 'active' to resume",
                    ),
                    "title": genai_types.Schema(
                        type="STRING",
                        description="New title if the patient wants to rename it",
                    ),
                    "notes": genai_types.Schema(
                        type="STRING",
                        description="Updated notes or instructions",
                    ),
                },
                required=["reminder_id"],
            ),
        ),

        # ── Consultation Tools ─────────────────────────────────
        genai_types.FunctionDeclaration(
            name="list_consultations",
            description=(
                "List all doctor visit recordings (audio consultations) the patient has saved. "
                "Use when the patient asks to see, show, or list their consultations, "
                "doctor visits, recordings, or appointments."
            ),
            parameters=genai_types.Schema(type="OBJECT", properties={}),
        ),

        genai_types.FunctionDeclaration(
            name="get_consultation",
            description=(
                "Get the full details of a specific audio consultation: summary, diagnoses, "
                "ICD codes, prescribed medicines, and reminder suggestions. "
                "Use when the patient asks about a specific consultation or visit. "
                "Call list_consultations first if the ID is unknown."
            ),
            parameters=genai_types.Schema(
                type="OBJECT",
                properties={
                    "consultation_id": genai_types.Schema(
                        type="STRING",
                        description="The consultation ID to retrieve",
                    ),
                },
                required=["consultation_id"],
            ),
        ),
    ]
)

_TOOL_CONFIG = genai_types.ToolConfig(
    function_calling_config=genai_types.FunctionCallingConfig(mode="AUTO"),
)


# ══════════════════════════════════════════════════════════════
#  Tool executor
# ══════════════════════════════════════════════════════════════

async def execute_tool(
    tool_name: str,
    args: dict,
    uid: str,
    db: firestore.AsyncClient,
) -> str:
    """Dispatches a Gemini function call to the appropriate backend handler."""
    try:
        if tool_name == "log_vitals":
            return await _tool_log_vitals(uid, args, db)
        elif tool_name == "get_latest_vitals":
            return await _tool_get_latest_vitals(uid, db)
        elif tool_name == "get_vital_trend":
            return await _tool_get_vital_trend(uid, args, db)
        elif tool_name == "list_documents":
            return await _tool_list_documents(uid, db)
        elif tool_name == "list_reminders":
            return await _tool_list_reminders(uid, args, db)
        elif tool_name == "create_reminder":
            return await _tool_create_reminder(uid, args, db)
        elif tool_name == "delete_reminder":
            return await _tool_delete_reminder(uid, args, db)
        elif tool_name == "update_reminder":
            return await _tool_update_reminder(uid, args, db)
        elif tool_name == "list_consultations":
            return await _tool_list_consultations(uid, db)
        elif tool_name == "get_consultation":
            return await _tool_get_consultation(uid, args, db)
        else:
            return f"Unknown tool: {tool_name}"
    except Exception as e:
        logger.error(f"Tool execution error [{tool_name}]: {e}")
        return f"Error executing {tool_name}: {str(e)}"


async def _tool_log_vitals(uid: str, args: dict, db: firestore.AsyncClient) -> str:
    from patient_service.vitals.vitals_func import log_vitals
    from patient_service.vitals.vitals_model import VitalsLogRequest, DeviceSource

    # Sanitize and convert types
    req = VitalsLogRequest(
        weight=float(args["weight"]) if args.get("weight") is not None else None,
        height=float(args["height"]) if args.get("height") is not None else None,
        systolic=int(args["systolic"]) if args.get("systolic") is not None else None,
        diastolic=int(args["diastolic"]) if args.get("diastolic") is not None else None,
        heart_rate=int(args["heart_rate"]) if args.get("heart_rate") is not None else None,
        glucose_fasting=float(args["glucose_fasting"]) if args.get("glucose_fasting") is not None else None,
        glucose_post_meal=float(args["glucose_post_meal"]) if args.get("glucose_post_meal") is not None else None,
        glucose_random=float(args["glucose_random"]) if args.get("glucose_random") is not None else None,
        spo2=float(args["spo2"]) if args.get("spo2") is not None else None,
        temperature=float(args["temperature"]) if args.get("temperature") is not None else None,
        respiratory_rate=int(args["respiratory_rate"]) if args.get("respiratory_rate") is not None else None,
        notes=args.get("notes"),
        device_source=DeviceSource.manual,
    )

    res = await log_vitals(uid, req, db)
    lines = ["✅ Vital signs recorded successfully:\n"]

    if res.weight is not None:
        lines.append(f"• Weight: {res.weight} kg")
    if res.height is not None:
        lines.append(f"• Height: {res.height} cm")
    if res.bmi is not None:
        lines.append(f"• Calculated BMI: {res.bmi} ({res.bmi_category})")
    if res.systolic is not None and res.diastolic is not None:
        lines.append(f"• Blood Pressure: {res.systolic}/{res.diastolic} mmHg")
    elif res.systolic is not None:
        lines.append(f"• Systolic BP: {res.systolic} mmHg")
    if res.heart_rate is not None:
        lines.append(f"• Heart Rate: {res.heart_rate} bpm")
    if res.glucose_fasting is not None:
        lines.append(f"• Fasting Blood Sugar: {res.glucose_fasting} mg/dL")
    if res.glucose_post_meal is not None:
        lines.append(f"• Post-meal Blood Sugar: {res.glucose_post_meal} mg/dL")
    if res.glucose_random is not None:
        lines.append(f"• Random Blood Sugar: {res.glucose_random} mg/dL")
    if res.spo2 is not None:
        lines.append(f"• Oxygen Saturation (SpO2): {res.spo2}%")
    if res.temperature is not None:
        lines.append(f"• Temperature: {res.temperature} °C")
    if res.respiratory_rate is not None:
        lines.append(f"• Respiratory Rate: {res.respiratory_rate} breaths/min")

    if res.flags:
        lines.append("\nClinical Observations:")
        for f in res.flags:
            badge = "⚠️ " if f.status in ("elevated", "high", "critical_high", "critical_low") else "ℹ️ "
            lines.append(f"  {badge}{f.message}")

    return "\n".join(lines)


async def _tool_get_latest_vitals(uid: str, db: firestore.AsyncClient) -> str:
    from patient_service.vitals.vitals_func import get_latest_vitals
    vitals = await get_latest_vitals(uid, db)
    if not vitals:
        return "No vitals recorded yet. You can log them by asking me (e.g. 'Add weight 65kg', 'Log BP 120/80')."

    lines = ["Here are your latest recorded vitals:\n"]
    for v in vitals:
        v_name = v.vital_type.replace("_", " ").title()
        date_str = v.measured_at.astimezone(IST).strftime("%d %b %Y, %I:%M %p IST") if v.measured_at else "Recent"
        val_str = ", ".join(f"{k}: {val}" for k, val in v.values.items())
        lines.append(f"• {v_name}: {val_str} (recorded on {date_str})")
        if v.flags:
            for f in v.flags:
                lines.append(f"  - {f.message}")
    return "\n".join(lines)


async def _tool_get_vital_trend(uid: str, args: dict, db: firestore.AsyncClient) -> str:
    from patient_service.vitals.vitals_func import get_vital_trend
    vtype = args.get("vital_type", "blood_pressure")
    days = int(args.get("days", 30))
    trend = await get_vital_trend(uid, vtype, days, db)
    if not trend.points:
        return f"No readings found for {vtype.replace('_', ' ')} in the last {days} days."

    lines = [f"Trend for {vtype.replace('_', ' ').title()} (Past {days} days):\n"]
    for p in trend.points:
        dt_str = p.measured_at.astimezone(IST).strftime("%d %b %Y %I:%M %p") if p.measured_at else ""
        vals = ", ".join(f"{k}: {v}" for k, v in p.values.items())
        lines.append(f"• {dt_str}: {vals} {trend.unit}")
    return "\n".join(lines)


async def _tool_list_documents(uid: str, db: firestore.AsyncClient) -> str:
    from patient_service.documents.documents_func import get_patient_documents
    docs = await get_patient_documents(uid, db)
    if not docs:
        return "No documents uploaded yet."
    lines = ["Here are your uploaded medical documents:\n"]
    for d in docs:
        name  = d.title or (d.file_path.split("/")[-1] if d.file_path else "Unnamed")
        date  = d.created_at.strftime("%d %b %Y") if d.created_at else "Unknown date"
        dtype = d.type.value if hasattr(d.type, "value") else str(d.type)
        stat  = d.status.value if hasattr(d.status, "value") else str(d.status)
        lines.append(f"• {name} — {dtype} | {stat} | uploaded {date} | ID: {d.id}")
    return "\n".join(lines)


async def _tool_list_reminders(uid: str, args: dict, db: firestore.AsyncClient) -> str:
    from patient_service.reminders.reminders_func import get_reminders
    from patient_service.reminders.reminders_model import ReminderStatus
    status_str = args.get("status")
    status_val = None
    if status_str:
        try:
            status_val = ReminderStatus(status_str)
        except ValueError:
            pass
    reminders = await get_reminders(uid, db, status=status_val)
    if not reminders:
        return "No reminders found."
    lines = ["Here are your reminders:\n"]
    for r in reminders:
        next_fire = (
            r.next_trigger_at.astimezone(IST).strftime("%d %b %Y %I:%M %p IST")
            if r.next_trigger_at else "N/A"
        )
        lines.append(
            f"• [{r.id}] {r.title} — {r.type.value} | {r.status.value} | next: {next_fire}"
        )
    return "\n".join(lines)


async def _tool_create_reminder(uid: str, args: dict, db: firestore.AsyncClient) -> str:
    from patient_service.reminders.reminders_func import create_reminder
    from patient_service.reminders.reminders_model import (
        MealTiming,
        ReminderCreateRequest,
        ReminderSchedule,
        ReminderType,
        RecurrenceType,
        MedicineReminderDetails,
        FollowUpReminderDetails,
    )

    rtype_str = (args.get("type") or "medicine").lower()
    rtype     = ReminderType.follow_up if "follow" in rtype_str else ReminderType.medicine

    recurrence_str = args.get("recurrence", "daily").lower()
    if rtype == ReminderType.follow_up and not args.get("recurrence"):
        recurrence_str = "once"
    try:
        recurrence = RecurrenceType(recurrence_str)
    except ValueError:
        recurrence = RecurrenceType.daily

    start_date_str = args.get("start_date", datetime.date.today().isoformat())
    try:
        start_date = datetime.date.fromisoformat(start_date_str)
    except ValueError:
        start_date = datetime.date.today()

    end_date = None
    if args.get("end_date"):
        try:
            end_date = datetime.date.fromisoformat(args["end_date"])
        except ValueError:
            pass

    # Resolve timing: prefer meal_timing over time_of_day
    meal_timing = None
    meal_timing_str = args.get("meal_timing")
    if meal_timing_str:
        try:
            meal_timing = MealTiming(meal_timing_str)
        except ValueError:
            pass

    time_of_day = args.get("time_of_day") or ("09:00" if not meal_timing else None)

    schedule = ReminderSchedule(
        recurrence=recurrence,
        start_date=start_date,
        end_date=end_date,
        time_of_day=time_of_day,
        meal_timing=meal_timing,
    )

    med_details = None
    if rtype == ReminderType.medicine and args.get("medicine_name"):
        med_details = MedicineReminderDetails(
            name=args["medicine_name"],
            dosage=args.get("dosage"),
        )

    fup_details = None
    if rtype == ReminderType.follow_up:
        appt_date = None
        if args.get("appointment_date"):
            try:
                appt_date = datetime.date.fromisoformat(args["appointment_date"])
            except ValueError:
                pass
        fup_details = FollowUpReminderDetails(
            specialty=args.get("specialty"),
            reason=args.get("notes"),
            appointment_date=appt_date,
            appointment_time=args.get("appointment_time") or args.get("time_of_day"),
        )

    if meal_timing:
        timing_label = meal_timing.value.replace("_", " ")
    else:
        timing_label = f"at {time_of_day or '09:00'}"
    title = args.get("title") or f"{args.get('medicine_name', 'Daily')} Reminder {timing_label}"

    req = ReminderCreateRequest(
        type=rtype,
        title=title,
        notes=args.get("notes"),
        schedule=schedule,
        medicine_details=med_details,
        follow_up_details=fup_details,
    )

    reminder = await create_reminder(uid, req, db)
    next_fire = (
        reminder.next_trigger_at.astimezone(IST).strftime("%d %b %Y at %I:%M %p IST")
        if reminder.next_trigger_at else "soon"
    )
    timing_display = (
        meal_timing.value.replace("_", " ") if meal_timing
        else f"at {schedule.time_of_day} IST"
    )
    return (
        f"Reminder created successfully!\n"
        f"• Title: {reminder.title}\n"
        f"• Type: {reminder.type.value}\n"
        f"• Schedule: {schedule.recurrence.value} {timing_display}\n"
        f"• First reminder: {next_fire}\n"
        f"• ID: {reminder.id}"
    )


async def _tool_delete_reminder(uid: str, args: dict, db: firestore.AsyncClient) -> str:
    from patient_service.reminders.reminders_func import delete_reminder
    reminder_id = args.get("reminder_id", "")
    if not reminder_id:
        return "No reminder ID provided. Please ask the patient to specify which reminder to delete."
    await delete_reminder(uid, reminder_id, db)
    return f"Reminder '{reminder_id}' has been cancelled successfully."


async def _tool_update_reminder(uid: str, args: dict, db: firestore.AsyncClient) -> str:
    from patient_service.reminders.reminders_func import update_reminder
    from patient_service.reminders.reminders_model import ReminderUpdateRequest, ReminderStatus
    reminder_id = args.get("reminder_id", "")
    if not reminder_id:
        return "No reminder ID provided."

    status_val = None
    if args.get("status"):
        try:
            status_val = ReminderStatus(args["status"])
        except ValueError:
            pass

    req = ReminderUpdateRequest(
        title=args.get("title"),
        notes=args.get("notes"),
        status=status_val,
    )
    reminder = await update_reminder(uid, reminder_id, req, db)
    return f"Reminder '{reminder.title}' updated. Status: {reminder.status.value}."


async def _tool_list_consultations(uid: str, db: firestore.AsyncClient) -> str:
    from patient_service.consultations.consultations_func import get_audio_consultations
    consultations = await get_audio_consultations(uid, db)
    if not consultations:
        return "No consultation recordings found."
    lines = ["Here are your consultation recordings:\n"]
    for c in consultations:
        date = c.created_at.astimezone(IST).strftime("%d %b %Y") if c.created_at else "Unknown date"
        title = c.title or "Untitled"
        status = c.status.value if hasattr(c.status, "value") else str(c.status)
        doctor = f" | Dr: {c.doctor_name}" if c.doctor_name else ""
        lines.append(f"• [{c.id}] {title} — {status} | {date}{doctor}")
        if c.key_diagnoses:
            lines.append(f"  Diagnoses: {', '.join(c.key_diagnoses)}")
        if c.icd_codes:
            icd_strs = [f"{i.code} ({i.description})" for i in c.icd_codes]
            lines.append(f"  ICD codes: {', '.join(icd_strs)}")
        if c.summary:
            summary_preview = c.summary[:120].rstrip()
            if len(c.summary) > 120:
                summary_preview += "..."
            lines.append(f"  Summary: {summary_preview}")
    return "\n".join(lines)


async def _tool_get_consultation(uid: str, args: dict, db: firestore.AsyncClient) -> str:
    from patient_service.consultations.consultations_func import get_audio_consultation
    consultation_id = args.get("consultation_id", "")
    if not consultation_id:
        return "No consultation ID provided. Please call list_consultations first."
    try:
        c = await get_audio_consultation(uid, consultation_id, db)
    except (ValueError, PermissionError) as e:
        return str(e)

    date = c.created_at.astimezone(IST).strftime("%d %b %Y") if c.created_at else "Unknown date"
    lines = [
        f"Consultation: {c.title or 'Untitled'} ({date})",
        f"Status: {c.status.value} | Language: {c.language.value if hasattr(c.language, "value") else c.language}",
    ]
    if c.doctor_name:
        lines.append(f"Doctor: {c.doctor_name}")
    if c.summary:
        lines.append(f"\nSummary:\n{c.summary}")
    if c.key_diagnoses:
        lines.append(f"\nDiagnoses: {', '.join(c.key_diagnoses)}")
    if c.icd_codes:
        icd_strs = [f"{i.code} — {i.description}" for i in c.icd_codes]
        lines.append(f"ICD codes: {'; '.join(icd_strs)}")
    if c.medicines:
        lines.append("\nMedicines prescribed:")
        for m in c.medicines:
            parts = [m.name]
            if m.dosage:      parts.append(m.dosage)
            if m.frequency:   parts.append(m.frequency)
            if m.instructions: parts.append(m.instructions)
            if m.duration:    parts.append(f"for {m.duration}")
            lines.append(f"  • {' | '.join(parts)}")
    if c.reminder_suggestions:
        lines.append(f"\nReminder suggestions: {len(c.reminder_suggestions)} created")
    return "\n".join(lines)


# ══════════════════════════════════════════════════════════════
#  Gemini function-calling entry point
# ══════════════════════════════════════════════════════════════

async def try_tool_call(
    prompt: str,
    history_str: str,
    today: str,
) -> Optional[tuple]:
    """
    Sends the prompt and conversation history to Gemini with tool calling enabled.

    Returns:
    - ("tool", tool_name, args_dict) — Gemini selected a tool to execute
    - ("text", clarifying_question)  — Gemini needs clarification from the patient
    - None                           — General question / RAG path
    """
    from common_code.gcp_clients import _get_genai

    system_instruction = (
        f"You are an AI Health Companion assistant. Today is {today} (IST, Asia/Kolkata).\n\n"
        "Your role is to understand user health queries, execute tools when asked to perform actions, "
        "or ask clarifying questions if details are missing.\n\n"
        "Respond in exactly ONE of three ways:\n\n"
        "━━ WAY 1: CALL A TOOL ━━\n"
        "When the patient requests an action and you have sufficient parameters, call the tool:\n"
        "• LOG VITALS (weight, BP, heart rate, blood sugar, SpO2, temperature, height, respiratory rate) → log_vitals\n"
        "  - e.g. 'Add weight 50kg' → log_vitals(weight=50)\n"
        "  - e.g. 'Record BP 125/82' → log_vitals(systolic=125, diastolic=82)\n"
        "  - e.g. 'My fasting sugar is 95' → log_vitals(glucose_fasting=95)\n"
        "  - e.g. 'My temperature is 99 F' → log_vitals(temperature=99.0)\n"
        "• GET LATEST VITALS → get_latest_vitals\n"
        "• GET VITAL TREND → get_vital_trend(vital_type=..., days=...)\n"
        "• LIST DOCUMENTS → list_documents\n"
        "• LIST REMINDERS → list_reminders\n"
        "• CREATE REMINDER → create_reminder (requires title, time/timing, recurrence, start_date)\n"
        "• DELETE / CANCEL REMINDER → delete_reminder\n"
        "• UPDATE / PAUSE REMINDER → update_reminder\n"
        "• LIST CONSULTATIONS → list_consultations\n"
        "• GET CONSULTATION DETAILS → get_consultation\n\n"
        "━━ WAY 2: ASK ONE CLARIFYING QUESTION ━━\n"
        "If the patient wants to log a vital or create a reminder but a critical value is missing:\n"
        "  - Said 'Log my blood pressure' but no numbers? → Ask: 'What was your blood pressure reading? (e.g. 120/80 mmHg)'\n"
        "  - Said 'Log my blood sugar' but no value? → Ask: 'What was your blood sugar reading in mg/dL, and was it fasting or after a meal?'\n"
        "  - Said 'Record my weight' but no number? → Ask: 'What is your current body weight in kg?'\n"
        "  - Said 'Set a medicine reminder' but no medicine or time? → Ask: 'What medicine would you like a reminder for, and at what time or meal?'\n"
        "Ask only ONE concise, friendly question per turn.\n\n"
        "━━ WAY 3: RESPOND WITH EXACTLY THE WORD 'PASS' ━━\n"
        "For medical explanations, health questions, symptoms, or greetings, respond with ONLY the word: PASS\n"
        "NEVER provide medical advice here. If it is a health question, output PASS so RAG grounds it."
    )

    user_turn = prompt
    if history_str:
        user_turn = f"Conversation so far:\n{history_str}\n\nPatient: {prompt}"

    def _call():
        client = _get_genai()
        return client.models.generate_content(
            model=settings.GEMINI_MODEL,
            contents=user_turn,
            config=genai_types.GenerateContentConfig(
                system_instruction=system_instruction,
                tools=[CHATBOT_TOOLS],
                tool_config=_TOOL_CONFIG,
                automatic_function_calling=genai_types.AutomaticFunctionCallingConfig(disable=True),
            ),
        )

    try:
        response = await asyncio.to_thread(_call)
        if not response.candidates:
            return None
        parts = response.candidates[0].content.parts

        for part in parts:
            if hasattr(part, "function_call") and part.function_call:
                fc = part.function_call
                logger.info(f"Tool selected: {fc.name} args={dict(fc.args)}")
                return "tool", fc.name, dict(fc.args)

        text_parts = [p.text for p in parts if hasattr(p, "text") and p.text]
        text = " ".join(text_parts).strip()
        if not text or text.strip().upper() == "PASS":
            return None

        logger.info(f"Tool call: Gemini returned clarifying question: {text[:80]}")
        return "text", text

    except Exception as e:
        logger.warning(f"Tool call attempt failed: {e}", exc_info=True)

    return None
