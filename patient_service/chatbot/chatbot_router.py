import asyncio
import json
import logging
import uuid

from fastapi import APIRouter, Depends, HTTPException, Query, Request, WebSocket, WebSocketDisconnect, status
from fastapi.responses import StreamingResponse
from firebase_admin import auth
from google.cloud import firestore
from typing import List

from common_code.config import settings
from common_code.firestore import get_db, log_audit_event
from common_code.firebase_auth import require_role
from common_code.gcp_clients import synthesize_speech, transcribe_audio_bytes
from patient_service.chatbot.chatbot_model import (
    ChatRequest,
    ChatResponse,
    ChatSessionCreateRequest,
    ChatSessionDetailResponse,
    ChatSessionResponse,
    DeleteSessionResponse,
)
from patient_service.chatbot.chatbot_func import (
    answer_patient_query,
    ask_inside_session,
    create_chat_session,
    delete_chat_session,
    get_chat_session_details,
    get_patient_chat_sessions,
    stream_patient_query,
    stream_session_ask,
)

logger      = logging.getLogger(__name__)
router      = APIRouter()
patient_gate = require_role(["patient"])


# ══════════════════════════════════════════════════════════════
#  Single-turn (stateless)
# ══════════════════════════════════════════════════════════════

@router.post("/ask", response_model=ChatResponse)
async def ask_companion(
    req: ChatRequest,
    request: Request,
    current_user: dict = Depends(patient_gate),
    db: firestore.AsyncClient = Depends(get_db),
):
    """Answers a health question using tenant-isolated RAG. Does not persist to any session."""
    uid = current_user["uid"]
    try:
        response = await answer_patient_query(uid, req.prompt, db)
        await log_audit_event(actor=uid, action="CHAT_ASK", target=uid, request=request)
        return response
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/ask/stream")
async def ask_companion_stream(
    req: ChatRequest,
    current_user: dict = Depends(patient_gate),
    db: firestore.AsyncClient = Depends(get_db),
):
    """
    Streaming SSE variant of /ask.
    Returns `text/event-stream` — clients receive events as Gemini generates the response.

    Event sequence:
    - `{"type": "sources", "sources": [...]}` — document filenames used for grounding
    - `{"type": "chunk",   "content": "..."}` — incremental Markdown text
    - `{"type": "done"}` — stream complete
    - `{"type": "error",  "message": "..."}` — on failure (stream may already be open)
    """
    uid = current_user["uid"]
    return StreamingResponse(
        stream_patient_query(uid, req.prompt, db),
        media_type="text/event-stream",
        headers={
            "Cache-Control":     "no-cache",
            "X-Accel-Buffering": "no",   # disable Nginx buffering
        },
    )


# ══════════════════════════════════════════════════════════════
#  Session management
# ══════════════════════════════════════════════════════════════

@router.post("/sessions", response_model=ChatSessionResponse, status_code=status.HTTP_201_CREATED)
async def create_session(
    req: ChatSessionCreateRequest,
    request: Request,
    current_user: dict = Depends(patient_gate),
    db: firestore.AsyncClient = Depends(get_db),
):
    """Creates a new chat session."""
    uid = current_user["uid"]
    session = await create_chat_session(uid, req.title, db)
    await log_audit_event(actor=uid, action="CHAT_SESSION_CREATE", target=session.id, request=request)
    return session


@router.get("/sessions", response_model=List[ChatSessionResponse])
async def list_sessions(
    request: Request,
    current_user: dict = Depends(patient_gate),
    db: firestore.AsyncClient = Depends(get_db),
):
    """Lists all sessions for the patient, newest first."""
    uid = current_user["uid"]
    return await get_patient_chat_sessions(uid, db)


@router.get("/sessions/{session_id}", response_model=ChatSessionDetailResponse)
async def get_session_history(
    session_id: str,
    request: Request,
    current_user: dict = Depends(patient_gate),
    db: firestore.AsyncClient = Depends(get_db),
):
    """Retrieves full conversation history for a session."""
    uid = current_user["uid"]
    try:
        details = await get_chat_session_details(session_id, uid, db)
        await log_audit_event(actor=uid, action="CHAT_SESSION_VIEW", target=session_id, request=request)
        return details
    except PermissionError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))


@router.delete("/sessions/{session_id}", response_model=DeleteSessionResponse)
async def delete_session(
    session_id: str,
    request: Request,
    current_user: dict = Depends(patient_gate),
    db: firestore.AsyncClient = Depends(get_db),
):
    """Deletes a session and its full history."""
    uid = current_user["uid"]
    try:
        await delete_chat_session(session_id, uid, db)
        await log_audit_event(actor=uid, action="CHAT_SESSION_DELETE", target=session_id, request=request)
        return DeleteSessionResponse(id=session_id, message="Session deleted successfully.")
    except PermissionError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))


@router.post("/sessions/{session_id}/ask", response_model=ChatResponse)
async def ask_in_session(
    session_id: str,
    req: ChatRequest,
    request: Request,
    current_user: dict = Depends(patient_gate),
    db: firestore.AsyncClient = Depends(get_db),
):
    """Multi-turn query within a session, grounded by conversation history and medical records."""
    uid = current_user["uid"]
    try:
        response = await ask_inside_session(session_id, uid, req.prompt, db)
        await log_audit_event(actor=uid, action="CHAT_SESSION_ASK", target=session_id, request=request)
        return response
    except PermissionError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/sessions/{session_id}/ask/stream")
