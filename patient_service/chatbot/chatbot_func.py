import datetime
import json
import uuid
from typing import AsyncGenerator, Optional
from google.cloud import firestore

from common_code.config import settings
from common_code.gcp_clients import async_generate_embeddings, async_generate_gemini_content, stream_gemini_content
from patient_service.chatbot.chatbot_model import (
    ChatCitation,
    ChatMessage,
    ChatResponse,
    ChatSessionDetailResponse,
    ChatSessionResponse,
    MessageRole,
)

_RAG_TOP_K         = 3
_RAG_SIM_THRESHOLD = 0.35   # below this the doc is not relevant enough to include
_HISTORY_WINDOW    = 10      # last N messages sent to Gemini as conversation context

_SYSTEM_PROMPT = (
    "You are an empathetic, professional AI Health Companion. "
    "Answer the patient's health or medical history questions using the context below. "
    "Follow these rules strictly:\n"
    "1. GROUNDING: Base answers on the provided report context when available. "
    "If no relevant context exists, say so honestly.\n"
    "2. NON-DIAGNOSTIC: Never make a definitive diagnosis. Use phrasing like 'may indicate', 'suggests'.\n"
    "3. SAFETY: Always remind the patient to consult their doctor for any medical decisions.\n"
    "4. CLARITY: Use plain language — avoid unexplained medical jargon.\n"
)


# ══════════════════════════════════════════════════════════════
#  Shared helpers
# ══════════════════════════════════════════════════════════════

def _cosine_similarity(v1: list[float], v2: list[float]) -> float:
    if len(v1) != len(v2) or not v1:
        return 0.0
    dot   = sum(a * b for a, b in zip(v1, v2))
    norm1 = sum(a * a for a in v1) ** 0.5
    norm2 = sum(b * b for b in v2) ** 0.5
    if norm1 == 0.0 or norm2 == 0.0:
        return 0.0
    return dot / (norm1 * norm2)


async def _rag_context(uid: str, prompt: str, db: firestore.AsyncClient) -> tuple[str, list[ChatCitation]]:
    """
    Tenant-isolated RAG retrieval.
    Returns (context_str, citations) where each citation carries id, title, and document type.
    Only fetches completed documents that have embeddings and exceed the similarity threshold.
    """
    docs_snap = await (
        db.collection(settings.DOCUMENTS_COLLECTION)
        .where("patientId", "==", uid)
        .where("status", "==", "completed")
        .get()
    )

    embedded_docs = [
        {
            "doc_id":    doc.id,
            "title":     d.get("title") or d.get("fileRef", "").split("/")[-1],
            "filename":  d.get("fileRef", "").split("/")[-1] or None,
            "doc_type":  d.get("type"),
            "summary":   d.get("summary", ""),
            "raw_text":  d.get("raw_text", ""),
            "embedding": d["embedding"],
        }
        for doc in docs_snap
        if (d := doc.to_dict()) and d.get("embedding")
    ]

    if not embedded_docs:
        return "No medical records found for this patient.", []

    prompt_vector = await async_generate_embeddings(prompt)

    ranked = sorted(
        ((_cosine_similarity(prompt_vector, doc["embedding"]), doc) for doc in embedded_docs),
        key=lambda x: x[0],
        reverse=True,
    )

    context_parts: list[str]         = []
    citations:     list[ChatCitation] = []
    for sim, doc in ranked[:_RAG_TOP_K]:
        if sim < _RAG_SIM_THRESHOLD:
            break
        context_parts.append(
            f"Document: {doc['title']}\n"
            f"Summary: {doc['summary']}\n"
            f"Report text: {doc['raw_text'][:1500]}\n"
        )
        citations.append(ChatCitation(id=doc["doc_id"], title=doc["title"], filename=doc.get("filename"), type=doc["doc_type"]))

    context_str = "\n---\n".join(context_parts) if context_parts else "No relevant records found for this question."
    return context_str, citations


def _get_session(d: dict, patient_id: str, session_id: str) -> None:
    """Raises ValueError (404) or PermissionError (403) if the session is invalid."""
    if not d:
        raise ValueError(f"Session {session_id} not found.")
    if d.get("patient_id") != patient_id:
        raise PermissionError("Access to this session is unauthorized.")


def _parse_citations(raw: list) -> list[ChatCitation]:
    out = []
    for s in raw or []:
        if isinstance(s, dict) and s.get("id"):
            out.append(ChatCitation(id=s["id"], title=s.get("title", ""), filename=s.get("filename"), type=s.get("type")))
        elif isinstance(s, str):
            # back-compat: old sessions stored plain strings
            out.append(ChatCitation(id="", title=s, type=None))
    return out


def _parse_messages(raw: list[dict]) -> list[ChatMessage]:
    messages = []
    for msg in raw:
        ts = msg.get("created_at")
        if isinstance(ts, str):
            try:
                ts = datetime.datetime.fromisoformat(ts.replace("Z", "+00:00"))
            except Exception:
                ts = datetime.datetime.now(datetime.UTC)
        elif not isinstance(ts, datetime.datetime):
            ts = datetime.datetime.now(datetime.UTC)
        messages.append(ChatMessage(
            role=MessageRole(msg.get("role", "model")),
            content=msg.get("content", ""),
            created_at=ts,
            sources=_parse_citations(msg.get("sources", [])),
        ))
    return messages


