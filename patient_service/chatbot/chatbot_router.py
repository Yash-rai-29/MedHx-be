from fastapi import APIRouter, Depends, HTTPException, status, Request, WebSocket, WebSocketDisconnect, Query
from google.cloud import firestore
from typing import List
from common_code.firestore import get_db, log_audit_event
from common_code.firebase_auth import require_role
from common_code.config import settings
from firebase_admin import auth
import uuid
import logging
from common_code.gcp_clients import upload_bytes_to_gcs, transcribe_audio, synthesize_speech

logger = logging.getLogger(__name__)
from patient_service.chatbot.chatbot_model import (
    ChatRequest,
    ChatResponse,
    ChatSessionCreateRequest,
    ChatSessionResponse,
    ChatSessionDetailResponse
)
from patient_service.chatbot.chatbot_func import (
    answer_patient_query,
    create_chat_session,
    get_patient_chat_sessions,
    get_chat_session_details,
    ask_inside_session
)

router = APIRouter()
patient_gate = require_role(["patient"])

@router.post("/ask", response_model=ChatResponse)
async def ask_companion(
    req: ChatRequest,
    request: Request,
    current_user: dict = Depends(patient_gate),
    db: firestore.AsyncClient = Depends(get_db)
):
    """Answers patient health or medical history questions using safe, tenant-isolated AI retrieval (RAG)."""
    uid = current_user.get("uid")
    try:
        response = await answer_patient_query(uid, req.prompt, db)
        await log_audit_event(actor=uid, action="CHAT_COMPANION", target=uid, request=request)
        return response
    except Exception as e:
        await log_audit_event(
            actor=uid,
            action="CHAT_COMPANION",
            target=uid,
            status="failed",
            details={"error": str(e)},
            request=request
        )
        raise HTTPException(status_code=500, detail=str(e))

@router.post("/sessions", response_model=ChatSessionResponse, status_code=status.HTTP_201_CREATED)
async def create_session(
    req: ChatSessionCreateRequest,
    request: Request,
    current_user: dict = Depends(patient_gate),
    db: firestore.AsyncClient = Depends(get_db)
):
    """Creates a new chatbot session for the patient."""
    uid = current_user.get("uid")
    try:
        session = await create_chat_session(uid, req.title, db)
        await log_audit_event(actor=uid, action="CHAT_SESSION_CREATE", target=session.id, request=request)
        return session
    except Exception as e:
        await log_audit_event(
            actor=uid,
            action="CHAT_SESSION_CREATE",
            target="",
            status="failed",
            details={"error": str(e)},
            request=request
        )
        raise HTTPException(status_code=500, detail=str(e))

@router.get("/sessions", response_model=List[ChatSessionResponse])
async def list_sessions(
    request: Request,
    current_user: dict = Depends(patient_gate),
    db: firestore.AsyncClient = Depends(get_db)
):
    """Lists all active and past chatbot sessions for the patient."""
    uid = current_user.get("uid")
    try:
        sessions = await get_patient_chat_sessions(uid, db)
        await log_audit_event(actor=uid, action="CHAT_SESSION_LIST", target=uid, request=request)
        return sessions
    except Exception as e:
        await log_audit_event(
            actor=uid,
            action="CHAT_SESSION_LIST",
            target=uid,
            status="failed",
            details={"error": str(e)},
            request=request
        )
        raise HTTPException(status_code=500, detail=str(e))

@router.get("/sessions/{session_id}", response_model=ChatSessionDetailResponse)
async def get_session_history(
    session_id: str,
    request: Request,
    current_user: dict = Depends(patient_gate),
    db: firestore.AsyncClient = Depends(get_db)
):
    """Retrieves conversation history details for a specific session."""
    uid = current_user.get("uid")
    try:
        session_details = await get_chat_session_details(session_id, uid, db)
        await log_audit_event(actor=uid, action="CHAT_SESSION_DETAILS", target=session_id, request=request)
        return session_details
    except ValueError as e:
        await log_audit_event(
            actor=uid,
            action="CHAT_SESSION_DETAILS",
            target=session_id,
            status="failed",
            details={"error": str(e)},
            request=request
        )
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        await log_audit_event(
            actor=uid,
            action="CHAT_SESSION_DETAILS",
            target=session_id,
            status="failed",
            details={"error": str(e)},
            request=request
        )
        raise HTTPException(status_code=500, detail=str(e))

@router.post("/sessions/{session_id}/ask", response_model=ChatResponse)
async def ask_in_session(
    session_id: str,
    req: ChatRequest,
    request: Request,
    current_user: dict = Depends(patient_gate),
    db: firestore.AsyncClient = Depends(get_db)
):
    """Queries the chatbot inside an active session, grounding with history and reports."""
    uid = current_user.get("uid")
    try:
        response = await ask_inside_session(session_id, uid, req.prompt, db)
        await log_audit_event(actor=uid, action="CHAT_SESSION_ASK", target=session_id, request=request)
        return response
    except ValueError as e:
        await log_audit_event(
            actor=uid,
            action="CHAT_SESSION_ASK",
            target=session_id,
            status="failed",
            details={"error": str(e)},
            request=request
        )
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        await log_audit_event(
            actor=uid,
            action="CHAT_SESSION_ASK",
            target=session_id,
            status="failed",
            details={"error": str(e)},
            request=request
        )
        raise HTTPException(status_code=500, detail=str(e))

