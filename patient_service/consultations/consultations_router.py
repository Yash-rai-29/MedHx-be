from fastapi import APIRouter, BackgroundTasks, Depends, File, Form, HTTPException, Query, UploadFile, status
from fastapi.responses import Response
from google.cloud import firestore
from typing import List, Optional

from common_code.config import settings
from common_code.firestore import get_db, log_audit_event
from common_code.firebase_auth import require_role
from patient_service.consultations.consultations_model import (
    AudioConsultationResponse,
    AudioConsultationUploadResponse,
    DeleteAudioConsultationResponse,
    PatientConsultationDetail,
    RefineConsultationRequest,
    TranslateSummaryResponse,
)
from patient_service.documents.documents_model import SupportedLanguage, TranslateSummaryRequest
from patient_service.consultations.consultations_func import (
    get_patient_consultations,
    get_patient_consultation_by_id,
    listen_consultation_summary,
    translate_consultation_summary,
    background_process_audio_consultation,
    delete_audio_consultation,
    get_audio_consultation,
    get_audio_consultations,
    listen_audio_consultation_summary,
    refine_consultation,
    upload_audio_consultation,
)

router = APIRouter()
patient_gate = require_role(["patient"])

_ALLOWED_MIME = {
    "audio/mpeg", "audio/mp3", "audio/wav", "audio/wave", "audio/x-wav",
    "audio/mp4", "audio/m4a", "audio/ogg", "audio/webm",
}


# ══════════════════════════════════════════════════════════════
#  List (no path param — must come first to avoid /{id} clash)
# ══════════════════════════════════════════════════════════════

@router.get("", response_model=List[PatientConsultationDetail])
async def get_consultations(
    current_user: dict = Depends(patient_gate),
    db: firestore.AsyncClient = Depends(get_db),
):
    """Lists all finalized doctor-published consultation summaries for the patient."""
    return await get_patient_consultations(current_user["uid"], db)


# ══════════════════════════════════════════════════════════════
#  Audio consultations — static prefix routes must precede /{id}
# ══════════════════════════════════════════════════════════════

@router.post(
    "/upload",
    response_model=AudioConsultationUploadResponse,
    status_code=status.HTTP_201_CREATED,
)
async def upload_audio_consultation_endpoint(
    background_tasks: BackgroundTasks,
    file: UploadFile = File(..., description="Audio file (mp3, wav, m4a, ogg, webm)"),
    language: SupportedLanguage = Form(SupportedLanguage.english, description="Primary spoken language"),
    current_user: dict = Depends(patient_gate),
    db: firestore.AsyncClient = Depends(get_db),
):
    """
    Uploads a patient-recorded consultation audio.
    ElevenLabs STT transcribes it; Gemini extracts medicines, follow-ups, and reminder suggestions.
    Processing runs in the background — poll GET /consultations/audio/{id} until status = completed.
    """
    uid       = current_user["uid"]
    mime_type = file.content_type or "audio/mpeg"
    if mime_type not in _ALLOWED_MIME:
        raise HTTPException(
            status_code=415,
            detail=f"Unsupported file type '{mime_type}'. Accepted: mp3, wav, m4a, ogg, webm.",
        )

    try:
        file_bytes = await file.read()
        if not file_bytes:
            raise HTTPException(status_code=400, detail="Uploaded file is empty.")

        record = await upload_audio_consultation(
            uid=uid,
            filename=file.filename or "recording.mp3",
            file_bytes=file_bytes,
            mime_type=mime_type,
            language=language,
            db=db,
        )

        gcs_uri = f"gs://{settings.STORAGE_BUCKET_NAME}/{record.file_path}"
        background_tasks.add_task(
            background_process_audio_consultation,
            record.id, uid, gcs_uri, language, db,
        )

        await log_audit_event(actor=uid, action="UPLOAD_AUDIO_CONSULTATION",
                              target=record.id, details={"language": language})
        return record

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/audio", response_model=List[AudioConsultationResponse])
async def list_audio_consultations(
    current_user: dict = Depends(patient_gate),
    db: firestore.AsyncClient = Depends(get_db),
):
    """Lists all patient-uploaded audio consultations, newest first."""
    try:
        return await get_audio_consultations(current_user["uid"], db)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/audio/{consultation_id}", response_model=AudioConsultationResponse)
