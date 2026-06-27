"""
Gemini function-calling tools for the chatbot.

Gemini decides autonomously (mode=AUTO) when a user prompt requires
a tool call vs. a plain RAG answer. Tools execute real backend logic
and return a text result that is fed back to Gemini for a natural
language response.
"""

import asyncio
import datetime
import logging
from typing import Optional
from zoneinfo import ZoneInfo

from google import genai
from google.genai import types as genai_types
from google.cloud import firestore

from common_code.config import settings

IST = ZoneInfo("Asia/Kolkata")

logger = logging.getLogger(__name__)


# ══════════════════════════════════════════════════════════════
#  Tool declarations (sent to Gemini)
# ══════════════════════════════════════════════════════════════

CHATBOT_TOOLS = genai_types.Tool(
    function_declarations=[

        genai_types.FunctionDeclaration(
            name="list_documents",
            description=(
                "List all medical documents, reports, and lab results that the patient "
                "has uploaded. Use this when the patient asks to see, show, or list their "
                "documents, files, reports, or records."
            ),
            parameters=genai_types.Schema(type="OBJECT", properties={}),
        ),

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
                    "time_of_day": genai_types.Schema(
                        type="STRING",
                        description="Time in HH:MM 24-hour format, e.g. '09:00'",
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
        if tool_name == "list_documents":
            return await _tool_list_documents(uid, db)
        elif tool_name == "list_reminders":
            return await _tool_list_reminders(uid, args, db)
        elif tool_name == "create_reminder":
            return await _tool_create_reminder(uid, args, db)
        elif tool_name == "delete_reminder":
            return await _tool_delete_reminder(uid, args, db)
        elif tool_name == "update_reminder":
            return await _tool_update_reminder(uid, args, db)
        else:
            return f"Unknown tool: {tool_name}"
    except Exception as e:
        logger.error(f"Tool execution error [{tool_name}]: {e}")
        return f"Error executing {tool_name}: {str(e)}"


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
        ReminderCreateRequest,
        ReminderSchedule,
        ReminderType,
        RecurrenceType,
        MedicineReminderDetails,
    )

    rtype_str = (args.get("type") or "medicine").lower()
    rtype     = ReminderType.follow_up if "follow" in rtype_str else ReminderType.medicine

    recurrence_str = args.get("recurrence", "daily").lower()
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

    schedule = ReminderSchedule(
        recurrence=recurrence,
        start_date=start_date,
        end_date=end_date,
        time_of_day=args.get("time_of_day", "09:00"),
    )

    med_details = None
    if rtype == ReminderType.medicine and args.get("medicine_name"):
        med_details = MedicineReminderDetails(
            name=args["medicine_name"],
            dosage=args.get("dosage"),
        )

    # Derive a sensible default title if Gemini didn't extract one
    title = args.get("title") or (
        f"{args.get('medicine_name', 'Daily')} Reminder at {args.get('time_of_day', '09:00')}"
    )

    req = ReminderCreateRequest(
        type=rtype,
        title=title,
        notes=args.get("notes"),
        schedule=schedule,
        medicine_details=med_details,
    )

    reminder = await create_reminder(uid, req, db)
    next_fire = (
        reminder.next_trigger_at.astimezone(IST).strftime("%d %b %Y at %I:%M %p IST")
        if reminder.next_trigger_at else "soon"
    )
    return (
        f"Reminder created successfully!\n"
        f"• Title: {reminder.title}\n"
        f"• Type: {reminder.type.value}\n"
        f"• Schedule: {schedule.recurrence.value} at {schedule.time_of_day} IST\n"
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


# ══════════════════════════════════════════════════════════════
#  Gemini function-calling entry point
# ══════════════════════════════════════════════════════════════

async def try_tool_call(
    prompt: str,
    history_str: str,
    today: str,
) -> Optional[tuple]:
    """
    Sends the prompt to Gemini with tools enabled.

    Returns one of three values:
    - ("tool", tool_name, args_dict) — Gemini selected a tool to execute
    - ("text", message)              — Gemini is asking a clarifying question
                                       (e.g. "What medicine is this for?")
    - None                           — not a tool action; let RAG handle it

    The PASS sentinel in the system instruction lets Gemini explicitly signal
    "this is a medical/general question" without generating a full text reply,
    so we can cleanly fall through to the RAG path.
    """
    from common_code.gcp_clients import _get_genai

    system_instruction = (
        f"You are an AI Health Companion assistant. Today is {today} (IST, Asia/Kolkata).\n\n"
        "Your ONLY job here is to handle tool actions and gather details for them.\n"
        "Respond in exactly ONE of three ways:\n\n"
        "━━ WAY 1: CALL A TOOL ━━\n"
        "When you have all required details, call the appropriate tool:\n"
        "• Patient wants to list/show documents → list_documents\n"
        "• Patient wants to list/show reminders → list_reminders (pass status filter if mentioned)\n"
        "• Patient wants to create a reminder AND you have: title + time + recurrence → create_reminder\n"
        "  - Use today's date as start_date unless patient specifies one.\n"
        "  - type: 'medicine' for medication reminders, 'follow_up' for doctor/appointment reminders.\n"
        "• Patient wants to delete/cancel a reminder → delete_reminder (call list_reminders first if ID unknown)\n"
        "• Patient wants to pause/resume/modify a reminder → update_reminder\n\n"
        "━━ WAY 2: ASK ONE CLARIFYING QUESTION ━━\n"
        "If the patient wants to create a reminder but a required detail is missing, ask exactly ONE question:\n"
        "  Missing title/purpose? → Ask: 'What is this reminder for? (e.g. medicine name or appointment)'\n"
        "  Medicine type but no medicine name? → Ask: 'What is the medicine name and dosage?'\n"
        "  No time specified? → Ask: 'What time would you like the reminder? (e.g. 9 AM)'\n"
        "  No recurrence? → Ask: 'Should this repeat daily, weekly, or just once?'\n"
        "  Missing end date for medicine course? → Ask: 'How long should this reminder continue? Or should it repeat indefinitely?'\n"
        "Ask only ONE question per turn. Be brief and friendly.\n\n"
        "━━ WAY 3: RESPOND WITH EXACTLY THE WORD 'PASS' ━━\n"
        "For everything else — medical questions, health advice, greetings, report explanations — "
        "respond with only the single word: PASS\n\n"
        "NEVER provide medical advice here. NEVER respond with more than one clarifying question."
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
            ),
        )

    try:
        response = await asyncio.to_thread(_call)
        if not response.candidates:
            return None
        parts = response.candidates[0].content.parts

        # Check for a function call first
        for part in parts:
            if hasattr(part, "function_call") and part.function_call:
                fc = part.function_call
                logger.info(f"Tool selected: {fc.name} args={dict(fc.args)}")
                return "tool", fc.name, dict(fc.args)

        # No function call — check if Gemini returned a clarifying question or PASS
        text_parts = [p.text for p in parts if hasattr(p, "text") and p.text]
        text = " ".join(text_parts).strip()
        if not text or text.strip().upper() == "PASS":
            return None

        logger.info(f"Tool call: Gemini returned clarifying question: {text[:80]}")
        return "text", text

    except Exception as e:
        logger.warning(f"Tool call attempt failed: {e}", exc_info=True)

    return None
