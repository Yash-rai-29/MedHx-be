import json
import logging
import uuid
from datetime import datetime, UTC
from typing import Optional

from google.cloud import firestore

from common_code.config import settings
from common_code.gcp_clients import (
    async_generate_gemini_content,
    async_upload_bytes_to_gcs,
    generate_signed_download_url,
    synthesize_speech,
    transcribe_audio,
    translate_text,
    VOICE_LOCALE_MAP,
)
from patient_service.consultations.consultations_model import (
    AudioConsultationResponse,
    AudioConsultationStatus,
    AudioConsultationUploadResponse,
    DiarizedSegment,
    ExtractedMedicine,
    FollowUpSuggestion,
    PatientConsultationDetail,
    RefineConsultationRequest,
    ReminderSuggestion,
    SuggestedReminderSchedule,
)
from patient_service.documents.documents_model import LANGUAGE_DISPLAY_NAMES, SupportedLanguage

logger = logging.getLogger(__name__)

_COLL = settings.AUDIO_CONSULTATIONS_COLLECTION  # "audio_consultations"


# ══════════════════════════════════════════════════════════════
#  Legacy — doctor-published consultations (unchanged)
# ══════════════════════════════════════════════════════════════

async def get_patient_consultations(patient_id: str, db: firestore.AsyncClient) -> list[PatientConsultationDetail]:
    docs = await (
        db.collection(settings.CONSULTATIONS_COLLECTION)
        .where("patientId", "==", patient_id)
        .where("status", "==", "published")
        .order_by("createdAt", direction=firestore.Query.DESCENDING)
        .get()
    )
    consultations = []
    for doc in docs:
        d = doc.to_dict()
        pdf_ref = d.get("pdfRef")
        consultations.append(PatientConsultationDetail(
            id=doc.id,
            doctorId=d.get("doctorId"),
            patientId=d.get("patientId"),
            status=d.get("status"),
            createdAt=d.get("createdAt"),
            summary_en=d.get("summary_en"),
            diagnoses=d.get("diagnoses", []),
            medicines=d.get("medicines", []),
            follow_up_days=d.get("follow_up_days", 0),
            pdfRef=pdf_ref,
            pdf_url=generate_signed_download_url(pdf_ref) if pdf_ref else None,
        ))
    return consultations


async def get_patient_consultation_by_id(
    consultation_id: str,
    patient_id: str,
    db: firestore.AsyncClient,
) -> PatientConsultationDetail:
    snap = await db.collection(settings.CONSULTATIONS_COLLECTION).document(consultation_id).get()
    if not snap.exists:
        raise ValueError("Consultation not found.")
    d = snap.to_dict()
    if d.get("patientId") != patient_id:
        raise PermissionError("Access is unauthorized.")
    if d.get("status") != "published":
        raise ValueError("This consultation report is not yet finalized.")
    pdf_ref = d.get("pdfRef")
    return PatientConsultationDetail(
        id=snap.id,
        doctorId=d.get("doctorId"),
        patientId=d.get("patientId"),
        status=d.get("status"),
        createdAt=d.get("createdAt"),
        summary_en=d.get("summary_en"),
        diagnoses=d.get("diagnoses", []),
        medicines=d.get("medicines", []),
        follow_up_days=d.get("follow_up_days", 0),
        pdfRef=pdf_ref,
        pdf_url=generate_signed_download_url(pdf_ref) if pdf_ref else None,
    )


async def translate_consultation_summary(
    consultation_id: str,
    patient_id: str,
    target_language: str,
    db: firestore.AsyncClient,
) -> str:
    consult = await get_patient_consultation_by_id(consultation_id, patient_id, db)
    if not consult.summary_en:
        raise ValueError("No summary content available to translate.")
    return translate_text(consult.summary_en, target_language)


async def listen_consultation_summary(
    consultation_id: str,
    patient_id: str,
    target_language: str,
    db: firestore.AsyncClient,
) -> bytes:
    text = await translate_consultation_summary(consultation_id, patient_id, target_language, db)
    locale = VOICE_LOCALE_MAP.get(target_language, "hi-IN")
    return synthesize_speech(text, locale)