# ══════════════════════════════════════════════════════════════
#  Stateless single-turn query (no session)
# ══════════════════════════════════════════════════════════════

async def answer_patient_query(uid: str, prompt: str, db: firestore.AsyncClient) -> ChatResponse:
    """Single-turn RAG query. Does not persist to any session."""
    context_str, sources = await _rag_context(uid, prompt, db)

    reply = await async_generate_gemini_content(
        f"{_SYSTEM_PROMPT}\n"
        f"--- Patient Medical Context ---\n{context_str}\n\n"
        f"--- Patient Question ---\n{prompt}\n\nResponse:",
        json_response=False,
    )
    return ChatResponse(reply=reply, sources=sources)


# ══════════════════════════════════════════════════════════════
#  Session management
# ══════════════════════════════════════════════════════════════

async def _generate_session_title(prompt: str) -> str:
    """Asks Gemini for a 4-6 word session title based on the opening message."""
    try:
        raw = await async_generate_gemini_content(
            "Summarize the following health question as a short chat session title — "
            "4 to 6 words maximum, no punctuation at the end, no quotes.\n\n"
            f"Question: {prompt}",
            json_response=False,
        )
        return raw.strip().strip('"').strip("'")[:80]
    except Exception:
        return prompt[:60].strip()


async def create_chat_session(
    patient_id: str,
    title: Optional[str],
    db: firestore.AsyncClient,
) -> ChatSessionResponse:
    session_id = str(uuid.uuid4())
    now        = datetime.datetime.now(datetime.UTC)

    await db.collection(settings.CHAT_SESSIONS_COLLECTION).document(session_id).set({
        "id":         session_id,
        "patient_id": patient_id,
        "title":      title or None,   # None until first message generates it
        "messages":   [],
        "created_at": now,
        "updated_at": now,
    })

    return ChatSessionResponse(
        id=session_id,
        patient_id=patient_id,
        title=title or None,
        message_count=0,
        created_at=now,
        updated_at=now,
    )


async def get_patient_chat_sessions(
    patient_id: str,
    db: firestore.AsyncClient,
) -> list[ChatSessionResponse]:
    docs = await (
        db.collection(settings.CHAT_SESSIONS_COLLECTION)
        .where("patient_id", "==", patient_id)
        .order_by("updated_at", direction=firestore.Query.DESCENDING)
        .get()
    )
    return [
        ChatSessionResponse(
            id=d["id"],
            patient_id=d["patient_id"],
            title=d["title"],
            message_count=len(d.get("messages", [])),
            created_at=d["created_at"],
            updated_at=d["updated_at"],
        )
        for doc in docs
        if (d := doc.to_dict())
    ]


async def get_chat_session_details(
    session_id: str,
    patient_id: str,
    db: firestore.AsyncClient,
) -> ChatSessionDetailResponse:
    snap = await db.collection(settings.CHAT_SESSIONS_COLLECTION).document(session_id).get()
    if not snap.exists:
        raise ValueError(f"Session {session_id} not found.")
    d = snap.to_dict()
    _get_session(d, patient_id, session_id)

    return ChatSessionDetailResponse(
        id=d["id"],
        patient_id=d["patient_id"],
        title=d["title"],
        created_at=d["created_at"],
        updated_at=d["updated_at"],
        messages=_parse_messages(d.get("messages", [])),
    )


async def delete_chat_session(
    session_id: str,
    patient_id: str,
    db: firestore.AsyncClient,
) -> None:
    snap = await db.collection(settings.CHAT_SESSIONS_COLLECTION).document(session_id).get()
    if not snap.exists:
        raise ValueError(f"Session {session_id} not found.")
    _get_session(snap.to_dict(), patient_id, session_id)
    await db.collection(settings.CHAT_SESSIONS_COLLECTION).document(session_id).delete()


# ══════════════════════════════════════════════════════════════
#  Session-aware multi-turn query
# ══════════════════════════════════════════════════════════════

