import asyncio
import json
import logging
import uuid
from typing import List

from fastapi import (
    APIRouter,
    Depends,
    HTTPException,
    Query,
    Request,
    WebSocket,
    WebSocketDisconnect,
    status,
)
from fastapi.responses import StreamingResponse
from firebase_admin import auth
from google.cloud import firestore

from common_code.config import settings
from common_code.firebase_auth import require_role
from common_code.firestore import get_db, log_audit_event
from common_code.gcp_clients import synthesize_speech, transcribe_audio_bytes
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
from patient_service.chatbot.chatbot_model import (
    ChatRequest,
    ChatResponse,
    ChatSessionCreateRequest,
    ChatSessionDetailResponse,
    ChatSessionResponse,
    DeleteSessionResponse,
)

logger = logging.getLogger(__name__)
router = APIRouter()
patient_gate = require_role(["patient"])


# ══════════════════════════════════════════════════════════════
#  Single-Turn (Stateless)
# ══════════════════════════════════════════════════════════════

@router.post(
    "/ask",
    response_model=ChatResponse,
    summary="Ask AI Companion (Stateless)",
    description="""
Answers health questions, logs vital signs, or manages reminders in a **single stateless turn** without saving to a chat session.

### Capabilities:
- **Medical Question Answering**: Grounded in the patient's uploaded medical records and lab reports via vector search.
- **Autonomous Tool Execution**: Automatically executes actions such as logging vitals (`log_vitals`), retrieving recent vitals (`get_latest_vitals`), listing documents (`list_documents`), or creating reminders (`create_reminder`).
- **Clarification**: If an action is requested but key details are missing, returns a helpful clarifying question.
""",
    response_description="AI Companion response with Markdown text and document citations.",
    responses={
        200: {"description": "Query answered successfully."},
        400: {"description": "Invalid prompt or query format."},
        401: {"description": "Missing or invalid Firebase authentication token."},
        500: {"description": "Internal server or Gemini generation error."},
    },
)
async def ask_companion(
    req: ChatRequest,
    request: Request,
    current_user: dict = Depends(patient_gate),
    db: firestore.AsyncClient = Depends(get_db),
):
    uid = current_user["uid"]
    try:
        response = await answer_patient_query(uid, req.prompt, db)
        await log_audit_event(actor=uid, action="CHAT_ASK", target=uid, request=request)
        return response
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post(
    "/ask/stream",
    summary="Stream AI Companion Reply (Stateless SSE)",
    description="""
Server-Sent Events (SSE) streaming variant of `/ask`. 

Returns a `text/event-stream` stream providing real-time visibility into AI thought processes, tool calls, grounding citations, and token generation.

### SSE Event Lifecycle:
1. `{"type": "thinking", "status": "Understanding your question..."}` — Real-time progress updates.
2. `{"type": "tool_call", "tool": "log_vitals", "args": {...}}` — Emitted when an action is triggered.
3. `{"type": "tool_result", "tool": "log_vitals", "output": "..."}` — Result of the backend tool execution.
4. `{"type": "sources", "sources": [...]}` — Document citations used for grounding.
5. `{"type": "chunk", "content": "..."}` — Incremental Markdown tokens from Gemini.
6. `{"type": "done"}` — Stream completed.
7. `{"type": "error", "message": "..."}` — Emitted if an error occurs mid-stream.
""",
    response_description="Server-Sent Events stream.",
    responses={
        200: {
            "content": {"text/event-stream": {}},
            "description": "Continuous stream of Server-Sent Events.",
        },
        401: {"description": "Unauthorized."},
    },
)
async def ask_companion_stream(
    req: ChatRequest,
    current_user: dict = Depends(patient_gate),
    db: firestore.AsyncClient = Depends(get_db),
):
    uid = current_user["uid"]
    return StreamingResponse(
        stream_patient_query(uid, req.prompt, db),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )


