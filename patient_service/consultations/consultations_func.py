import asyncio
import json
import logging
import time
import uuid
from datetime import datetime, UTC
from typing import Optional
from zoneinfo import ZoneInfo

from google.cloud import firestore

from common_code.config import settings
from common_code.notification_dispatcher import dispatch_notification
from common_code.pii_masker import mask_pii
from common_code.gcp_clients import (
    async_generate_gemini_content,
    async_generate_gemini_content_with_usage,
    async_upload_bytes_to_gcs,
    async_download_bytes_from_gcs,
    async_delete_gcs_prefix,
    generate_signed_download_url,
    synthesize_speech,
    transcribe_audio,
    translate_text,
    VOICE_LOCALE_MAP,
)
from patient_service.consultations.consultations_model import (
    AttachedDocument,
    AudioConsultationListItem,
    AudioConsultationResponse,
    AudioConsultationStatus,
    AudioConsultationUploadResponse,
    ConsultationEvalMetrics,
    DiarizedSegment,
    ExtractedMedicine,
    ICDCode,
    PatientConsultationDetail,
    RefineConsultationRequest,
    ReminderSuggestion,
)
from patient_service.documents.documents_model import LANGUAGE_DISPLAY_NAMES, SupportedLanguage

logger = logging.getLogger(__name__)

_COLL = settings.AUDIO_CONSULTATIONS_COLLECTION  # "audio_consultations"

# Valid MealTiming enum values — kept here to avoid a circular import from reminders_model.
# If reminders_model.MealTiming gains new values, update this set too.
_VALID_MEAL_TIMINGS = frozenset({
    "before_breakfast", "after_breakfast",
    "before_lunch",     "after_lunch",
    "before_dinner",    "after_dinner",
    "specific_time",
})


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


async def translate_audio_consultation_summary(
    consultation_id: str,
    patient_id: str,
    target_language: str,
    db: firestore.AsyncClient,
) -> str:
    snap = await db.collection(_COLL).document(consultation_id).get()
    if not snap.exists:
        raise ValueError("Audio consultation not found.")
    d = snap.to_dict()
    if d.get("patientId") != patient_id:
        raise PermissionError("Access is unauthorized.")
    summary = d.get("summary") or ""
    if not summary.strip():
        raise ValueError("Consultation summary is not available yet. Please wait until processing is complete.")
    return translate_text(summary, target_language)


async def listen_consultation_summary(
    consultation_id: str,
    patient_id: str,
    target_language: str,
    db: firestore.AsyncClient,
) -> bytes:
    cache_path = f"tts/consultations/{consultation_id}/{target_language}.mp3"
    cached = await async_download_bytes_from_gcs(cache_path)
    if cached:
        return cached

    text   = await translate_consultation_summary(consultation_id, patient_id, target_language, db)
    locale = VOICE_LOCALE_MAP.get(target_language, "hi-IN")
    audio  = synthesize_speech(text, locale)
    await async_upload_bytes_to_gcs(cache_path, audio, content_type="audio/mpeg")
    return audio


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
        "title":                 None,
        "transcript":            None,
        "segments":              None,
        "medicines":             None,
        "reminder_suggestions":  None,
        "summary":               None,
        "doctor_name":           None,
        "key_diagnoses":         None,
        "attached_document_ids": [],
        "icd_codes":             None,
        "pii_map":               {},
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
    return f"""You are a medical AI assistant. Analyse the following transcript and return ONLY valid JSON with no markdown or explanation.

CRITICAL RULES — read before extracting:
1. Set "is_medical_consultation" to true ONLY if a doctor or healthcare professional is explicitly advising the patient about their health, prescribing medicines, or recommending follow-ups. Emotional conversations, general chats, random audio, or non-clinical content must be false.
2. If "is_medical_consultation" is false, return empty arrays [] for medicines and reminder_suggestions, and [] for key_diagnoses.
3. NEVER invent or infer medicines, diagnoses, or follow-ups. Only extract information that is EXPLICITLY AND VERBATIM spoken in the transcript. If a medicine name is not literally spoken, do not include it.
4. Do not suggest a reminder for a medicine unless the doctor or speaker explicitly names that medicine in the transcript.
5. Do not suggest a follow-up unless a follow-up visit or specialist referral is explicitly mentioned.

Return this exact JSON structure:
{{
  "is_medical_consultation": <true | false>,
  "summary": "<patient-friendly summary in {language_name} — if not a medical consultation write a one-sentence note saying so>",
  "doctor_name": "<doctor name if spoken, null otherwise>",
  "key_diagnoses": ["<only diagnoses explicitly stated>"],
  "icd_codes": [
    {{
      "code": "<ICD-10-CM code e.g. E11.9>",
      "description": "<standard ICD-10 condition name>"
    }}
  ],
  "medicines": [
    {{
      "name": "<exact medicine name as spoken>",
      "dosage": "<dosage as spoken, null if not mentioned>",
      "frequency": "<frequency as spoken, null if not mentioned>",
      "instructions": "<instructions as spoken, null if not mentioned>",
      "duration": "<duration as spoken, null if not mentioned>"
    }}
  ],
  "reminder_suggestions": [
    {{
      "type": "<medicine | follow_up>",
      "title": "<short title using the exact medicine name or follow-up as spoken>",
      "notes": null,
      "notification_enabled": true,
      "schedule": {{
        "recurrence": "<once | daily | weekly | monthly>",
        "time_of_day": "<HH:MM 24h IST e.g. 08:00>",
        "start_date": null,
        "end_date": null,
        "meal_timing": null
      }},
      "medicine_details": {{
        "name": "<exact medicine name as spoken>",
        "dosage": "<dosage or null>",
        "frequency": "<frequency or null>",
        "instructions": "<instructions or null>"
      }},
      "follow_up_details": {{
        "specialty": "<specialty or null>",
        "reason": "<reason or null>",
        "urgency": "<urgent | routine | elective>",
        "appointment_date": null,
        "appointment_time": null
      }}
    }}
  ]
}}

Additional rules:
- icd_codes: map each entry in key_diagnoses to its ICD-10-CM code. Only include codes you are certain about — omit an entry rather than guessing. Return [] if no diagnoses were explicitly stated.
- medicine reminder → type = "medicine", fill medicine_details, set follow_up_details to null
- follow_up reminder → type = "follow_up", fill follow_up_details, set medicine_details to null
- recurrence for follow-up appointments is usually "once"
- Create EXACTLY ONE reminder_suggestion per distinct medicine name. Do NOT create multiple suggestions for the same medicine. For medicines taken multiple times per day (e.g. "3 times daily"), set time_of_day to the morning/first dose time (e.g. "08:00") and put the full dosing schedule in the notes field (e.g. "3 times daily — 8 AM, 2 PM, 8 PM"). The patient can add extra reminders themselves if needed.
- start_date and appointment_date are always null — the patient sets them when confirming
- Return [] for any array with no valid data — never omit a key
- meal_timing must be exactly one of these values or null: before_breakfast, after_breakfast, before_lunch, after_lunch, before_dinner, after_dinner, specific_time. Do NOT use any other string — not "before_meals", "after_meals", "post_meal", "pre_meal", or similar. If the transcript says "before meals" / "after meals" and you cannot determine which specific meal, set meal_timing to null and set time_of_day instead (e.g. "08:00" for morning, "13:00" for afternoon, "20:00" for evening).

Transcript:
{transcript}"""