# ══════════════════════════════════════════════════════════════
#  Audio consultation — upload
# ══════════════════════════════════════════════════════════════

async def upload_audio_consultation(
    uid: str,
    filename: str,
    file_bytes: bytes,
    mime_type: str,
    language: str,
    db: firestore.AsyncClient,
) -> AudioConsultationUploadResponse:
    """Uploads audio to GCS, creates a pending Firestore record, returns the initial response."""
    consultation_id = str(uuid.uuid4())
    safe_name = filename.replace(" ", "_")
    blob_name = f"patients/{uid}/audio_consultations/{consultation_id}_{safe_name}"

    gcs_uri = await async_upload_bytes_to_gcs(blob_name, file_bytes, mime_type)
    now     = datetime.now(UTC)

    doc = {
        "id":                    consultation_id,
        "patientId":             uid,
        "status":                AudioConsultationStatus.pending.value,
        "file_path":             blob_name,
        "gcs_uri":               gcs_uri,
        "language":              language,
        "transcript":            None,
        "segments":              None,
        "medicines":             None,
        "follow_ups":            None,
        "reminder_suggestions":  None,
        "summary":               None,
        "doctor_name":           None,
        "key_diagnoses":         None,
        "attached_document_ids": [],
        "error_message":         None,
        "created_at":            now,
    }
    await db.collection(_COLL).document(consultation_id).set(doc)

    return AudioConsultationUploadResponse(
        id=consultation_id,
        status=AudioConsultationStatus.pending,
        file_path=blob_name,
        created_at=now,
    )


# ══════════════════════════════════════════════════════════════
#  Audio consultation — background processing
# ══════════════════════════════════════════════════════════════

def _extraction_prompt(transcript: str, language_name: str) -> str:
    return f"""You are a medical AI assistant. Analyse the following doctor-patient consultation transcript and return ONLY valid JSON with no markdown or explanation.

Return this exact JSON structure:
{{
  "summary": "<patient-friendly summary in {language_name} — 3-5 sentences covering diagnoses and care plan>",
  "doctor_name": "<doctor name if mentioned, null otherwise>",
  "key_diagnoses": ["<diagnosis 1>", "<diagnosis 2>"],
  "medicines": [
    {{
      "name": "<medicine name>",
      "dosage": "<e.g. 500mg, null if unknown>",
      "frequency": "<e.g. twice daily, null if unknown>",
      "instructions": "<e.g. after meals, null if unknown>",
      "duration": "<e.g. 7 days / ongoing, null if unknown>"
    }}
  ],
  "follow_ups": [
    {{
      "specialty": "<e.g. cardiology>",
      "reason": "<why needed>",
      "suggested_within_days": <integer or null>
    }}
  ],
  "reminder_suggestions": [
    {{
      "title": "<short title e.g. Take Metformin 500mg>",
      "type": "<medicine or follow_up>",
      "notes": "<optional patient note or null>",
      "medicine_details": {{
        "name": "<name>",
        "dosage": "<dosage or null>",
        "frequency": "<frequency or null>",
        "instructions": "<instructions or null>",
        "duration": "<duration or null>"
      }},
      "follow_up_details": null,
      "suggested_schedule": {{
        "recurrence": "<once | daily | weekly | monthly>",
        "time_of_day": "<HH:MM 24h e.g. 08:00>",
        "days_of_week": null
      }}
    }}
  ]
}}

Rules:
- medicine reminder → fill medicine_details, set follow_up_details to null
- follow_up reminder → fill follow_up_details (with specialty and reason), set medicine_details to null
- If a medicine is taken twice daily, create two reminder_suggestions (morning and evening)
- Return empty arrays [] when nothing is found — never omit a key

Transcript:
{transcript}"""


