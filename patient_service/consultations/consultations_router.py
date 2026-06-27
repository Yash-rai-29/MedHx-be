from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.responses import Response
from google.cloud import firestore
from typing import List
from common_code.firestore import get_db, log_audit_event
from common_code.firebase_auth import require_role
from patient_service.consultations.consultations_model import (
    PatientConsultationDetail,
    TranslateSummaryRequest,
    TranslateSummaryResponse
)
from patient_service.consultations.consultations_func import (
    get_patient_consultations,
    get_patient_consultation_by_id,
    translate_consultation_summary,
    listen_consultation_summary
)

router = APIRouter()
patient_gate = require_role(["patient"])

@router.get("", response_model=List[PatientConsultationDetail])
async def get_consultations(
    current_user: dict = Depends(patient_gate),
    db: firestore.AsyncClient = Depends(get_db)
):
    """Retrieves all finalized consultation summaries and prescriptions for the patient."""
    patient_id = current_user.get("uid")
    consultations = await get_patient_consultations(patient_id, db)
    return consultations

@router.get("/{id}", response_model=PatientConsultationDetail)
async def get_consultation_by_id(
    id: str,
    current_user: dict = Depends(patient_gate),
    db: firestore.AsyncClient = Depends(get_db)
):
    """Gets details of a specific consultation report."""
    patient_id = current_user.get("uid")
    try:
        consultation = await get_patient_consultation_by_id(id, patient_id, db)
        return consultation
    except PermissionError as e:
        raise HTTPException(status_code=403, detail=str(e))
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))

@router.post("/{id}/translate", response_model=TranslateSummaryResponse)
async def translate_summary(
    id: str,
    req: TranslateSummaryRequest,
    current_user: dict = Depends(patient_gate),
    db: firestore.AsyncClient = Depends(get_db)
):
    """Translates the summary of the consultation into the requested Indian language."""
    patient_id = current_user.get("uid")
    try:
        translated = await translate_consultation_summary(id, patient_id, req.target_language, db)
        await log_audit_event(
            actor=patient_id,
            action="TRANSLATE_CONSULTATION_SUMMARY",
            target=id,
            details={"language": req.target_language}
        )
        return TranslateSummaryResponse(translated_text=translated, language=req.target_language)
    except PermissionError as e:
        raise HTTPException(status_code=403, detail=str(e))
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

@router.get("/{id}/listen")
async def listen_summary(
    id: str,
    lang: str = "hi",
    current_user: dict = Depends(patient_gate),
    db: firestore.AsyncClient = Depends(get_db)
):
    """Generates and returns synthesized text-to-speech audio bytes of the translated summary."""
    patient_id = current_user.get("uid")
    try:
        audio_bytes = await listen_consultation_summary(id, patient_id, lang, db)
        await log_audit_event(
            actor=patient_id,
            action="LISTEN_CONSULTATION_SUMMARY",
            target=id,
            details={"language": lang}
        )
        return Response(content=audio_bytes, media_type="audio/mpeg")
    except PermissionError as e:
        raise HTTPException(status_code=403, detail=str(e))
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