async def _infer_speaker_roles(segments: list[dict]) -> dict[str, str]:
    """Uses Gemini to map Scribe speaker IDs (speaker_0, speaker_1 …) to Doctor/Patient.

    Analyses the first 30 segments, looking for medical-authority patterns
    (diagnostic questions, prescribing, treatment explanations → Doctor) vs
    symptom descriptions and personal disclosures (→ Patient).

    Returns e.g. {"speaker_0": "Doctor", "speaker_1": "Patient"}.
    Falls back to {"speaker_0": "Patient"} on any error.
    """
    if not segments:
        return {}

    speaker_ids = list({s.get("speaker_id") for s in segments if s.get("speaker_id")})
    if len(speaker_ids) < 2:
        # Only one speaker detected — label as Patient
        return {sid: "Patient" for sid in speaker_ids}

    sample = segments[:30]
    sample_text = "\n".join(
        f"[{s['speaker_id']}]: {s['text']}"
        for s in sample
        if s.get("speaker_id") and s.get("text")
    )
    sample_text = mask_pii(sample_text).masked_text  # strip PII before sending to Gemini

    prompt = (
        "You are analysing a medical consultation transcript that has been automatically labelled "
        "with speaker IDs (speaker_0, speaker_1, etc.) by a speech diarization system.\n\n"
        "Your task: determine which speaker ID corresponds to the Doctor and which to the Patient.\n\n"
        "Doctor signals: asks diagnostic questions, explains conditions, prescribes medicines, "
        "gives medical instructions, uses clinical terminology, asks about symptoms.\n"
        "Patient signals: describes personal symptoms, answers questions, shares health history, "
        "expresses concern about their health.\n\n"
        "Sample transcript:\n"
        f"{sample_text}\n\n"
        "Return ONLY a valid JSON object mapping each speaker_id to exactly one of: "
        '"Doctor", "Patient", or "Unknown". No explanation, no markdown.\n'
        f"Speaker IDs present: {speaker_ids}\n"
        'Example: {"speaker_0": "Doctor", "speaker_1": "Patient"}'
    )

    try:
        raw = await async_generate_gemini_content(prompt, json_response=True)
        mapping = json.loads(raw)
        # Validate — ensure only known speaker IDs are returned
        return {k: v for k, v in mapping.items() if k in speaker_ids}
    except Exception as e:
        logger.warning(f"Speaker role inference failed: {e}")
        return {sid: "Unknown" for sid in speaker_ids}


