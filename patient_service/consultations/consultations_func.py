import logging
from google.cloud import firestore
from common_code.config import settings
from common_code.gcp_clients import (
    translate_text,
    synthesize_speech,
    generate_signed_download_url,
    VOICE_LOCALE_MAP
)
from patient_service.consultations.consultations_model import PatientConsultationDetail

logger = logging.getLogger(__name__)

async def get_patient_consultations(patient_id: str, db: firestore.AsyncClient) -> list[PatientConsultationDetail]:
    """Retrieves all finalized (published) consultations for the patient."""
    docs = await db.collection(settings.CONSULTATIONS_COLLECTION) \
        .where("patientId", "==", patient_id) \
        .where("status", "==", "published") \
        .order_by("createdAt", direction=firestore.Query.DESCENDING) \
        .get()
        
    consultations = []
    for doc in docs:
        d = doc.to_dict()
        pdf_ref = d.get("pdfRef")
        pdf_url = generate_signed_download_url(pdf_ref) if pdf_ref else None
        
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
            pdf_url=pdf_url
        ))
    return consultations

async def get_patient_consultation_by_id(
    consultation_id: str,
    patient_id: str,
    db: firestore.AsyncClient
) -> PatientConsultationDetail:
    """Retrieves details of a specific consultation, enforcing ownership and publication status."""
    doc_snap = await db.collection(settings.CONSULTATIONS_COLLECTION).document(consultation_id).get()
    if not doc_snap.exists:
        raise ValueError("Consultation not found.")
        
    d = doc_snap.to_dict()
    if d.get("patientId") != patient_id:
        raise PermissionError("Access to this consultation is unauthorized.")
        
    if d.get("status") != "published":
        raise ValueError("This consultation report is not yet finalized.")
        
    pdf_ref = d.get("pdfRef")
    pdf_url = generate_signed_download_url(pdf_ref) if pdf_ref else None
    
    return PatientConsultationDetail(
        id=doc_snap.id,
        doctorId=d.get("doctorId"),
        patientId=d.get("patientId"),
        status=d.get("status"),
        createdAt=d.get("createdAt"),
        summary_en=d.get("summary_en"),
        diagnoses=d.get("diagnoses", []),
        medicines=d.get("medicines", []),
        follow_up_days=d.get("follow_up_days", 0),
        pdfRef=pdf_ref,
        pdf_url=pdf_url
    )

async def translate_consultation_summary(
    consultation_id: str,
    patient_id: str,
    target_language: str,
    db: firestore.AsyncClient
) -> str:
    """Translates the summary of a consultation into a preferred Indian language."""
    consult = await get_patient_consultation_by_id(consultation_id, patient_id, db)
    summary = consult.summary_en
    if not summary:
        raise ValueError("No summary content available to translate.")
        
    return translate_text(summary, target_language)

async def listen_consultation_summary(
    consultation_id: str,
    patient_id: str,
    target_language: str,
    db: firestore.AsyncClient
) -> bytes:
    """Translates the summary and returns synthesized text-to-speech audio bytes."""
    translated_text = await translate_consultation_summary(consultation_id, patient_id, target_language, db)
    locale_code = VOICE_LOCALE_MAP.get(target_language, "hi-IN")
    
    return synthesize_speech(translated_text, locale_code)
