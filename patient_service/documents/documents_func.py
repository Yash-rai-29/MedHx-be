import datetime
import uuid
import json
import logging
from typing import Optional
from google.cloud import firestore
from common_code.config import settings
from common_code.gcp_clients import (
    upload_bytes_to_gcs,
    async_upload_bytes_to_gcs,
    parse_medical_document,
    generate_gemini_content,
    async_generate_gemini_content,
    generate_embeddings,
    async_generate_embeddings,
    translate_text,
    synthesize_speech
)
from patient_service.documents.documents_model import (
    DocumentResponse,
    DocumentStatus,
    DocumentType,
    TranslateSummaryResponse
)

logger = logging.getLogger(__name__)


async def create_pending_document(
    uid: str,
    file_path: str,
    db: firestore.AsyncClient,
    title: Optional[str] = None,
    description: Optional[str] = None
) -> DocumentResponse:
    """Creates a document record in Firestore with 'in_progress' status."""
    doc_id = str(uuid.uuid4())
    created_at = datetime.datetime.now(datetime.UTC)
    
    doc_record = {
        "id": doc_id,
        "patientId": uid,
        "fileRef": file_path,
        "status": DocumentStatus.in_progress.value,
        "type": DocumentType.other.value,
        "raw_text": "",
        "summary": "Processing report... Please check back in a few moments.",
        "createdAt": created_at,
        "title": title,
        "description": description,
        "doctor_name": None,
        "document_date": None,
        "medications": [],
        "abnormal_labs": [],
        "red_flags": [],
        "actionable_steps": []
    }
    
    await db.collection(settings.DOCUMENTS_COLLECTION).document(doc_id).set(doc_record)
    
    return DocumentResponse(
        id=doc_id,
        file_path=file_path,
        status=DocumentStatus.in_progress,
        type=DocumentType.other,
        raw_text="",
        summary="Processing report... Please check back in a few moments.",
        created_at=created_at,
        title=title,
        description=description,
        doctor_name=None,
        document_date=None,
        medications=[],
        abnormal_labs=[],
        red_flags=[],
        actionable_steps=[]
    )

async def upload_and_process_document(
    uid: str,
    filename: str,
    file_bytes: bytes,
    mime_type: str,
    db: firestore.AsyncClient,
    title: Optional[str] = None,
    description: Optional[str] = None
) -> DocumentResponse:
    """Uploads raw document bytes to GCS bucket (non-blocking) and creates a pending document record."""
    clean_filename = "".join(c for c in filename if c.isalnum() or c in "._-")
    timestamp = int(datetime.datetime.now(datetime.UTC).timestamp())
    blob_name = f"patients/{uid}/reports/{timestamp}_{clean_filename}"

    # Non-blocking upload to GCS
    await async_upload_bytes_to_gcs(blob_name, file_bytes, content_type=mime_type)

    return await create_pending_document(uid, blob_name, db, title, description)