async def ask_inside_session(
    session_id: str,
    patient_id: str,
    prompt: str,
    db: firestore.AsyncClient,
    model: Optional[str] = None,
) -> ChatResponse:
    """Multi-turn RAG query inside a session. Persists both turns to the session history."""
    doc_ref  = db.collection(settings.CHAT_SESSIONS_COLLECTION).document(session_id)
    snap     = await doc_ref.get()
    if not snap.exists:
        raise ValueError(f"Session {session_id} not found.")
    d = snap.to_dict()
    _get_session(d, patient_id, session_id)

    messages_raw = d.get("messages", [])

    # 1. RAG retrieval
    context_str, sources = await _rag_context(patient_id, prompt, db)

    # 2. Conversation history (last N messages as context)
    history_lines = [
        f"{'Patient' if m['role'] == 'user' else 'AI Companion'}: {m['content']}"
        for m in messages_raw[-_HISTORY_WINDOW:]
    ]
    history_str = "\n".join(history_lines) if history_lines else "No previous conversation."

    # 3. Grounded Gemini call
    reply = await async_generate_gemini_content(
        f"{_SYSTEM_PROMPT}\n"
        f"--- Patient Medical Context ---\n{context_str}\n\n"
        f"--- Conversation History ---\n{history_str}\n\n"
        f"--- Patient Question ---\n{prompt}\n\nResponse:",
        json_response=False,
        model=model,
    )

    # 4. Persist both turns (sources belong only to the model reply)
    now = datetime.datetime.now(datetime.UTC)
    messages_raw.append({"role": "user",  "content": prompt, "created_at": now, "sources": []})
    messages_raw.append({"role": "model", "content": reply,  "created_at": now,
                         "sources": [c.model_dump() for c in sources]})

    update: dict = {"messages": messages_raw, "updated_at": now}
    if len(messages_raw) == 2 and not d.get("title"):  # first exchange, no title yet
        update["title"] = await _generate_session_title(prompt)
    await doc_ref.update(update)

    return ChatResponse(reply=reply, sources=sources)


# ══════════════════════════════════════════════════════════════
#  SSE streaming variants
# ══════════════════════════════════════════════════════════════

def _sse(event_type: str, **payload) -> str:
    """Formats a single SSE data line."""
    return f"data: {json.dumps({'type': event_type, **payload})}\n\n"


async def stream_patient_query(
    uid: str,
    prompt: str,
    db: firestore.AsyncClient,
) -> AsyncGenerator[str, None]:
    """
    SSE generator for stateless single-turn queries.
    Emits: sources → chunk* → done  (or error on failure).
    """
    try:
        context_str, sources = await _rag_context(uid, prompt, db)
    except Exception as e:
        yield _sse("error", message=f"Failed to retrieve medical context: {e}")
        return

    yield _sse("sources", sources=sources)

    gemini_prompt = (
        f"{_SYSTEM_PROMPT}\n"
        f"--- Patient Medical Context ---\n{context_str}\n\n"
        f"--- Patient Question ---\n{prompt}\n\nResponse:"
    )
    try:
        async for chunk in stream_gemini_content(gemini_prompt):
            yield _sse("chunk", content=chunk)
    except Exception as e:
        yield _sse("error", message=f"Streaming interrupted: {e}")
        return

    yield _sse("done")


async def stream_session_ask(
    session_id: str,
    patient_id: str,
    prompt: str,
    db: firestore.AsyncClient,
    model: Optional[str] = None,
) -> AsyncGenerator[str, None]:
    """
    SSE generator for session-aware multi-turn queries.
    Emits: sources → chunk* → done  (or error on failure).
    Persists both turns to Firestore after the stream completes.
    """
    doc_ref = db.collection(settings.CHAT_SESSIONS_COLLECTION).document(session_id)
    snap    = await doc_ref.get()
    if not snap.exists:
        yield _sse("error", message=f"Session {session_id} not found.")
        return
    d = snap.to_dict()
    try:
        _get_session(d, patient_id, session_id)
    except PermissionError as e:
        yield _sse("error", message=str(e))
        return

    messages_raw = d.get("messages", [])

    try:
        context_str, sources = await _rag_context(patient_id, prompt, db)
    except Exception as e:
        yield _sse("error", message=f"Failed to retrieve medical context: {e}")
        return

    yield _sse("sources", sources=sources)

    history_lines = [
        f"{'Patient' if m['role'] == 'user' else 'AI Companion'}: {m['content']}"
        for m in messages_raw[-_HISTORY_WINDOW:]
    ]
    history_str = "\n".join(history_lines) if history_lines else "No previous conversation."

    gemini_prompt = (
        f"{_SYSTEM_PROMPT}\n"
        f"--- Patient Medical Context ---\n{context_str}\n\n"
        f"--- Conversation History ---\n{history_str}\n\n"
        f"--- Patient Question ---\n{prompt}\n\nResponse:"
    )

    reply_chunks: list[str] = []
    try:
        async for chunk in stream_gemini_content(gemini_prompt, model=model):
            reply_chunks.append(chunk)
            yield _sse("chunk", content=chunk)
    except Exception as e:
        yield _sse("error", message=f"Streaming interrupted: {e}")
        return

    yield _sse("done")

    # Persist after stream so Firestore write never blocks the client
    full_reply = "".join(reply_chunks)
    now = datetime.datetime.now(datetime.UTC)
    messages_raw.append({"role": "user",  "content": prompt,     "created_at": now, "sources": []})
    messages_raw.append({"role": "model", "content": full_reply, "created_at": now,
                         "sources": [c.model_dump() for c in sources]})

    update: dict = {"messages": messages_raw, "updated_at": now}
    if len(messages_raw) == 2 and not d.get("title"):  # first exchange, no title yet
        update["title"] = await _generate_session_title(prompt)
    await doc_ref.update(update)