async def ask_in_session_stream(
    session_id: str,
    req: ChatRequest,
    current_user: dict = Depends(patient_gate),
    db: firestore.AsyncClient = Depends(get_db),
):
    """
    Streaming SSE variant of /sessions/{id}/ask.
    Conversation history and RAG grounding apply identically to the non-streaming endpoint.
    Both turns are persisted to Firestore **after** the stream completes.

    Event sequence:
    - `{"type": "sources", "sources": [...]}` — document filenames used for grounding
    - `{"type": "chunk",   "content": "..."}` — incremental Markdown text
    - `{"type": "done"}` — stream complete, message saved to session
    - `{"type": "error",  "message": "..."}` — on failure
    """
    uid = current_user["uid"]
    return StreamingResponse(
        stream_session_ask(session_id, uid, req.prompt, db),
        media_type="text/event-stream",
        headers={
            "Cache-Control":     "no-cache",
            "X-Accel-Buffering": "no",
        },
    )


# ══════════════════════════════════════════════════════════════
#  Voice WebSocket
# ══════════════════════════════════════════════════════════════

@router.websocket("/sessions/{session_id}/ws/voice")
async def chatbot_voice_websocket(
    websocket: WebSocket,
    session_id: str,
    token: str = Query(..., description="Firebase Auth ID token"),
    db: firestore.AsyncClient = Depends(get_db),
):
    """
    Real-time voice chat over WebSocket.
    Client sends audio bytes (WAV) or JSON `{"prompt": "..."}` for text.
    Server replies with JSON event + MP3 audio bytes.
    """
    # ── Authenticate ──────────────────────────────────────────
    try:
        decoded  = auth.verify_id_token(token)
        uid      = decoded["uid"]
        role     = decoded.get("role")
        if not role:
            snap = await db.collection(settings.USERS_COLLECTION).document(uid).get()
            role = snap.to_dict().get("role") if snap.exists else None
        if role != "patient":
            raise ValueError("Unauthorized role")
    except Exception as e:
        logger.warning(f"WebSocket auth failed: {e}")
        await websocket.close(code=status.WS_1008_POLICY_VIOLATION)
        return

    # ── Verify session ownership ───────────────────────────────
    try:
        snap = await db.collection(settings.CHAT_SESSIONS_COLLECTION).document(session_id).get()
        if not snap.exists or snap.to_dict().get("patient_id") != uid:
            await websocket.close(code=status.WS_1008_POLICY_VIOLATION)
            return
    except Exception as e:
        logger.error(f"WebSocket session check failed: {e}")
        await websocket.close(code=status.WS_1011_INTERNAL_ERROR)
        return

    await websocket.accept()
    logger.info(f"WS voice connected uid={uid} session={session_id}")

    try:
        while True:
            message = await websocket.receive()

            # ── Audio bytes ────────────────────────────────────
            if message.get("bytes"):
                audio_bytes = message["bytes"]
                if len(audio_bytes) < 100:
                    # Too short to be real speech — ignore
                    await websocket.send_json({"event": "silence", "message": "Audio too short."})
                    continue

                filename = f"{uuid.uuid4()}.wav"
                try:
                    transcription = await transcribe_audio_bytes(audio_bytes, filename=filename)
                    prompt_text   = transcription.get("full_text", "").strip()
                except Exception as ex:
                    logger.error(f"WS transcription error: {ex}")
                    await websocket.send_json({"event": "error", "message": "Audio transcription failed."})
                    continue

                if not prompt_text:
                    await websocket.send_json({"event": "silence", "message": "No speech detected."})
                    continue

                # Send transcription immediately so the FE can show it without waiting
                await websocket.send_json({"event": "transcribed", "user_text": prompt_text})

            # ── Text frame ────────────────────────────────────
            elif message.get("text"):
                try:
                    prompt_text = json.loads(message["text"]).get("prompt", "").strip()
                except Exception:
                    prompt_text = message["text"].strip()
                if not prompt_text:
                    continue

            else:
                continue

            # ── RAG + Gemini ───────────────────────────────────
            try:
                chat_resp  = await ask_inside_session(session_id, uid, prompt_text, db)
                reply_text = chat_resp.reply
                sources    = [s.model_dump() for s in chat_resp.sources]
            except Exception as ex:
                logger.error(f"WS RAG error: {ex}", exc_info=True)
                await websocket.send_json({"event": "error", "message": "Failed to generate a response."})
                continue

            # ── TTS (async, non-blocking) ────────────────────────
            try:
                voice_bytes = await asyncio.to_thread(synthesize_speech, reply_text, "en-IN")
            except Exception as ex:
                logger.warning(f"WS TTS error: {ex}")
                voice_bytes = b""

            await websocket.send_json({
                "event":     "response",
                "user_text": prompt_text,
                "ai_text":   reply_text,
                "sources":   sources,
            })
            if voice_bytes:
                await websocket.send_bytes(voice_bytes)

    except WebSocketDisconnect:
        logger.info(f"WS voice disconnected uid={uid} session={session_id}")
    except Exception as e:
        logger.error(f"WS loop error uid={uid}: {e}")