async def _generate_consultation_title(transcript: str, language: str) -> str | None:
    """Generates a short, descriptive title for the consultation using Gemini.

    Returns None if Gemini fails or the transcript is too short.
    """
    if len(transcript.strip()) < 50:
        return None

    excerpt = transcript[:800]
    prompt = (
        "Generate a concise, descriptive title (5–8 words) for this medical consultation. "
        "The title should capture the primary health concern or topic discussed. "
        "Write the title in English regardless of the transcript language.\n"
        "Good examples: 'Diabetes Management and Medication Review', "
        "'Hypertension Follow-up with ECG Results', 'Respiratory Infection Diagnosis'.\n"
        "Return ONLY the title text — no quotes, no explanation, no punctuation at the end.\n\n"
        f"Transcript excerpt:\n{excerpt}"
    )
    try:
        title = await async_generate_gemini_content(prompt)
        title = title.strip().strip('"').strip("'")
        return title if title else None
    except Exception as e:
        logger.warning(f"Consultation title generation failed: {e}")
        return None


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

    process_start = time.monotonic()

    try:
        # ── 1. Transcribe (ElevenLabs STT) ───────────────────────────────────
        stt_start     = time.monotonic()
        transcription = await transcribe_audio(gcs_uri)
        stt_duration  = time.monotonic() - stt_start

        transcript   = transcription.get("full_text", "")
        raw_segments = transcription.get("segments", [])

        if len(transcript.strip()) < 20:
            await doc_ref.update({
                "status":        AudioConsultationStatus.failed.value,
                "error_message": "Audio too short or no speech detected. Please upload a clearer recording.",
            })
            return

        # ── 1b. Mask PII before any LLM call ────────────────────────────────
        pii_result        = mask_pii(transcript)
        masked_transcript = pii_result.masked_text
        pii_map           = pii_result.replacement_map
        if pii_map:
            logger.info(f"[{consultation_id}] Masked {len(pii_map)} PII token(s) before LLM calls.")

        # ── 1c. Speaker role inference + title (parallel, both use masked text)
        role_map, consultation_title = await asyncio.gather(
            _infer_speaker_roles(raw_segments),
            _generate_consultation_title(masked_transcript, language),
        )

        # Build enriched segments with named roles
        enriched_segments = [
            {
                "speaker_id": s.get("speaker_id"),
                "role":       role_map.get(s.get("speaker_id"), "Unknown"),
                "text":       s.get("text", ""),
                "start_time": s.get("start_time"),
                "end_time":   s.get("end_time"),
            }
            for s in raw_segments
            if s.get("text", "").strip()
        ]

        # ── 2. Gemini extraction (masked transcript, capture token usage) ────
        extraction_start = time.monotonic()
        prompt           = _extraction_prompt(masked_transcript, LANGUAGE_DISPLAY_NAMES.get(language, "English"))
        raw_json, gemini_usage = await async_generate_gemini_content_with_usage(prompt, json_response=True)
        extraction_duration    = time.monotonic() - extraction_start

        gemini_input_tokens  = gemini_usage.get("prompt_token_count", 0)
        gemini_output_tokens = gemini_usage.get("candidates_token_count", 0)
        # Gemini 2.5 Flash pricing: $0.075/1M input, $0.30/1M output
        estimated_cost_usd = (
            (gemini_input_tokens / 1_000_000) * 0.075
            + (gemini_output_tokens / 1_000_000) * 0.30
        )

        try:
            extracted = json.loads(raw_json)
        except json.JSONDecodeError:
            logger.warning(f"[{consultation_id}] Gemini returned non-JSON: {raw_json[:200]}")
            await doc_ref.update({
                "status":        AudioConsultationStatus.failed.value,
                "transcript":    transcript,
                "pii_map":       pii_map,
                "error_message": "Clinical extraction failed — AI returned an unreadable response. Please try again.",
            })
            return

        summary     = extracted.get("summary", "")
        doctor_name = extracted.get("doctor_name")
        is_medical  = bool(extracted.get("is_medical_consultation", True))

        hallucinated_dropped = 0
        safety_warnings      = 0

        if not is_medical:
            logger.info(f"[{consultation_id}] Gemini flagged as non-medical — skipping clinical extraction.")
            key_diagnoses = []
            icd_codes     = []
            medicines     = []
            suggestions   = []
        else:
            transcript_lower = transcript.lower()

            # ── Diagnoses — word-match guard against transcript ───────────────
            raw_diagnoses = extracted.get("key_diagnoses") or []
            key_diagnoses = []
            for dx in raw_diagnoses:
                if not isinstance(dx, str):
                    continue
                words = [w for w in dx.lower().split() if len(w) >= 4]
                if words and not any(w in transcript_lower for w in words):
                    logger.info(f"[{consultation_id}] Dropping hallucinated diagnosis: {dx!r}")
                    hallucinated_dropped += 1
                else:
                    key_diagnoses.append(dx)

            # ── ICD codes — description must match transcript or a kept diagnosis ─
            diagnoses_lower = " ".join(key_diagnoses).lower()
            icd_codes = []
            for item in (extracted.get("icd_codes") or []):
                if not isinstance(item, dict) or not item.get("code") or not item.get("description"):
                    continue
                desc_words = [w for w in item["description"].lower().split() if len(w) >= 4]
                if desc_words and not any(
                    w in transcript_lower or w in diagnoses_lower for w in desc_words
                ):
                    logger.info(f"[{consultation_id}] Dropping hallucinated ICD code: {item['code']}")
                    hallucinated_dropped += 1
                else:
                    icd_codes.append({"code": item["code"], "description": item["description"]})

            medicines = [m for m in (extracted.get("medicines") or []) if isinstance(m, dict) and m.get("name")]
            raw_sugg  = [s for s in (extracted.get("reminder_suggestions") or []) if isinstance(s, dict) and s.get("title")]

            # ── Medicine suggestion guard — name must appear in transcript ────
            suggestions = []
            for s in raw_sugg:
                if s.get("type") == "medicine":
                    med_name = (s.get("medicine_details") or {}).get("name") or s.get("title", "")
                    words    = [w for w in med_name.lower().split() if len(w) >= 4]
                    if words and not any(w in transcript_lower for w in words):
                        logger.info(f"[{consultation_id}] Dropping hallucinated medicine suggestion: {med_name}")
                        hallucinated_dropped += 1
                        continue
                suggestions.append(s)

            # ── Resolve start_date / end_date on medicine suggestions ─────────
            suggestions = _resolve_suggestion_dates(suggestions)

            # ── Safety checks — allergy, drug interactions, dosing ────────────
            if medicines:
                try:
                    from common_code.safety_engine import run_safety_checks
                    profile_snap    = await db.collection(settings.PATIENTS_COLLECTION).document(uid).get()
                    patient_profile = profile_snap.to_dict() if profile_snap.exists else {}
                    safety_result   = await run_safety_checks(
                        db=db,
                        patient_id=uid,
                        prescribed_medicines=medicines,
                        patient_profile=patient_profile,
                    )
                    all_warnings = (
                        safety_result.get("setup_warnings", [])
                        + safety_result.get("allergy_conflicts", [])
                        + safety_result.get("drug_interactions", [])
                        + safety_result.get("duplicate_therapies", [])
                        + safety_result.get("dosing_warnings", [])
                    )
                    safety_warnings = len(all_warnings)
                    if safety_warnings:
                        logger.warning(
                            f"[{consultation_id}] {safety_warnings} safety warning(s): "
                            + str(all_warnings)[:400]
                        )
                except Exception as se:
                    logger.warning(f"[{consultation_id}] Safety check failed (non-blocking): {se}")

        # Calculate STT Cost (ElevenLabs Scribe v2 pricing: $0.01/min -> $0.000167/sec)
        audio_duration_secs = transcription.get("audio_duration_secs") or 0.0
        stt_cost_usd = audio_duration_secs * (0.01 / 60.0)

        # Calculate Total Cost in INR (using exchange rate 1 USD = 94 INR)
        total_cost_usd = estimated_cost_usd + stt_cost_usd
        estimated_cost_inr = total_cost_usd * 94.0

        total_duration = time.monotonic() - process_start
        logger.info(
            f"[{consultation_id}] processed in {total_duration:.1f}s "
            f"(STT {stt_duration:.1f}s, extraction {extraction_duration:.1f}s) "
            f"tokens={gemini_input_tokens}+{gemini_output_tokens} "
            f"cost=${estimated_cost_usd:.5f} (STT cost=${stt_cost_usd:.5f}, INR total=₹{estimated_cost_inr:.4f}) "
            f"dropped={hallucinated_dropped}"
        )

        # ── 3. Persist — store ORIGINAL transcript (PII intact) ──────────────
        update: dict = {
            "status":               AudioConsultationStatus.completed.value,
            "transcript":           transcript,
            "pii_map":              pii_map,
            "segments":             enriched_segments,
            "summary":              summary,
            "doctor_name":          doctor_name,
            "key_diagnoses":        key_diagnoses,
            "icd_codes":            icd_codes,
            "medicines":            medicines,
            "reminder_suggestions": suggestions,
            "error_message":        None,
        }
        if consultation_title:
            update["title"] = consultation_title
        await doc_ref.update(update)

        # Store Eval metrics in a sub-collection "evals" inside the document under "metrics"
        eval_metrics_dict = {
            "stt_duration_s":        round(stt_duration, 2),
            "extraction_duration_s": round(extraction_duration, 2),
            "total_duration_s":      round(total_duration, 2),
            "gemini_input_tokens":   gemini_input_tokens,
            "gemini_output_tokens":  gemini_output_tokens,
            "estimated_cost_usd":    round(estimated_cost_usd, 6),
            "stt_cost_usd":          round(stt_cost_usd, 6),
            "estimated_cost_inr":    round(estimated_cost_inr, 4),
            "hallucinated_dropped":  hallucinated_dropped,
            "safety_warnings":       safety_warnings,
        }
        await doc_ref.collection("evals").document("metrics").set(eval_metrics_dict)
        logger.info(f"Audio consultation {consultation_id} processed successfully.")

        # Build a specific body from the extracted data we already have
        _parts = []
        if medicines:
            _parts.append(f"{len(medicines)} medicine{'s' if len(medicines) != 1 else ''}")
        if key_diagnoses:
            _parts.append(f"{len(key_diagnoses)} {'diagnoses' if len(key_diagnoses) != 1 else 'diagnosis'}")
        if any(s.get("type") == "follow_up" for s in suggestions):
            _parts.append("follow-up advice")
        _insight = (", ".join(_parts) + " identified") if _parts else "summary ready"

        # {patient_first_name} is substituted by the dispatcher via format_safe
        _notif_body = "Hi {patient_first_name}, your consultation has been analysed — " + _insight + ". Tap to view."

        await dispatch_notification(
            patient_id=uid,
            title=None,   # uses template default: "Consultation analysis ready 🩺"
            body=_notif_body,
            notification_type="audio_consultation",
            extra_data={"consultation_id": consultation_id},
        )

    except Exception as e:
        logger.error(f"Audio consultation {consultation_id} failed: {e}")
        await doc_ref.update({
            "status":        AudioConsultationStatus.failed.value,
            "error_message": f"Processing error: {str(e)[:400]}",
        })
        await dispatch_notification(
            patient_id=uid,
            title="Consultation processing failed",
            body="We could not analyse your recording. Please try uploading again.",
            notification_type="audio_consultation",
            extra_data={"consultation_id": consultation_id},
        )