async def get_audio_consultation_endpoint(
    consultation_id: str,
    current_user: dict = Depends(patient_gate),
    db: firestore.AsyncClient = Depends(get_db),
):
    """Gets a single audio consultation by ID. Poll until status = completed."""
    try:
        return await get_audio_consultation(current_user["uid"], consultation_id, db)
    except PermissionError as e:
        raise HTTPException(status_code=403, detail=str(e))
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.delete(
    "/audio/{consultation_id}",
    response_model=DeleteAudioConsultationResponse,
    status_code=status.HTTP_200_OK,
)
async def delete_audio_consultation_endpoint(
    consultation_id: str,
    current_user: dict = Depends(patient_gate),
    db: firestore.AsyncClient = Depends(get_db),
):
    """Deletes an audio consultation record and its GCS audio file."""
    uid = current_user["uid"]
    try:
        await delete_audio_consultation(uid, consultation_id, db)
        await log_audit_event(actor=uid, action="DELETE_AUDIO_CONSULTATION",
                              target=consultation_id, details={})
        return DeleteAudioConsultationResponse(
            id=consultation_id,
            message="Audio consultation deleted successfully.",
        )
    except PermissionError as e:
        raise HTTPException(status_code=403, detail=str(e))
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.patch("/audio/{consultation_id}/refine", response_model=AudioConsultationResponse)
async def refine_audio_consultation_endpoint(
    consultation_id: str,
    req: RefineConsultationRequest,
    current_user: dict = Depends(patient_gate),
    db: firestore.AsyncClient = Depends(get_db),
):
    """
    Applies a plain-text instruction to a completed consultation.
    Gemini updates the transcript, summary, medicines, follow-ups, and reminder suggestions in place.
    Only available when status = completed.
    """
    uid = current_user["uid"]
    try:
        result = await refine_consultation(uid, consultation_id, req, db)
        await log_audit_event(actor=uid, action="REFINE_AUDIO_CONSULTATION",
                              target=consultation_id, details={"prompt_len": len(req.prompt)})
        return result
    except PermissionError as e:
        raise HTTPException(status_code=403, detail=str(e))
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get(
    "/audio/{consultation_id}/listen",
    responses={200: {"content": {"audio/mpeg": {}}, "description": "MP3 of the consultation summary"}},
)
async def listen_audio_consultation_endpoint(
    consultation_id: str,
    lang: Optional[SupportedLanguage] = Query(None, description="Override language for TTS (defaults to summary language)"),
    current_user: dict = Depends(patient_gate),
    db: firestore.AsyncClient = Depends(get_db),
):
    """
    Returns synthesized speech (MP3) of the audio consultation summary via ElevenLabs TTS.
    If lang is provided and differs from the summary language, the summary is translated first.
    """
    uid = current_user["uid"]
    try:
        audio = await listen_audio_consultation_summary(uid, consultation_id, db, lang_override=lang)
        await log_audit_event(actor=uid, action="LISTEN_AUDIO_CONSULTATION",
                              target=consultation_id, details={"lang": lang or "auto"})
        return Response(content=audio, media_type="audio/mpeg")
    except PermissionError as e:
        raise HTTPException(status_code=403, detail=str(e))
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# ══════════════════════════════════════════════════════════════
#  Legacy — doctor-published consultations (parameterized last)
# ══════════════════════════════════════════════════════════════

@router.get("/{consultation_id}", response_model=PatientConsultationDetail)
async def get_consultation_by_id(
    consultation_id: str,
    current_user: dict = Depends(patient_gate),
    db: firestore.AsyncClient = Depends(get_db),
):
    """Gets a single doctor-published consultation by ID."""
    try:
        return await get_patient_consultation_by_id(consultation_id, current_user["uid"], db)
    except PermissionError as e:
        raise HTTPException(status_code=403, detail=str(e))
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))


@router.post("/{consultation_id}/translate", response_model=TranslateSummaryResponse)
async def translate_summary(
    consultation_id: str,
    req: TranslateSummaryRequest,
    current_user: dict = Depends(patient_gate),
    db: firestore.AsyncClient = Depends(get_db),
):
    """Translates the doctor-consultation summary into the requested language."""
    uid = current_user["uid"]
    try:
        text = await translate_consultation_summary(consultation_id, uid, req.target_language, db)
        await log_audit_event(actor=uid, action="TRANSLATE_CONSULTATION_SUMMARY",
                              target=consultation_id, details={"language": req.target_language})
        return TranslateSummaryResponse(translated_text=text, language=req.target_language)
    except PermissionError as e:
        raise HTTPException(status_code=403, detail=str(e))
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.get(
    "/{consultation_id}/listen",
    responses={200: {"content": {"audio/mpeg": {}}, "description": "MP3 of the consultation summary"}},
)
async def listen_summary(
    consultation_id: str,
    lang: SupportedLanguage = Query(SupportedLanguage.hindi, description="Language for TTS"),
    current_user: dict = Depends(patient_gate),
    db: firestore.AsyncClient = Depends(get_db),
):
    """Returns synthesized speech (MP3) of the doctor-consultation summary."""
    uid = current_user["uid"]
    try:
        audio = await listen_consultation_summary(consultation_id, uid, lang, db)
        await log_audit_event(actor=uid, action="LISTEN_CONSULTATION_SUMMARY",
                              target=consultation_id, details={"language": lang})
        return Response(content=audio, media_type="audio/mpeg")
    except PermissionError as e:
        raise HTTPException(status_code=403, detail=str(e))
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
