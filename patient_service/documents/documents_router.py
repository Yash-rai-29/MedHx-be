from fastapi import APIRouter, Depends, HTTPException, status, Query, UploadFile, File, BackgroundTasks, Form
from fastapi.responses import Response
from google.cloud import firestore
from typing import List, Optional
from common_code.firestore import get_db, log_audit_event
from common_code.firebase_auth import require_role
from patient_service.documents.documents_model import (
    DocumentResponse,
    DeleteDocumentResponse,
    SupportedLanguage,
    TranslateSummaryRequest,
    TranslateSummaryResponse
)
from patient_service.documents.documents_func import (
    get_patient_documents,
    delete_document,
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
    description: Optional[str] = Form(None, description="Optional notes about the document"),
    language: Optional[SupportedLanguage] = Form(None, description="Language to generate the summary in (default: English)"),
    consultation_id: Optional[str] = Form(None, description="Audio consultation ID to attach this document to"),
    current_user: dict = Depends(patient_gate),
    db: firestore.AsyncClient = Depends(get_db)
):
    """Uploads a medical document, detects MIME type, parses and stores it.
    If consultation_id is provided the document is linked to that audio consultation
    and appears in both the documents list and the consultation view.
    If language is provided the Gemini summary, red_flags, and actionable_steps
    are generated in that language; otherwise English is used."""
    uid = current_user.get("uid")
    try:
        file_bytes = await file.read()
        mime_type = file.content_type or "application/pdf"
        doc = await upload_and_process_document(
            uid, file.filename, file_bytes, mime_type, db, title, description, language, consultation_id
        )
        background_tasks.add_task(
            background_parse_and_index_document,
            doc.id, uid, doc.file_path, mime_type, db, title, description, language
        )
        await log_audit_event(
            actor=uid,
            action="UPLOAD_AND_PARSE_REPORT",
            target=doc.id,
            details={"title": title, "language": language.value if language else "en"}
        )
        return doc
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("", response_model=List[DocumentResponse])
async def get_documents(
    current_user: dict = Depends(patient_gate),
    db: firestore.AsyncClient = Depends(get_db)
):
    """Retrieves all medical history documents for the authenticated patient."""
    uid = current_user.get("uid")
    return await get_patient_documents(uid, db)


@router.post("/{doc_id}/translate", response_model=TranslateSummaryResponse)
async def translate_summary(
    doc_id: str,
    req: TranslateSummaryRequest,
    current_user: dict = Depends(patient_gate),
    db: firestore.AsyncClient = Depends(get_db)
):
    """Translates the document summary into the requested language (SupportedLanguage enum)."""
    uid = current_user.get("uid")
    try:
        translated = await translate_document_summary(uid, doc_id, req.target_language, db)
        await log_audit_event(
            actor=uid, action="TRANSLATE_REPORT", target=doc_id,
            details={"lang": req.target_language}
        )
        return translated
    except PermissionError as e:
        raise HTTPException(status_code=403, detail=str(e))
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.delete("/{doc_id}", response_model=DeleteDocumentResponse, status_code=status.HTTP_200_OK)
async def remove_document(
    doc_id: str,
    current_user: dict = Depends(patient_gate),
    db: firestore.AsyncClient = Depends(get_db)
):
    """Deletes a document record and its GCS file. Only the owning patient can delete."""
    uid = current_user.get("uid")
    try:
        await delete_document(uid, doc_id, db)
        await log_audit_event(actor=uid, action="DELETE_DOCUMENT", target=doc_id, details={})
        return DeleteDocumentResponse(id=doc_id, message="Document deleted successfully.")
    except PermissionError as e:
        raise HTTPException(status_code=403, detail=str(e))
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get(
    "/{doc_id}/listen",
    responses={200: {"content": {"audio/mpeg": {}}, "description": "MP3 audio of the document summary"}},
)
async def listen_summary(
    doc_id: str,
    lang: Optional[SupportedLanguage] = Query(
        None,
        description="Override language for speech. Defaults to the language the summary was generated in."
    ),
    current_user: dict = Depends(patient_gate),
    db: firestore.AsyncClient = Depends(get_db)
):
    """Streams an MP3 audio reading of the document summary.
    By default uses the document's own language (e.g. Tamil summary → Tamil speech).
    Pass ?lang=hi to override."""
    uid = current_user.get("uid")
    try:
        audio_content = await synthesize_summary_speech(uid, doc_id, db, lang_override=lang)
        await log_audit_event(
            actor=uid, action="AUDIO_LISTEN_REPORT", target=doc_id,
            details={"lang": lang.value if lang else "auto"}
        )
        return Response(content=audio_content, media_type="audio/mpeg")
    except PermissionError as e:
        raise HTTPException(status_code=403, detail=str(e))
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