# ══════════════════════════════════════════════════════════════
#  Audio consultation — read / delete
# ══════════════════════════════════════════════════════════════

def _parse_suggestions(raw: list) -> list[ReminderSuggestion]:
    """Parses AI-generated suggestion dicts into ReminderCreateRequest objects.

    Handles two formats:
    - New: has a 'schedule' key matching ReminderSchedule (post-refactor)
    - Old: has a 'suggested_schedule' key (pre-refactor, stored in Firestore before migration)
    """
    out = []
    for s in raw:
        if not isinstance(s, dict) or not s.get("title"):
            continue
        try:
            # Backward-compat: migrate old suggested_schedule → schedule
            if "suggested_schedule" in s and "schedule" not in s:
                old = s.pop("suggested_schedule") or {}
                s["schedule"] = {
                    "recurrence": old.get("recurrence") or "daily",
                    "time_of_day": old.get("time_of_day") or "09:00",
                    "start_date": None,
                    "end_date": None,
                }
            # Default schedule if entirely missing
            if not s.get("schedule"):
                s["schedule"] = {"recurrence": "daily", "time_of_day": "09:00"}
            # Normalize and validate meal_timing. Gemini may return generic strings
            # like "before_meals" that are not valid MealTiming enum values.
            # Clear them so the time_of_day fallback is used rather than the whole
            # suggestion being dropped by a Pydantic ValidationError.
            sched = s.get("schedule") or {}
            if isinstance(sched, dict):
                mt = sched.get("meal_timing")
                if isinstance(mt, str):
                    normalized = mt.strip().lower().replace(" ", "_")
                    sched["meal_timing"] = normalized if normalized in _VALID_MEAL_TIMINGS else None
                s["schedule"] = sched

            out.append(ReminderSuggestion(**s))
        except Exception as e:
            logger.warning(
                f"Skipping malformed suggestion '{s.get('title', '?')}': {e} | raw={s}"
            )
    return out