@router.websocket("/sessions/{session_id}/ws/voice")
async def chatbot_voice_websocket(
    websocket: WebSocket,
    session_id: str,
    token: str = Query(..., description="Firebase Auth ID Token for authentication"),
    db: firestore.AsyncClient = Depends(get_db)
):
    """
    Real-time WebSocket voice chat. Transcribes client speech, queries RAG using gemini-2.5-flash,
    synthesizes response using ElevenLabs, and streams response text/audio bytes back.
    """
    # 1. Authenticate over query token
    try:
        decoded_token = auth.verify_id_token(token)
        uid = decoded_token.get("uid")
        role = decoded_token.get("role")
        if not role:
            # Fallback firestore check
            user_doc = await db.collection(settings.USERS_COLLECTION).document(uid).get()
            if user_doc.exists:
                role = user_doc.to_dict().get("role")
        if role != "patient":
            raise ValueError("Unauthorized role")
    except Exception as e:
        logger.warning(f"WebSocket auth failed: {e}")
        await websocket.close(code=status.WS_1008_POLICY_VIOLATION)
        return

    # Verify session ownership
    try:
        session_doc = await db.collection(settings.CHAT_SESSIONS_COLLECTION).document(session_id).get()
        if not session_doc.exists or session_doc.to_dict().get("patient_id") != uid:
            await websocket.close(code=status.WS_1008_POLICY_VIOLATION)
            return
    except Exception as e:
        logger.error(f"WebSocket session check failed: {e}")
        await websocket.close(code=status.WS_1011_INTERNAL_ERROR)
        return

    await websocket.accept()
    logger.info(f"WebSocket voice connection accepted for user {uid}, session {session_id}")

    try:
        while True:
            # Wait for user input (audio bytes or JSON text)
            message = await websocket.receive()
            
            # Check message format
            if "bytes" in message and message["bytes"]:
                audio_bytes = message["bytes"]
                
                # 1. Temporarily save audio to GCS for transcription wrapper compatibility
                temp_filename = f"patients/{uid}/temp_voice_{uuid.uuid4()}.wav"
                try:
                    gcs_uri = upload_bytes_to_gcs(temp_filename, audio_bytes, content_type="audio/wav")
                    
                    # 2. Transcribe voice audio
                    transcription_data = await transcribe_audio(gcs_uri)
                    prompt_text = transcription_data.get("full_text", "").strip()
                    
                    # Clean up temporary GCS file
                    try:
                        from common_code.gcp_clients import _get_storage
                        bucket = _get_storage().bucket(settings.STORAGE_BUCKET_NAME)
                        bucket.blob(temp_filename).delete()
                    except Exception:
                        pass
                except Exception as ex:
                    logger.error(f"Transcription error: {ex}")
                    await websocket.send_json({"event": "error", "message": "Failed to process audio transcription"})
                    continue
                
                if not prompt_text:
                    await websocket.send_json({"event": "silence", "message": "No voice detected. Please speak clearly."})
                    continue
                
                # 3. Query RAG inside session using gemini-2.5-flash
                try:
                    chat_resp = await ask_inside_session(
                        session_id=session_id,
                        patient_id=uid,
                        prompt=prompt_text,
                        db=db,
                        model="gemini-2.5-flash"
                    )
                    reply_text = chat_resp.reply
                    sources = chat_resp.sources
                except Exception as ex:
                    logger.error(f"RAG query error: {ex}")
                    await websocket.send_json({"event": "error", "message": "Failed to search medical documents"})
                    continue
                
                # 4. Synthesize AI reply to Speech
                try:
                    voice_bytes = synthesize_speech(reply_text)
                except Exception as ex:
                    logger.error(f"Speech synthesis error: {ex}")
                    voice_bytes = b""
                
                # 5. Send results back to user
                await websocket.send_json({
                    "event": "response",
                    "user_text": prompt_text,
                    "ai_text": reply_text,
                    "sources": sources
                })
                if voice_bytes:
                    await websocket.send_bytes(voice_bytes)
                    
            elif "text" in message and message["text"]:
                try:
                    import json
                    text_data = json.loads(message["text"])
                    prompt_text = text_data.get("prompt", "").strip()
                except Exception:
                    prompt_text = message["text"].strip()
                
                if not prompt_text:
                    continue
                    
                try:
                    chat_resp = await ask_inside_session(
                        session_id=session_id,
                        patient_id=uid,
                        prompt=prompt_text,
                        db=db,
                        model="gemini-2.5-flash"
                    )
                    reply_text = chat_resp.reply
                    sources = chat_resp.sources
                except Exception as ex:
                    await websocket.send_json({"event": "error", "message": str(ex)})
                    continue
                
                try:
                    voice_bytes = synthesize_speech(reply_text)
                except Exception:
                    voice_bytes = b""
                    
                await websocket.send_json({
                    "event": "response",
                    "user_text": prompt_text,
                    "ai_text": reply_text,
                    "sources": sources
                })
                if voice_bytes:
                    await websocket.send_bytes(voice_bytes)
                    
    except WebSocketDisconnect:
        logger.info(f"WebSocket voice connection disconnected for user {uid}")
    except Exception as e:
        logger.error(f"WebSocket loop error: {e}")