async def background_process_audio_consultation(
    consultation_id: str,
    uid: str,
    gcs_uri: str,
    language: str,
    db: firestore.AsyncClient,
) -> None:
    """
    Runs inside FastAPI BackgroundTasks:
    1. ElevenLabs STT (primary) → GCP Chirp (fallback)
    2. Gemini extraction of medicines / follow-ups / reminder suggestions
    3. Firestore update with completed status
    """
    doc_ref = db.collection(_COLL).document(consultation_id)
    await doc_ref.update({"status": AudioConsultationStatus.in_progress.value})

    try:
        # ── 1. Transcribe (ElevenLabs STT first, GCP fallback) ──
        transcription = await transcribe_audio(gcs_uri, VOICE_LOCALE_MAP.get(language, "en-IN"))
        transcript   = transcription.get("full_text", "")
        raw_segments = transcription.get("segments", [])

        if len(transcript.strip()) < 20:
            await doc_ref.update({
                "status":        AudioConsultationStatus.failed.value,
                "error_message": "Audio too short or no speech detected. Please upload a clearer recording.",
            })
            return

        # ── 2. Gemini extraction ─────────────────────────────────
        prompt   = _extraction_prompt(transcript, LANGUAGE_DISPLAY_NAMES.get(language, "English"))
        raw_json = await async_generate_gemini_content(prompt, json_response=True)

        try:
            extracted = json.loads(raw_json)
        except json.JSONDecodeError:
            logger.warning(f"Gemini returned non-JSON for {consultation_id}: {raw_json[:200]}")
            extracted = {}

        summary       = extracted.get("summary", "")
        doctor_name   = extracted.get("doctor_name")
        key_diagnoses = extracted.get("key_diagnoses") or []
        medicines     = [m for m in (extracted.get("medicines") or []) if isinstance(m, dict) and m.get("name")]
        follow_ups    = [f for f in (extracted.get("follow_ups") or []) if isinstance(f, dict) and f.get("specialty")]
        suggestions   = [s for s in (extracted.get("reminder_suggestions") or []) if isinstance(s, dict) and s.get("title")]

        # ── 3. Persist ───────────────────────────────────────────
        await doc_ref.update({
            "status":               AudioConsultationStatus.completed.value,
            "transcript":           transcript,
            "segments":             [{"speaker": s.get("speaker", "Speaker"), "text": s.get("text", "")}
                                     for s in raw_segments],
            "summary":              summary,
            "doctor_name":          doctor_name,
            "key_diagnoses":        key_diagnoses,
            "medicines":            medicines,
            "follow_ups":           follow_ups,
            "reminder_suggestions": suggestions,
            "error_message":        None,
        })
        logger.info(f"Audio consultation {consultation_id} processed successfully.")

    except Exception as e:
        logger.error(f"Audio consultation {consultation_id} failed: {e}")
        await doc_ref.update({
            "status":        AudioConsultationStatus.failed.value,
            "error_message": f"Processing error: {str(e)[:400]}",
        })


# ══════════════════════════════════════════════════════════════
#  Audio consultation — read / delete
# ══════════════════════════════════════════════════════════════

def _parse_suggestions(raw: list) -> list[ReminderSuggestion]:
    out = []
    for s in raw:
        if not isinstance(s, dict) or not s.get("title"):
            continue
        try:
            med_raw  = s.get("medicine_details")
            fup_raw  = s.get("follow_up_details")
            sched_raw = s.get("suggested_schedule")
            out.append(ReminderSuggestion(
                title=s["title"],
                type=s.get("type", "medicine"),
                notes=s.get("notes"),
                medicine_details=ExtractedMedicine(**med_raw)          if med_raw   else None,
                follow_up_details=FollowUpSuggestion(**fup_raw)        if fup_raw   else None,
                suggested_schedule=SuggestedReminderSchedule(**sched_raw) if sched_raw else None,
            ))
        except Exception as e:
            logger.warning(f"Skipping malformed suggestion: {e}")
    return out