async def background_parse_and_index_document(
    doc_id: str,
    uid: str,
    file_path: str,
    mime_type: str,
    db: firestore.AsyncClient,
    title: Optional[str] = None,
    description: Optional[str] = None
) -> None:
    """
    Background task: OCR via Document AI → structured extraction via Gemini →
    embedding → Firestore update → push notification.

    IMPORTANT: FastAPI BackgroundTasks re-use the same async event loop but the
    `db` dependency is still valid for the lifetime of the background task since
    Cloud Firestore AsyncClient manages its own gRPC channel lifecycle.
    All blocking IO (Gemini, embeddings) is offloaded via asyncio.to_thread.
    """
    from common_code.notification_dispatcher import dispatch_notification
    from patient_service.notifications.notifications_model import NotificationType

    doc_ref = db.collection(settings.DOCUMENTS_COLLECTION).document(doc_id)

    try:
        gcs_uri = f"gs://{settings.STORAGE_BUCKET_NAME}/{file_path}"
        logger.info(f"[doc:{doc_id}] Starting background parse for uid={uid}, path={file_path}")

        # 1. OCR via Document AI (async wrapper)
        raw_text = await parse_medical_document(gcs_uri, mime_type)
        logger.info(f"[doc:{doc_id}] OCR completed, text length={len(raw_text)}")

        # Incorporate user-provided context if present
        context_str = ""
        if title:
            context_str += f"User-provided document title: {title}\n"
        if description:
            context_str += f"User-provided document description or notes: {description}\n"

        # 2. Structured extraction via Gemini (non-blocking thread offload)
        prompt = (
            "You are an empathetic, clear clinical assistant.\n"
            "Analyze the provided medical document text (and any user-provided metadata) and perform the following actions:\n"
            "1. Categorize the document as one of the following exact types: ['prescription', 'lab_report', 'discharge_summary', 'imaging_report', 'other'].\n"
            "2. Explain this medical document in plain, layman English for the patient. Avoid heavy medical jargon. "
            "Explain what the tests or notes mean, whether results are normal/high/low, and suggest general care steps. "
            "Emphasize that they should consult their doctor.\n"
            "3. Extract structured clinical data:\n"
            "   - doctor_name: Doctor or hospital/clinic name.\n"
            "   - document_date: Inferred consultation or test date in YYYY-MM-DD format.\n"
            "   - medications: List of medications found, each with keys: 'name', 'dosage', 'frequency', 'instructions'.\n"
            "   - abnormal_labs: List of laboratory values or metrics that are outside the normal reference ranges, each with keys: 'parameter_name', 'value', 'reference_range', 'status' (High/Low/Critical).\n"
            "   - red_flags: List of warning symptoms mentioned in the document that mean the patient should seek immediate emergency care.\n"
            "   - actionable_steps: List of next steps, lifestyle changes, dietary restrictions, or follow-up timelines.\n\n"
            "You MUST return a JSON object with the following exact keys:\n"
            "{\n"
            "  \"category\": \"<category_name>\",\n"
            "  \"summary\": \"<empathetic clinical summary>\",\n"
            "  \"doctor_name\": \"<doctor_name or null>\",\n"
            "  \"document_date\": \"<YYYY-MM-DD or null>\",\n"
            "  \"medications\": [{\"name\": \"...\", \"dosage\": \"...\", \"frequency\": \"...\", \"instructions\": \"...\"}],\n"
            "  \"abnormal_labs\": [{\"parameter_name\": \"...\", \"value\": \"...\", \"reference_range\": \"...\", \"status\": \"...\"}],\n"
            "  \"red_flags\": [\"...\"],\n"
            "  \"actionable_steps\": [\"...\"]\n"
            "}\n\n"
            f"{context_str}\n"
            f"Document Text:\n{raw_text}"
        )

        gemini_output = await async_generate_gemini_content(prompt, json_response=True)
        logger.info(f"[doc:{doc_id}] Gemini extraction completed")

        doc_type = DocumentType.other
        summary = ""
        doctor_name = None
        document_date = None
        medications: list = []
        abnormal_labs: list = []
        red_flags: list = []
        actionable_steps: list = []

        try:
            parsed = json.loads(gemini_output)
            raw_category = parsed.get("category", "other").strip().lower()
            try:
                doc_type = DocumentType(raw_category)
            except ValueError:
                logger.warning(f"[doc:{doc_id}] Unknown Gemini category '{raw_category}', falling back to 'other'")
                doc_type = DocumentType.other
            summary       = parsed.get("summary", "")
            doctor_name   = parsed.get("doctor_name")
            document_date = parsed.get("document_date")
            medications   = parsed.get("medications", [])
            abnormal_labs = parsed.get("abnormal_labs", [])
            red_flags     = parsed.get("red_flags", [])
            actionable_steps = parsed.get("actionable_steps", [])
        except (json.JSONDecodeError, Exception) as je:
            logger.warning(f"[doc:{doc_id}] Failed to parse Gemini JSON: {je}. Raw output: {gemini_output[:200]}")
            summary = gemini_output
            doc_type = (
                DocumentType.lab_report
                if ("lab" in raw_text.lower() or "report" in raw_text.lower())
                else DocumentType.prescription
            )

        # 3. Generate embedding vector (non-blocking thread offload)
        embedding_text = f"Summary: {summary}\nReport details: {raw_text[:500]}"
        vector = await async_generate_embeddings(embedding_text)
        logger.info(f"[doc:{doc_id}] Embedding generated, dim={len(vector)}")

        # 4. Persist enriched data to Firestore
        await doc_ref.update({
            "status":           DocumentStatus.completed.value,
            "type":             doc_type.value,
            "raw_text":         raw_text,
            "summary":          summary,
            "embedding":        vector,
            "doctor_name":      doctor_name,
            "document_date":    document_date,
            "medications":      medications,
            "abnormal_labs":    abnormal_labs,
            "red_flags":        red_flags,
            "actionable_steps": actionable_steps,
            "processedAt":      datetime.datetime.now(datetime.UTC),
        })
        logger.info(f"[doc:{doc_id}] Firestore updated with status=completed")

        # 5. Push notification to patient
        await dispatch_notification(
            patient_id=uid,
            title=None,
            body=None,
            notification_type=NotificationType.report.value,
            extra_data={"document_id": doc_id}
        )
        logger.info(f"[doc:{doc_id}] Report-ready push notification dispatched")

    except Exception as e:
        logger.error(f"[doc:{doc_id}] Background parsing FAILED: {type(e).__name__}: {e}", exc_info=True)
        try:
            await doc_ref.update({
                "status":    DocumentStatus.failed.value,
                "summary":   f"Processing failed: {str(e)}",
                "failedAt":  datetime.datetime.now(datetime.UTC),
            })
        except Exception as update_err:
            logger.error(f"[doc:{doc_id}] Could not update failure status: {update_err}")