# Approximate IST clock times for each meal_timing value.
# Used to decide whether today's dose has already passed when resolving start_date.
_MEAL_TIMING_TIMES: dict[str, tuple[int, int]] = {
    "before_breakfast": (8,   0),
    "after_breakfast":  (9,  30),
    "before_lunch":     (13,  0),
    "after_lunch":      (14,  0),
    "before_dinner":    (19, 30),
    "after_dinner":     (21,  0),
    "specific_time":    (9,   0),  # fallback; overridden by time_of_day when present
}

_IST = ZoneInfo("Asia/Kolkata")


def _resolve_suggestion_dates(suggestions: list[dict]) -> list[dict]:
    """Fill in start_date and end_date on medicine reminder suggestion dicts.

    This runs on the raw dicts before they are written to Firestore so that the
    dates are persisted correctly. (The ReminderSchedule Pydantic validator runs
    only during GET / parse, which is too late to save the computed dates.)

    Logic:
    - start_date: today (IST) if the dose time has not yet passed, else tomorrow.
    - end_date:   start_date + (duration_days − 1) if a day/week/month count is
                  present in medicine_details.duration; omitted for "ongoing".
    """
    import re
    from datetime import date, timedelta

    now_ist   = datetime.now(_IST)
    today_ist = now_ist.date()

    def _parse_reminder_hour_minute(sched: dict) -> tuple[int, int]:
        """Return (hour, minute) IST for the scheduled dose."""
        tod = (sched.get("time_of_day") or "").strip()
        if tod and ":" in tod:
            try:
                h, m = map(int, tod.split(":")[:2])
                return h, m
            except ValueError:
                pass
        mt = sched.get("meal_timing") or ""
        return _MEAL_TIMING_TIMES.get(mt, (9, 0))

    def _parse_duration_days(duration_str: str) -> int | None:
        """Return total days from strings like '2 days', '1 week', '3 months'."""
        s = (duration_str or "").lower().strip()
        if not s or any(kw in s for kw in ("ongoing", "indefinite", "chronic", "long")):
            return None
        m = re.search(r"(\d+)\s*(day|week|month)", s)
        if not m:
            return None
        n    = int(m.group(1))
        unit = m.group(2)
        if unit == "week":
            return n * 7
        if unit == "month":
            return n * 30
        return n  # days

    for s in suggestions:
        if s.get("type") != "medicine":
            continue
        sched = s.get("schedule")
        if not isinstance(sched, dict):
            continue
        if sched.get("start_date") is not None:
            continue  # already resolved — don't overwrite

        h, m       = _parse_reminder_hour_minute(sched)
        dose_mins  = h * 60 + m
        now_mins   = now_ist.hour * 60 + now_ist.minute
        start_date: date = today_ist if now_mins < dose_mins else today_ist + timedelta(days=1)

        sched["start_date"] = start_date.isoformat()

        duration_str = ((s.get("medicine_details") or {}).get("duration") or "").strip()
        duration_days = _parse_duration_days(duration_str)
        if duration_days is not None:
            # end_date is the last day the patient should take the medicine.
            # start_date counts as day 1, so end_date = start_date + duration_days - 1.
            end_date = start_date + timedelta(days=duration_days - 1)
            sched["end_date"] = end_date.isoformat()

        s["schedule"] = sched

    return suggestions


