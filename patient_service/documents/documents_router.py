from fastapi import APIRouter, Depends, HTTPException, status, Query, UploadFile, File, BackgroundTasks, Form
from fastapi.responses import Response
from google.cloud import firestore
from typing import List, Optional
from common_code.firestore import get_db, log_audit_event
from common_code.firebase_auth import require_role
from patient_service.documents.documents_model import (
    DocumentResponse,
    TranslateSummaryRequest,
    TranslateSummaryResponse
)
from patient_service.documents.documents_func import (
    get_patient_documents,
    translate_document_summary,
    synthesize_summary_speech,
    upload_and_process_document,
    background_parse_and_index_document
)


router = APIRouter()
patient_gate = require_role(["patient"])


@router.post("/upload", response_model=DocumentResponse, status_code=status.HTTP_201_CREATED)
async def upload_document(
    background_tasks: BackgroundTasks,
    file: UploadFile = File(...),
    title: Optional[str] = Form(None, description="Optional user-defined title for the document"),
    description: Optional[str] = Form(None, description="Optional user-defined description or notes about the document"),
    current_user: dict = Depends(patient_gate),
    db: firestore.AsyncClient = Depends(get_db)
):
    """Directly uploads a medical document/image, detects MIME type, uploads to GCS, parses and stores it."""
    uid = current_user.get("uid")
    try:
        file_bytes = await file.read()
        mime_type = file.content_type or "application/pdf"
        doc = await upload_and_process_document(uid, file.filename, file_bytes, mime_type, db, title, description)
        background_tasks.add_task(
            background_parse_and_index_document,
            doc.id,
            uid,
            doc.file_path,
            mime_type,
            db,
            title,
            description
        )
        await log_audit_event(
            actor=uid, 
            action="UPLOAD_AND_PARSE_REPORT", 
            target=doc.id, 
            details={"title": title}
        )
        return doc
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("", response_model=List[DocumentResponse])
async def get_documents(
    current_user: dict = Depends(patient_gate),
    db: firestore.AsyncClient = Depends(get_db)
):
    """Retrieves patient medical history documents list."""
    uid = current_user.get("uid")
    docs = await get_patient_documents(uid, db)
    return docs

@router.post("/{doc_id}/translate", response_model=TranslateSummaryResponse)
async def translate_summary(
    doc_id: str,
    req: TranslateSummaryRequest,
    current_user: dict = Depends(patient_gate),
    db: firestore.AsyncClient = Depends(get_db)
):
    """Translates the plain-English summary of an existing document into the patient's language."""
    uid = current_user.get("uid")
    try:
        translated = await translate_document_summary(uid, doc_id, req.target_language, db)
        await log_audit_event(actor=uid, action="TRANSLATE_REPORT", target=doc_id, details={"lang": req.target_language})
        return translated
    except PermissionError as e:
        raise HTTPException(status_code=403, detail=str(e))
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@router.get("/{doc_id}/listen")
async def listen_summary(
    doc_id: str,
    lang: str = Query("en", description="Locale language to generate speech for (e.g. en, hi, ta, te)"),
    current_user: dict = Depends(patient_gate),
    db: firestore.AsyncClient = Depends(get_db)
):
    """Generates audio reading of the document summary and streams back the audio file."""
    uid = current_user.get("uid")
    try:
        audio_content = await synthesize_summary_speech(uid, doc_id, lang, db)
        await log_audit_event(actor=uid, action="AUDIO_LISTEN_REPORT", target=doc_id, details={"lang": lang})
        return Response(content=audio_content, media_type="audio/mpeg")
    except PermissionError as e:
        raise HTTPException(status_code=403, detail=str(e))
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