# ══════════════════════════════════════════════════════════════
#  Session Management
# ══════════════════════════════════════════════════════════════

@router.post(
    "/sessions",
    response_model=ChatSessionResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Create Chat Session",
    description="""
Creates a new persistent conversation session for multi-turn dialogues with the AI Health Companion.

If `title` is omitted, the title will be automatically generated by Gemini from the patient's first message.
""",
    response_description="Created chat session metadata.",
    responses={
        201: {"description": "Chat session created successfully."},
        401: {"description": "Unauthorized."},
    },
)
async def create_session(
    req: ChatSessionCreateRequest,
    request: Request,
    current_user: dict = Depends(patient_gate),
    db: firestore.AsyncClient = Depends(get_db),
):
    uid = current_user["uid"]
    session = await create_chat_session(uid, req.title, db)
    await log_audit_event(actor=uid, action="CHAT_SESSION_CREATE", target=session.id, request=request)
    return session


@router.get(
    "/sessions",
    response_model=List[ChatSessionResponse],
    summary="List Chat Sessions",
    description="""
Returns all chat conversation sessions belonging to the authenticated patient, ordered by most recently active first (`updated_at` descending).
""",
    response_description="List of patient chat sessions.",
    responses={
        200: {"description": "List of sessions returned successfully."},
        401: {"description": "Unauthorized."},
    },
)
async def list_sessions(
    request: Request,
    current_user: dict = Depends(patient_gate),
    db: firestore.AsyncClient = Depends(get_db),
):
    uid = current_user["uid"]
    return await get_patient_chat_sessions(uid, db)


@router.get(
    "/sessions/{session_id}",
    response_model=ChatSessionDetailResponse,
    summary="Get Chat Session History",
    description="""
Retrieves the full message history and metadata for a specific conversation session.
Messages are ordered chronologically (oldest first).
""",
    response_description="Full conversation history with citations.",
    responses={
        200: {"description": "Session details and messages returned successfully."},
        401: {"description": "Unauthorized."},
        404: {"description": "Session not found or belongs to another patient."},
    },
)
async def get_session_history(
    session_id: str,
    request: Request,
    current_user: dict = Depends(patient_gate),
    db: firestore.AsyncClient = Depends(get_db),
):
    uid = current_user["uid"]
    try:
        details = await get_chat_session_details(session_id, uid, db)
        await log_audit_event(actor=uid, action="CHAT_SESSION_VIEW", target=session_id, request=request)
        return details
    except PermissionError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))


@router.delete(
    "/sessions/{session_id}",
    response_model=DeleteSessionResponse,
    summary="Delete Chat Session",
    description="""
Permanently deletes a chat session and all of its associated message history from Firestore.
Only the owning patient can delete their session.
""",
    response_description="Deletion confirmation.",
    responses={
        200: {"description": "Session deleted successfully."},
        401: {"description": "Unauthorized."},
        404: {"description": "Session not found or belongs to another patient."},
    },
)
async def delete_session(
    session_id: str,
    request: Request,
    current_user: dict = Depends(patient_gate),
    db: firestore.AsyncClient = Depends(get_db),
):
    uid = current_user["uid"]
    try:
        await delete_chat_session(session_id, uid, db)
        await log_audit_event(actor=uid, action="CHAT_SESSION_DELETE", target=session_id, request=request)
        return DeleteSessionResponse(id=session_id, message="Session deleted successfully.")
    except PermissionError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))