async def _to_response(doc_id: str, d: dict, db: firestore.AsyncClient) -> AudioConsultationResponse:
    raw_lang = d.get("language", "en")
    try:
        language = SupportedLanguage(raw_lang)
    except ValueError:
        language = SupportedLanguage.english

    raw_segments    = d.get("segments")
    raw_medicines   = d.get("medicines")
    raw_suggestions = d.get("reminder_suggestions")
    raw_diagnoses   = d.get("key_diagnoses")
    raw_icd         = d.get("icd_codes") or []
    raw_ids         = d.get("attached_document_ids") or []

    # Parse segments — guard individually so one corrupt entry never breaks the whole GET
    segments: Optional[list[DiarizedSegment]] = None
    if raw_segments is not None:
        segments = []
        for s in raw_segments:
            if not s.get("text"):
                continue
            try:
                segments.append(DiarizedSegment(**s))
            except Exception as seg_err:
                logger.warning(f"[{doc_id}] Skipping corrupt segment: {seg_err}")

    # Parse ICD codes
    icd_codes: list[ICDCode] = []
    for item in raw_icd:
        if isinstance(item, dict) and item.get("code") and item.get("description"):
            try:
                icd_codes.append(ICDCode(**item))
            except Exception:
                pass

    # Batch-fetch titles for all attached documents in one round-trip
    attached_documents: Optional[list[AttachedDocument]] = None
    if raw_ids:
        refs = [db.collection(settings.DOCUMENTS_COLLECTION).document(did) for did in raw_ids]
        snaps = []
        async for snap in db.get_all(refs):
            if snap.exists:
                snaps.append(AttachedDocument(id=snap.id, title=(snap.to_dict() or {}).get("title")))
        attached_documents = snaps or None

    return AudioConsultationResponse(
        id=doc_id,
        status=AudioConsultationStatus(d.get("status", "pending")),
        file_path=d.get("file_path", ""),
        title=d.get("title"),
        language=language,
        transcript=d.get("transcript"),
        segments=segments,
        medicines=[ExtractedMedicine(**m) for m in raw_medicines if m.get("name")] if raw_medicines is not None else None,
        reminder_suggestions=_parse_suggestions(raw_suggestions) if raw_suggestions is not None else None,
        key_diagnoses=raw_diagnoses if raw_diagnoses is not None else None,
        icd_codes=icd_codes or None,
        summary=d.get("summary"),
        doctor_name=d.get("doctor_name"),
        attached_documents=attached_documents,
        error_message=d.get("error_message"),
        created_at=d.get("created_at", datetime.now(UTC)),
    )


_LIST_FIELDS = [
    "patientId", "status", "file_path", "title", "language",
    "summary", "doctor_name", "key_diagnoses", "icd_codes", "error_message", "created_at",
]


def _to_list_item(doc_id: str, d: dict) -> AudioConsultationListItem:
    raw_lang = d.get("language", "en")
    try:
        language = SupportedLanguage(raw_lang)
    except ValueError:
        language = SupportedLanguage.english

    raw_icd = d.get("icd_codes") or []
    icd_codes: list[ICDCode] = []
    for item in raw_icd:
        if isinstance(item, dict) and item.get("code") and item.get("description"):
            try:
                icd_codes.append(ICDCode(**item))
            except Exception:
                pass

    return AudioConsultationListItem(
        id=doc_id,
        status=AudioConsultationStatus(d.get("status", "pending")),
        file_path=d.get("file_path", ""),
        title=d.get("title"),
        language=language,
        summary=d.get("summary"),
        doctor_name=d.get("doctor_name"),
        key_diagnoses=d.get("key_diagnoses") or None,
        icd_codes=icd_codes or None,
        error_message=d.get("error_message"),
        created_at=d.get("created_at", datetime.now(UTC)),
    )


