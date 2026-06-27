import logging
import datetime
import json
import os
from common_code.firestore import get_db, send_push_notification, log_audit_event
from common_code.config import settings

logger = logging.getLogger(__name__)

# Cache path for templates
TEMPLATES_PATH = os.path.join(os.path.dirname(__file__), "notification_templates.json")

def load_notification_templates() -> dict:
    """Loads notification templates from configuration file."""
    try:
        with open(TEMPLATES_PATH, "r") as f:
            return json.load(f)
    except Exception as e:
        logger.error(f"Failed to load notification templates: {e}")
        return {}

def format_safe(template: str, context: dict) -> str:
    """Safely formats a string template with key/values from context, ignoring missing keys."""
    if not template:
        return ""
    class SafeDict(dict):
        def __missing__(self, key):
            return f"{{{key}}}"
    try:
        return template.format_map(SafeDict(context))
    except Exception as e:
        logger.warning(f"Error formatting template '{template}': {e}")
        return template

async def dispatch_notification(
    patient_id: str,
    title: str | None,
    body: str | None,
    notification_type: str,
    extra_data: dict | None = None,
    deeplink: str | None = None
) -> bool:
    """
    Decoupled helper that records an in-app notification in Firestore, fetches
    user preferences, and dispatches an FCM push notification (updating the document on result).
    Dynamically loads titles, bodies, and deep links from templates configuration.
    """
    db = get_db()
    
    # 1. Fetch user profile from Firestore to resolve the active FCM token
    user_doc = await db.collection(settings.USERS_COLLECTION).document(patient_id).get()
    if not user_doc.exists:
        logger.warning(f"Notification dispatcher failed: User profile '{patient_id}' not found.")
        return False
        
    user_data = user_doc.to_dict()

    # Resolve FCM token: prefer platform-keyed map, fall back to legacy flat key
    # fcm_tokens map: { "ios": "...", "android": "...", "web": "..." }
    fcm_tokens_map: dict = user_data.get("fcm_tokens") or {}
    # Pick the first available token across all platforms (most-recently registered wins)
    platform_order = ["android", "ios", "web"]
    fcm_token = None
    for platform in platform_order:
        if fcm_tokens_map.get(platform):
            fcm_token = fcm_tokens_map[platform]
            break
    # Final fallback: legacy single flat key written by older clients
    if not fcm_token:
        fcm_token = user_data.get("fcm_token")

    
    # 2. Check patient clinical notification preferences
    notifications_disabled = False
    patient_doc = await db.collection(settings.PATIENTS_COLLECTION).document(patient_id).get()
    if patient_doc.exists:
        patient_data = patient_doc.to_dict()
        notifications_disabled = patient_data.get("notifications_disabled", False)

    # 3. Determine initial push status
    push_status = "pending"
    if not fcm_token:
        push_status = "skipped_no_token"
    elif notifications_disabled:
        push_status = "skipped_disabled"

    # 4. Resolve templates & dynamic formatting
    templates = load_notification_templates()
    type_config = templates.get(notification_type, templates.get("general", {}))
    
    patient_name = user_data.get("name", "Patient")
    first_name = patient_name.split()[0] if patient_name else "Patient"

    context = extra_data.copy() if extra_data is not None else {}
    context.setdefault("patient_name", patient_name)
    context.setdefault("patient_first_name", first_name)

    # Enrich context dynamically based on type and database records
    if notification_type == "medicine" and "reminder_id" in context:
        reminder_id = context["reminder_id"]
        try:
            rem_doc = await db.collection(settings.REMINDERS_COLLECTION).document(reminder_id).get()
            if rem_doc.exists:
                rem_data = rem_doc.to_dict()
                title_val = rem_data.get("title", "")
                med_name = title_val
                if title_val.lower().startswith("take "):
                    med_name = title_val[5:]
                
                # Human friendly meal relation
                meal_relation = rem_data.get("mealRelativeTiming", "NONE")
                meal_friendly = "at any time"
                if meal_relation == "BEFORE_FOOD":
                    meal_friendly = "before food"
                elif meal_relation == "AFTER_FOOD":
                    meal_friendly = "after food"
                
                context.setdefault("medicine_name", med_name)
                context.setdefault("reminder_title", title_val)
                context.setdefault("schedule_time", rem_data.get("schedule", ""))
                context.setdefault("meal_relation", meal_friendly)
        except Exception as e:
            logger.warning(f"Failed to enrich reminder context: {e}")

    elif notification_type == "report" and "document_id" in context:
        doc_id = context["document_id"]
        try:
            doc_snap = await db.collection(settings.DOCUMENTS_COLLECTION).document(doc_id).get()
            if doc_snap.exists:
                doc_data = doc_snap.to_dict()
                file_ref = doc_data.get("fileRef", "")
                filename = file_ref.split("/")[-1] if file_ref else "Report"
                if "_" in filename:
                    parts = filename.split("_", 1)
                    if parts[0].isdigit():
                        filename = parts[1]
                
                raw_type = doc_data.get("type", "report")
                friendly_type = raw_type.replace("_", " ").title()
                
                context.setdefault("document_name", filename)
                context.setdefault("document_type", friendly_type)
        except Exception as e:
            logger.warning(f"Failed to enrich document context: {e}")

    elif notification_type == "consultation" and "consultation_id" in context:
        cons_id = context["consultation_id"]
        try:
            cons_snap = await db.collection(settings.CONSULTATIONS_COLLECTION).document(cons_id).get()
            if cons_snap.exists:
                cons_data = cons_snap.to_dict()
                context.setdefault("doctor_name", cons_data.get("doctorName", "Doctor"))
                context.setdefault("appointment_time", cons_data.get("appointmentTime", ""))
        except Exception as e:
            logger.warning(f"Failed to enrich consultation context: {e}")
            
    # Resolve title
    resolved_title = title
    if not resolved_title or resolved_title == "Reminder Alert ⏰":
        resolved_title = type_config.get("default_title", "Notification 🔔")
    resolved_title = format_safe(resolved_title, context)
    
    # Resolve body
    resolved_body = body
    if not resolved_body:
        resolved_body = type_config.get("default_body", "")
    resolved_body = format_safe(resolved_body, context)

    # Resolve deep link
    resolved_deeplink = deeplink
    if not resolved_deeplink:
        resolved_deeplink = context.get("deeplink")
    if not resolved_deeplink:
        deeplink_template = type_config.get("deeplink_template", "/")
        resolved_deeplink = format_safe(deeplink_template, context)


    # 5. Write in-app notification document to Firestore
    notification_payload = {
        "patientId": patient_id,
        "title": resolved_title,
        "body": resolved_body,
        "deeplink": resolved_deeplink,
        "isRead": False,
        "createdAt": datetime.datetime.now(datetime.UTC),
        "type": notification_type,
        "extraData": extra_data or {},
        "pushStatus": push_status,
        "pushMessageId": None
    }
    
    try:
        _, notif_ref = await db.collection(settings.NOTIFICATIONS_COLLECTION).add(notification_payload)
        notification_id = notif_ref.id
    except Exception as e:
        logger.error(f"Failed to write notification to Firestore: {e}")
        return False

    # 6. Trigger FCM Push Notification if pending
    if push_status == "pending":
        payload = extra_data.copy() if extra_data is not None else {}
        payload.update({
            "type": notification_type,
            "patient_id": patient_id,
            "notification_id": notification_id
        })
        if resolved_deeplink:
            payload["deeplink"] = resolved_deeplink
            
        logger.info(f"Sending push notification to user {patient_id} of type {notification_type}...")
        msg_id = send_push_notification(
            token=fcm_token,
            title=resolved_title,
            body=resolved_body,
            data=payload
        )
        
        if msg_id:
            push_status = "sent"
            try:
                await notif_ref.update({
                    "pushStatus": "sent",
                    "pushMessageId": msg_id
                })
            except Exception as e:
                logger.warning(f"Failed to update push status on notification {notification_id}: {e}")
                
            await log_audit_event(
                actor="system",
                action="SEND_PUSH_NOTIFICATION",
                target=patient_id,
                details={
                    "title": resolved_title,
                    "type": notification_type,
                    "message_id": msg_id,
                    "notification_id": notification_id
                }
            )
        else:
            push_status = "failed"
            try:
                await notif_ref.update({
                    "pushStatus": "failed"
                })
            except Exception as e:
                logger.warning(f"Failed to update failed push status on notification {notification_id}: {e}")
            logger.warning(f"Notification dispatcher failed to send FCM message for user {patient_id}.")
    else:
        logger.info(f"FCM push skipped for user {patient_id} due to status: {push_status}")

    return True