def _to_response(doc_id: str, d: dict) -> AudioConsultationResponse:
    raw_lang = d.get("language", "en")
    try:
        language = SupportedLanguage(raw_lang)
    except ValueError:
        language = SupportedLanguage.english

    raw_segments = d.get("segments")
    raw_medicines = d.get("medicines")
    raw_follow_ups = d.get("follow_ups")
    raw_suggestions = d.get("reminder_suggestions")
    raw_diagnoses = d.get("key_diagnoses")

    raw_attached = d.get("attached_document_ids")

    return AudioConsultationResponse(
        id=doc_id,
        status=AudioConsultationStatus(d.get("status", "pending")),
        file_path=d.get("file_path", ""),
        language=language,
        transcript=d.get("transcript"),
        segments=[DiarizedSegment(**s) for s in raw_segments if s.get("text")] if raw_segments is not None else None,
        medicines=[ExtractedMedicine(**m) for m in raw_medicines if m.get("name")] if raw_medicines is not None else None,
        follow_ups=[FollowUpSuggestion(**f) for f in raw_follow_ups if f.get("specialty")] if raw_follow_ups is not None else None,
        reminder_suggestions=_parse_suggestions(raw_suggestions) if raw_suggestions is not None else None,
        key_diagnoses=raw_diagnoses if raw_diagnoses is not None else None,
        summary=d.get("summary"),
        doctor_name=d.get("doctor_name"),
        attached_document_ids=raw_attached if raw_attached else None,
        error_message=d.get("error_message"),
        created_at=d.get("created_at", datetime.now(UTC)),
    )


async def get_audio_consultations(uid: str, db: firestore.AsyncClient) -> list[AudioConsultationResponse]:
    docs = await (
        db.collection(_COLL)
        .where("patientId", "==", uid)
        .order_by("created_at", direction=firestore.Query.DESCENDING)
        .get()
    )
    results = []
    for doc in docs:
        try:
            results.append(_to_response(doc.id, doc.to_dict()))
        except Exception as e:
            logger.warning(f"Skipping malformed audio consultation {doc.id}: {e}")
    return results


async def get_audio_consultation(
    uid: str,
    consultation_id: str,
    db: firestore.AsyncClient,
) -> AudioConsultationResponse:
    snap = await db.collection(_COLL).document(consultation_id).get()
    if not snap.exists:
        raise ValueError("Audio consultation not found.")
    d = snap.to_dict()
    if d.get("patientId") != uid:
        raise PermissionError("Access is unauthorized.")
    return _to_response(snap.id, d)


async def delete_audio_consultation(
    uid: str,
    consultation_id: str,
    db: firestore.AsyncClient,
) -> None:
    snap = await db.collection(_COLL).document(consultation_id).get()
    if not snap.exists:
        raise ValueError("Audio consultation not found.")
    d = snap.to_dict()
    if d.get("patientId") != uid:
        raise PermissionError("Access is unauthorized.")

    blob_name = d.get("file_path")
    if blob_name:
        try:
            import asyncio
            from common_code.gcp_clients import _get_storage
            await asyncio.to_thread(
                lambda: _get_storage().bucket(settings.STORAGE_BUCKET_NAME).blob(blob_name).delete()
            )
        except Exception as e:
            logger.warning(f"GCS delete failed for {blob_name}: {e}")

    await db.collection(_COLL).document(consultation_id).delete()


async def listen_audio_consultation_summary(
    uid: str,
    consultation_id: str,
    db: firestore.AsyncClient,
    lang_override: Optional[str] = None,
) -> bytes:
    """Returns MP3 bytes of the consultation summary, optionally translated."""
    consultation = await get_audio_consultation(uid, consultation_id, db)
    if not consultation.summary:
        raise ValueError("No summary available — consultation may still be processing.")

    effective_lang = lang_override or consultation.language or "en"
    text           = consultation.summary

    if lang_override and lang_override != consultation.language:
        text = translate_text(consultation.summary, lang_override)

    locale = VOICE_LOCALE_MAP.get(effective_lang, "en-IN")
    return synthesize_speech(text, locale)




# ══════════════════════════════════════════════════════════════
#  Post-completion refinement
# ══════════════════════════════════════════════════════════════