async def get_audio_consultations(uid: str, db: firestore.AsyncClient) -> list[AudioConsultationListItem]:
    docs = await (
        db.collection(_COLL)
        .where("patientId", "==", uid)
        .select(_LIST_FIELDS)
        .get()
    )
    results = []
    for doc in docs:
        try:
            results.append(_to_list_item(doc.id, doc.to_dict()))
        except Exception as e:
            logger.warning(f"Skipping malformed audio consultation {doc.id}: {e}")
    results.sort(key=lambda r: r.created_at or datetime.min.replace(tzinfo=UTC), reverse=True)
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
    return await _to_response(snap.id, d, db)


async def get_consultation_eval(
    uid: str,
    consultation_id: str,
    db: firestore.AsyncClient,
) -> ConsultationEvalMetrics:
    snap = await db.collection(_COLL).document(consultation_id).get()
    if not snap.exists:
        raise ValueError("Audio consultation not found.")
    d = snap.to_dict()
    if d.get("patientId") != uid:
        raise PermissionError("Access is unauthorized.")

    eval_ref = db.collection(_COLL).document(consultation_id).collection("evals").document("metrics")
    eval_snap = await eval_ref.get()
    if eval_snap.exists:
        ed = eval_snap.to_dict() or {}
        return ConsultationEvalMetrics(
            stt_duration_s=ed.get("stt_duration_s"),
            extraction_duration_s=ed.get("extraction_duration_s"),
            total_duration_s=ed.get("total_duration_s"),
            gemini_input_tokens=ed.get("gemini_input_tokens"),
            gemini_output_tokens=ed.get("gemini_output_tokens"),
            estimated_cost_usd=ed.get("estimated_cost_usd"),
            stt_cost_usd=ed.get("stt_cost_usd"),
            estimated_cost_inr=ed.get("estimated_cost_inr"),
            hallucinated_dropped=ed.get("hallucinated_dropped"),
            safety_warnings=ed.get("safety_warnings"),
        )
    else:
        # Fallback to main document fields for backwards-compatibility
        _eval_keys = (
            "stt_duration_s", "extraction_duration_s", "total_duration_s",
            "gemini_input_tokens", "gemini_output_tokens", "estimated_cost_usd",
            "hallucinated_dropped", "safety_warnings",
        )
        if not any(k in d for k in _eval_keys):
            raise ValueError("Eval metrics are not available yet — consultation may still be processing.")
        return ConsultationEvalMetrics(
            stt_duration_s=d.get("stt_duration_s"),
            extraction_duration_s=d.get("extraction_duration_s"),
            total_duration_s=d.get("total_duration_s"),
            gemini_input_tokens=d.get("gemini_input_tokens"),
            gemini_output_tokens=d.get("gemini_output_tokens"),
            estimated_cost_usd=d.get("estimated_cost_usd"),
            stt_cost_usd=d.get("stt_cost_usd"),
            estimated_cost_inr=d.get("estimated_cost_inr"),
            hallucinated_dropped=d.get("hallucinated_dropped"),
            safety_warnings=d.get("safety_warnings"),
        )


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
            from common_code.gcp_clients import _get_storage
            await asyncio.to_thread(
                lambda: _get_storage().bucket(settings.STORAGE_BUCKET_NAME).blob(blob_name).delete()
            )
        except Exception as e:
            logger.warning(f"GCS delete failed for {blob_name}: {e}")

    # Delete evals/metrics sub-collection document if exists
    try:
        await db.collection(_COLL).document(consultation_id).collection("evals").document("metrics").delete()
    except Exception as e:
        logger.warning(f"Failed to delete evals/metrics for {consultation_id}: {e}")

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

    cache_path = f"tts/audio_consultations/{consultation_id}/{effective_lang}.mp3"
    cached = await async_download_bytes_from_gcs(cache_path)
    if cached:
        return cached

    text = consultation.summary
    if lang_override and lang_override != consultation.language:
        text = translate_text(consultation.summary, lang_override)

    locale = VOICE_LOCALE_MAP.get(effective_lang, "en-IN")
    audio  = synthesize_speech(text, locale)
    await async_upload_bytes_to_gcs(cache_path, audio, content_type="audio/mpeg")
    return audio




# ══════════════════════════════════════════════════════════════
#  Post-completion refinement
# ══════════════════════════════════════════════════════════════

