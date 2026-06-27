from fastapi import APIRouter, Depends, HTTPException, status
from google.cloud import firestore
from common_code.firestore import get_db, log_audit_event
from common_code.firebase_auth import require_role
from doctor_service.consultations.consultations_model import (
    StartConsultationRequest,
    SignedAudioUrlResponse,
    TranscriptionResponse,
    ExtractionResponse,
    ReviewConsultationRequest,
    PublishReportResponse
)
from doctor_service.consultations.consultations_func import (
    initiate_consultation,
    get_upload_audio_url,
    transcribe_consult_audio,
    extract_consult_entities,
    review_and_save_consult,
    publish_consult_report
)

router = APIRouter()
doctor_gate = require_role(["doctor"])

@router.post("/start", status_code=status.HTTP_201_CREATED)
async def start_consultation(
    req: StartConsultationRequest,
    current_user: dict = Depends(doctor_gate),
    db: firestore.AsyncClient = Depends(get_db)
):
    """Starts a new consultation process, validating that patient access OTP is active."""
    doctor_uid = current_user.get("uid")
    try:
        consult_id = await initiate_consultation(doctor_uid, req.patient_id, db)
        await log_audit_event(actor=doctor_uid, action="START_CONSULTATION", target=consult_id)
        return {"consultation_id": consult_id}
    except PermissionError as e:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=str(e))

@router.get("/{consult_id}/audio-url", response_model=SignedAudioUrlResponse)
async def get_audio_url(
    consult_id: str,
    current_user: dict = Depends(doctor_gate),
    db: firestore.AsyncClient = Depends(get_db)
):
    """Generates signed GCS destination path for upload of consultation recordings."""
    doctor_uid = current_user.get("uid")
    try:
        url_resp = await get_upload_audio_url(doctor_uid, consult_id, db)
        return url_resp
    except PermissionError as e:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(e))
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=str(e))

@router.post("/{consult_id}/transcribe", response_model=TranscriptionResponse)
async def transcribe(
    consult_id: str,
    current_user: dict = Depends(doctor_gate),
    db: firestore.AsyncClient = Depends(get_db)
):
    """Triggers speech-to-text transcription on the uploaded audio recording file."""
    doctor_uid = current_user.get("uid")
    try:
        resp = await transcribe_consult_audio(doctor_uid, consult_id, db)
        await log_audit_event(actor=doctor_uid, action="TRANSCRIBE_CONSULT_AUDIO", target=consult_id)
        return resp
    except PermissionError as e:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(e))
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=str(e))

@router.post("/{consult_id}/extract", response_model=ExtractionResponse)
async def extract_entities(
    consult_id: str,
    current_user: dict = Depends(doctor_gate),
    db: firestore.AsyncClient = Depends(get_db)
):
    """Applies Vertex AI Gemini model to structured clinical details extraction and ICD-11 coding."""
    doctor_uid = current_user.get("uid")
    try:
        entities = await extract_consult_entities(doctor_uid, consult_id, db)
        await log_audit_event(actor=doctor_uid, action="EXTRACT_CONSULT_AI", target=consult_id)
        return entities
    except PermissionError as e:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(e))
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=str(e))

@router.put("/{consult_id}/review")
async def review_consultation(
    consult_id: str,
    req: ReviewConsultationRequest,
    current_user: dict = Depends(doctor_gate),
    db: firestore.AsyncClient = Depends(get_db)
):
    """Saves doctor modifications and edits to extracted symptoms, diagnoses, or prescriptions."""
    doctor_uid = current_user.get("uid")
    try:
        await review_and_save_consult(doctor_uid, consult_id, req, db)
        await log_audit_event(actor=doctor_uid, action="REVIEW_CONSULTATION", target=consult_id)
        return {"status": "success", "message": "Consultation details committed."}
    except PermissionError as e:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(e))
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=str(e))

@router.post("/{consult_id}/publish", response_model=PublishReportResponse)
async def publish_report(
    consult_id: str,
    current_user: dict = Depends(doctor_gate),
    db: firestore.AsyncClient = Depends(get_db)
):
    """Finalizes consultation report, renders PDF prescription card, sets reminders relative to meals, and saves analytics."""
    doctor_uid = current_user.get("uid")
    try:
        resp = await publish_consult_report(doctor_uid, consult_id, db)
        await log_audit_event(actor=doctor_uid, action="PUBLISH_CONSULTATION", target=consult_id)
        return resp
    except PermissionError as e:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(e))
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=str(e))