def _refine_prompt(
    transcript: str,
    summary: str,
    medicines: list,
    follow_ups: list,
    reminder_suggestions: list,
    key_diagnoses: list,
    language_name: str,
    user_prompt: str,
) -> str:
    return f"""You are a medical AI assistant helping a patient refine their consultation notes.

Current consultation data:
---
Transcript:
{transcript or "(no transcript)"}

Summary:
{summary or "(no summary)"}

Medicines: {json.dumps(medicines or [], ensure_ascii=False)}
Follow-ups: {json.dumps(follow_ups or [], ensure_ascii=False)}
Key diagnoses: {json.dumps(key_diagnoses or [], ensure_ascii=False)}
Reminder suggestions: {json.dumps(reminder_suggestions or [], ensure_ascii=False)}
---

Patient instruction: "{user_prompt}"

Apply the patient's instruction to the consultation data above and return ONLY valid JSON with no markdown or explanation.
Use the exact same JSON structure as the original extraction. Keep all fields that are not mentioned in the instruction unchanged.
Generate the summary in {language_name}.

Return:
{{
  "transcript": "<updated or unchanged transcript>",
  "summary": "<updated patient-friendly summary>",
  "doctor_name": "<string or null>",
  "key_diagnoses": ["<diagnosis>"],
  "medicines": [{{"name": "...", "dosage": "...", "frequency": "...", "instructions": "...", "duration": "..."}}],
  "follow_ups": [{{"specialty": "...", "reason": "...", "suggested_within_days": null}}],
  "reminder_suggestions": [
    {{
      "title": "...", "type": "medicine or follow_up", "notes": null,
      "medicine_details": {{"name": "...", "dosage": "...", "frequency": "...", "instructions": "...", "duration": "..."}},
      "follow_up_details": null,
      "suggested_schedule": {{"recurrence": "daily", "time_of_day": "09:00", "days_of_week": null}}
    }}
  ]
}}"""


async def refine_consultation(
    uid: str,
    consultation_id: str,
    req: RefineConsultationRequest,
    db: firestore.AsyncClient,
) -> AudioConsultationResponse:
    """
    Applies a user's plain-text instruction to an already-completed consultation.
    Sends the current data + instruction to Gemini and writes the delta back to Firestore.
    Only available when status = completed.
    """
    snap = await db.collection(_COLL).document(consultation_id).get()
    if not snap.exists:
        raise ValueError("Audio consultation not found.")
    d = snap.to_dict()
    if d.get("patientId") != uid:
        raise PermissionError("Access is unauthorized.")
    if d.get("status") != AudioConsultationStatus.completed.value:
        raise ValueError("Refinement is only available after the consultation has finished processing.")

    prompt = _refine_prompt(
        transcript=d.get("transcript", ""),
        summary=d.get("summary", ""),
        medicines=d.get("medicines") or [],
        follow_ups=d.get("follow_ups") or [],
        reminder_suggestions=d.get("reminder_suggestions") or [],
        key_diagnoses=d.get("key_diagnoses") or [],
        language_name=LANGUAGE_DISPLAY_NAMES.get(d.get("language", "en"), "English"),
        user_prompt=req.prompt,
    )

    raw_json = await async_generate_gemini_content(prompt, json_response=True)

    try:
        extracted = json.loads(raw_json)
    except json.JSONDecodeError:
        logger.warning(f"Gemini returned non-JSON for refine {consultation_id}: {raw_json[:200]}")
        raise ValueError("AI could not process the instruction — please rephrase and try again.")

    medicines     = [m for m in (extracted.get("medicines") or []) if isinstance(m, dict) and m.get("name")]
    follow_ups    = [f for f in (extracted.get("follow_ups") or []) if isinstance(f, dict) and f.get("specialty")]
    suggestions   = [s for s in (extracted.get("reminder_suggestions") or []) if isinstance(s, dict) and s.get("title")]
    key_diagnoses = extracted.get("key_diagnoses") or d.get("key_diagnoses") or []
    summary       = extracted.get("summary") or d.get("summary", "")
    transcript    = extracted.get("transcript") or d.get("transcript", "")
    doctor_name   = extracted.get("doctor_name") or d.get("doctor_name")

    await db.collection(_COLL).document(consultation_id).update({
        "transcript":           transcript,
        "summary":              summary,
        "doctor_name":          doctor_name,
        "key_diagnoses":        key_diagnoses,
        "medicines":            medicines,
        "follow_ups":           follow_ups,
        "reminder_suggestions": suggestions,
    })

    updated = await db.collection(_COLL).document(consultation_id).get()
    return _to_response(consultation_id, updated.to_dict())