@router.post(
    "/sessions/{session_id}/ask",
    response_model=ChatResponse,
    summary="Ask in Chat Session (Multi-Turn)",
    description="""
Sends a message inside an existing chat session.

### Multi-Turn Context & Tool Calling:
- **Conversation Memory**: Remembers prior turns in the session (e.g. following up on previous vital readings or symptoms).
- **Vitals & Actions**: Patient can say *"Log weight 50kg"* or *"My BP is 120/80"* and the AI executes the action while retaining session history.
- **Clarification Handling**: If vital measurements are incomplete (e.g. *"Record my BP"*), the AI asks a clarifying question, and seamlessly processes the response on the next turn.
- **Automatic Storage**: Both user and model turns are appended to the session.
""",
    response_description="AI Companion response with Markdown text and citations.",
    responses={
        200: {"description": "Message processed and appended to session."},
        401: {"description": "Unauthorized."},
        404: {"description": "Session not found."},
        500: {"description": "Internal server error."},
    },
)
async def ask_in_session(
    session_id: str,
    req: ChatRequest,
    request: Request,
    current_user: dict = Depends(patient_gate),
    db: firestore.AsyncClient = Depends(get_db),
):
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


@router.post(
    "/sessions/{session_id}/ask/stream",
    summary="Stream in Chat Session (Multi-Turn SSE)",
    description="""
Real-time Server-Sent Events (SSE) streaming variant of `/sessions/{session_id}/ask`.

Streams incremental thoughts, tool executions, document citations, and response tokens. Both turns are automatically persisted to Firestore after stream completion.

### Event Sequence:
- `event: thinking` $\rightarrow$ `{"type": "thinking", "status": "Understanding your request..."}`
- `event: tool_call` $\rightarrow$ `{"type": "tool_call", "tool": "log_vitals", "args": {...}}`
- `event: tool_result` $\rightarrow$ `{"type": "tool_result", "tool": "log_vitals", "output": "..."}`
- `event: sources` $\rightarrow$ `{"type": "sources", "sources": [...]}`
- `event: chunk` $\rightarrow$ `{"type": "chunk", "content": "..."}`
- `event: done` $\rightarrow$ `{"type": "done"}`
""",
    response_description="Real-time Server-Sent Events stream.",
    responses={
        200: {
            "content": {"text/event-stream": {}},
            "description": "Continuous stream of Server-Sent Events.",
        },
        401: {"description": "Unauthorized."},
        404: {"description": "Session not found."},
    },
)
async def ask_in_session_stream(
    session_id: str,
    req: ChatRequest,
    current_user: dict = Depends(patient_gate),
    db: firestore.AsyncClient = Depends(get_db),
):
    uid = current_user["uid"]
    return StreamingResponse(
        stream_session_ask(session_id, uid, req.prompt, db),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
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
    token: str = Query(..., description="Firebase Auth ID token for patient authentication"),
    db: firestore.AsyncClient = Depends(get_db),
):
    """
    Real-Time Bidirectional Voice WebSocket.

    Enables conversational voice interactions with natural speech-to-text, tool execution, and text-to-speech.

    ### Protocol & Frame Flow:
    1. **Authentication**: Client connects with `?token=<firebase_id_token>`.
    2. **User Input**:
       - Binary frame: Raw Audio recording bytes (WAV/MP3 format, max 10MB).
       - Text frame: JSON `{"prompt": "User query text"}`.
    3. **Immediate Transcription**: Server returns JSON `{"event": "transcribed", "user_text": "..."}`.
    4. **Response Metadata**: Server returns JSON `{"event": "response", "user_text": "...", "ai_text": "...", "sources": [...]}`.
    5. **Audio Output**: Server transmits binary synthesized MP3 speech bytes immediately following metadata.
    """
    # ── Authenticate ──────────────────────────────────────────
    try:
        decoded = auth.verify_id_token(token)
        uid     = decoded["uid"]
        role    = decoded.get("role")
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
                    await websocket.send_json({"event": "silence", "message": "Audio too short."})
                    continue
                if len(audio_bytes) > 10 * 1024 * 1024:
                    await websocket.send_json({"event": "error", "message": "Audio payload too large (max 10MB)."})
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

                # Send transcription immediately so FE shows it without waiting
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

            # ── RAG + Tool Execution + Gemini ───────────────────
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