def _refine_prompt(
    transcript: str,
    summary: str,
    medicines: list,
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
Key diagnoses: {json.dumps(key_diagnoses or [], ensure_ascii=False)}
---

Patient instruction: "{user_prompt}"

IMPORTANT:
- Apply ONLY the change described in the patient's instruction. Do not remove, alter, or omit any existing data that is not mentioned in the instruction.
- Only return reminder_suggestions for NEW items introduced by this instruction (e.g. a new medicine or follow-up just added). Do NOT reproduce existing reminder suggestions — they are preserved automatically. Return an empty array [] for reminder_suggestions if the instruction does not add a new medicine or follow-up.
- Only include medicines and diagnoses that are explicitly mentioned in the transcript or added by this instruction. Never invent clinical data.
- Generate the summary in {language_name}.

Return ONLY valid JSON with no markdown:
{{
  "summary": "<updated patient-friendly summary — must include all original content plus the change>",
  "doctor_name": "<string or null>",
  "key_diagnoses": ["<only diagnoses explicitly stated>"],
  "icd_codes": [{{"code": "<ICD-10-CM code>", "description": "<standard condition name>"}}],
  "medicines": [{{"name": "...", "dosage": "...", "frequency": "...", "instructions": "...", "duration": "..."}}],
  "reminder_suggestions": [
    {{
      "type": "<medicine | follow_up>",
      "title": "...", "notes": null, "notification_enabled": true,
      "schedule": {{"recurrence": "daily", "time_of_day": "09:00", "start_date": null, "end_date": null, "meal_timing": null}},
      "medicine_details": {{"name": "...", "dosage": "...", "frequency": "...", "instructions": "..."}},
      "follow_up_details": {{"specialty": "...", "reason": "...", "urgency": "routine", "appointment_date": null, "appointment_time": null}}
    }}
  ]
}}

Rules for reminder_suggestions:
- Create EXACTLY ONE suggestion per distinct medicine name. For medicines taken multiple times per day, set time_of_day to the morning/first dose time and put the full schedule in notes (e.g. "3 times daily — 8 AM, 2 PM, 8 PM").
- meal_timing must be exactly one of: before_breakfast, after_breakfast, before_lunch, after_lunch, before_dinner, after_dinner, specific_time — or null. Do NOT use "before_meals", "after_meals", "post_meal", or similar."""


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

    original_transcript = d.get("transcript", "")
    masked_transcript   = mask_pii(original_transcript).masked_text  # strip PII before Gemini

    prompt = _refine_prompt(
        transcript=masked_transcript,
        summary=d.get("summary", ""),
        medicines=d.get("medicines") or [],
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

    # ── Transcript is read-only — never overwrite with Gemini output ──────────
    # The transcript is the source of truth (actual audio). Refine only corrects
    # the extracted layer (summary, medicines, diagnoses, suggestions).
    transcript = d.get("transcript", "")

    # ── Summary: use Gemini's if non-empty, else keep existing ───────────────
    existing_summary = d.get("summary", "")
    summary = extracted.get("summary") or existing_summary

    # ── Medicines: use Gemini's updated list; fall back to existing if empty ─
    new_medicines = [m for m in (extracted.get("medicines") or []) if isinstance(m, dict) and m.get("name")]
    medicines     = new_medicines if new_medicines else (d.get("medicines") or [])

    # ── Diagnoses: union of existing + any new ones Gemini added ─────────────
    existing_diagnoses = d.get("key_diagnoses") or []
    new_diagnoses      = [x for x in (extracted.get("key_diagnoses") or []) if isinstance(x, str) and x.strip()]
    key_diagnoses      = existing_diagnoses + [x for x in new_diagnoses if x not in existing_diagnoses]

    # ── ICD codes: use Gemini's updated list if non-empty, else keep existing ─
    new_icd_raw = [
        i for i in (extracted.get("icd_codes") or [])
        if isinstance(i, dict) and i.get("code") and i.get("description")
    ]
    icd_codes = new_icd_raw if new_icd_raw else (d.get("icd_codes") or [])

    # ── Doctor name: keep existing if Gemini returns nothing ─────────────────
    doctor_name = extracted.get("doctor_name") or d.get("doctor_name")

    # ── Merge reminder_suggestions: existing + validated new ones ──
    # Existing suggestions from Firestore are always preserved.
    # Gemini only returns net-new suggestions (instructed by the prompt).
    # We cross-check medicine names against the transcript before appending.
    existing_suggestions = d.get("reminder_suggestions") or []
    existing_titles = {s.get("title", "").lower() for s in existing_suggestions if isinstance(s, dict)}

    transcript_lower = transcript.lower()
    new_suggestions = []
    for s in (extracted.get("reminder_suggestions") or []):
        if not isinstance(s, dict) or not s.get("title"):
            continue
        if s.get("title", "").lower() in existing_titles:
            continue  # already present, skip duplicate
        if s.get("type") == "medicine":
            med_name = (s.get("medicine_details") or {}).get("name") or s.get("title", "")
            words = [w for w in med_name.lower().split() if len(w) >= 4]
            if words and not any(w in transcript_lower for w in words):
                logger.info(f"[{consultation_id}] Dropping hallucinated refine suggestion: {med_name}")
                continue
        new_suggestions.append(s)

    suggestions = existing_suggestions + new_suggestions

    await db.collection(_COLL).document(consultation_id).update({
        # transcript intentionally excluded — refine never mutates the source audio text
        "summary":              summary,
        "doctor_name":          doctor_name,
        "key_diagnoses":        key_diagnoses,
        "icd_codes":            icd_codes,
        "medicines":            medicines,
        "reminder_suggestions": suggestions,
    })

    # Invalidate TTS cache — summary has changed
    await async_delete_gcs_prefix(f"tts/audio_consultations/{consultation_id}/")

    updated = await db.collection(_COLL).document(consultation_id).get()
    return await _to_response(consultation_id, updated.to_dict(), db)