async def get_patient_documents(uid: str, db: firestore.AsyncClient) -> list[DocumentResponse]:
    """Retrieves patient medical history documents."""
    docs = await db.collection(settings.DOCUMENTS_COLLECTION) \
        .where("patientId", "==", uid) \
        .order_by("createdAt", direction=firestore.Query.DESCENDING) \
        .get()
        
    results = []
    for doc in docs:
        d = doc.to_dict()
        results.append(DocumentResponse(
            id=doc.id,
            file_path=d.get("fileRef", ""),
            status=d.get("status", "completed"),
            type=d.get("type", "other"),
            raw_text=d.get("raw_text", ""),
            summary=d.get("summary", ""),
            translated_summary=d.get("translated_summary"),
            created_at=d.get("createdAt"),
            title=d.get("title"),
            description=d.get("description"),
            doctor_name=d.get("doctor_name"),
            document_date=d.get("document_date"),
            medications=d.get("medications", []),
            abnormal_labs=d.get("abnormal_labs", []),
            red_flags=d.get("red_flags", []),
            actionable_steps=d.get("actionable_steps", [])
        ))
    return results

async def translate_document_summary(
    uid: str,
    doc_id: str,
    target_language: str,
    db: firestore.AsyncClient
) -> TranslateSummaryResponse:
    """Translates the summary of an existing document to the patient's language."""
    doc_ref = db.collection(settings.DOCUMENTS_COLLECTION).document(doc_id)
    doc_snap = await doc_ref.get()
    
    if not doc_snap.exists:
        raise ValueError("Document not found.")
        
    doc_data = doc_snap.to_dict()
    if doc_data.get("patientId") != uid:
        raise PermissionError("Access to this document is unauthorized.")
        
    translations = doc_data.get("translations", {})
    if target_language in translations:
        return TranslateSummaryResponse(
            translated_summary=translations[target_language],
            language=target_language
        )
        
    summary = doc_data.get("summary", "")
    translated = translate_text(summary, target_language)
    
    translations[target_language] = translated
    await doc_ref.update({
        "translations": translations,
        "translated_summary": translated
    })
    
    return TranslateSummaryResponse(
        translated_summary=translated,
        language=target_language
    )

async def synthesize_summary_speech(
    uid: str,
    doc_id: str,
    lang: str,
    db: firestore.AsyncClient
) -> bytes:
    """Generates audio reading of the document summary in the chosen language."""
    doc_snap = await db.collection(settings.DOCUMENTS_COLLECTION).document(doc_id).get()
    if not doc_snap.exists:
        raise ValueError("Document not found.")
        
    doc_data = doc_snap.to_dict()
    if doc_data.get("patientId") != uid:
        raise PermissionError("Access is unauthorized.")
        
    text_to_speak = doc_data.get("summary", "")
    voice_locale = "en-IN"
    
    if lang != "en":
        translations = doc_data.get("translations", {})
        if lang in translations:
            text_to_speak = translations[lang]
        else:
            text_to_speak = translate_text(text_to_speak, lang)
            translations[lang] = text_to_speak
            await db.collection(settings.DOCUMENTS_COLLECTION).document(doc_id).update({
                "translations": translations,
                "translated_summary": text_to_speak
            })
            
        voice_locales = {
            "hi": "hi-IN",
            "ta": "ta-IN",
            "te": "te-IN"
        }
        voice_locale = voice_locales.get(lang, "en-IN")
        
    audio_content = synthesize_speech(text_to_speak, voice_locale)
    return audio_content

