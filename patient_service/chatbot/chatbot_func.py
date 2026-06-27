import datetime
import uuid
from google.cloud import firestore
from common_code.config import settings
from common_code.gcp_clients import generate_embeddings, generate_gemini_content
from patient_service.chatbot.chatbot_model import (
    ChatResponse,
    ChatSessionResponse,
    ChatSessionDetailResponse,
    ChatMessage
)

def cosine_similarity(v1: list[float], v2: list[float]) -> float:
    """Computes similarity index between two embedding vectors in pure Python."""
    if len(v1) != len(v2) or not v1 or not v2:
        return 0.0
    dot_product = sum(a * b for a, b in zip(v1, v2))
    norm_a = sum(a * a for a in v1) ** 0.5
    norm_b = sum(b * b for b in v2) ** 0.5
    if norm_a == 0.0 or norm_b == 0.0:
        return 0.0
    return dot_product / (norm_a * norm_b)

async def answer_patient_query(uid: str, prompt: str, db: firestore.AsyncClient) -> ChatResponse:
    """
    Executes a tenant-isolated RAG query. Matches patient question against their own
    indexed documents ONLY, construct grounds, and queries Gemini.
    """
    # 1. Fetch all documents belonging specifically to this patient (Tenant Isolation)
    docs_snap = await db.collection(settings.DOCUMENTS_COLLECTION) \
        .where("patientId", "==", uid) \
        .get()
        
    documents = []
    for doc in docs_snap:
        d = doc.to_dict()
        if "embedding" in d and d["embedding"]:
            documents.append({
                "id": doc.id,
                "fileRef": d.get("fileRef", "Unknown Report"),
                "summary": d.get("summary", ""),
                "raw_text": d.get("raw_text", ""),
                "embedding": d["embedding"]
            })
            
    # 2. Generate embedding for patient prompt
    prompt_vector = generate_embeddings(prompt)
    
    # 3. Match relevant context using local vector similarity
    ranked_docs = []
    for doc in documents:
        sim = cosine_similarity(prompt_vector, doc["embedding"])
        ranked_docs.append((sim, doc))
        
    # Sort descending by similarity
    ranked_docs.sort(key=lambda x: x[0], reverse=True)
    
    # Select top 2 matching reports
    context_parts = []
    sources = []
    for sim, doc in ranked_docs[:2]:
        if sim > 0.1:  # small similarity threshold
            context_parts.append(
                f"Report Reference: {doc['fileRef']}\n"
                f"Summary: {doc['summary']}\n"
                f"Detailed details: {doc['raw_text'][:800]}\n"
            )
            sources.append(doc["fileRef"].split("/")[-1]) # return filename
            
    context_str = "\n---\n".join(context_parts) if context_parts else "No report records found matching this context."
    
    # 4. Construct Gemini prompt grounding it with the patient's data
    gemini_prompt = (
        "You are an empathetic, professional AI Health Companion. "
        "Your task is to answer the patient's questions about their health or reports. "
        "You MUST structure your responses under strict clinical safety rules:\n"
        "1. GROUNDING: Use the provided medical report context to answer if possible.\n"
        "2. NON-DIAGNOSTIC: You cannot make final medical diagnoses. Use safe phrasing like 'indicates', 'suggests'.\n"
        "3. DISCLAIMER: Always include a warm disclaimer reminding the patient to consult their doctor.\n"
        "4. LANGUAGE: Answer in clear, plain language.\n\n"
        f"--- Patient Medical Context ---\n{context_str}\n\n"
        f"--- Patient Question ---\n{prompt}\n\n"
        "Response:"
    )
    
    reply = generate_gemini_content(gemini_prompt, json_response=False)
    
    return ChatResponse(
        reply=reply,
        sources=sources
    )

async def create_chat_session(patient_id: str, title: str | None, db: firestore.AsyncClient) -> ChatSessionResponse:
    """Creates a new chatbot session document in Firestore."""
    session_id = str(uuid.uuid4())
    if not title:
        title = f"Conversation {datetime.datetime.now(datetime.UTC).strftime('%Y-%m-%d %H:%M')}"
        
    now = datetime.datetime.now(datetime.UTC)
    doc_data = {
        "id": session_id,
        "patient_id": patient_id,
        "title": title,
        "created_at": now,
        "updated_at": now,
        "messages": []
    }
    
    await db.collection(settings.CHAT_SESSIONS_COLLECTION).document(session_id).set(doc_data)
    
    return ChatSessionResponse(
        id=session_id,
        patient_id=patient_id,
        title=title,
        created_at=now,
        updated_at=now
    )

async def get_patient_chat_sessions(patient_id: str, db: firestore.AsyncClient) -> list[ChatSessionResponse]:
    """Retrieves all chat sessions for the patient, sorted by updated_at descending."""
    docs = await db.collection(settings.CHAT_SESSIONS_COLLECTION) \
        .where("patient_id", "==", patient_id) \
        .get()
        
    sessions = []
    for doc in docs:
        d = doc.to_dict()
        sessions.append(ChatSessionResponse(
            id=d["id"],
            patient_id=d["patient_id"],
            title=d["title"],
            created_at=d["created_at"],
            updated_at=d["updated_at"]
        ))
    sessions.sort(key=lambda s: s.updated_at, reverse=True)
    return sessions

async def get_chat_session_details(session_id: str, patient_id: str, db: firestore.AsyncClient) -> ChatSessionDetailResponse:
    """Fetches details of a chat session, validating patient ownership."""
    doc = await db.collection(settings.CHAT_SESSIONS_COLLECTION).document(session_id).get()
    if not doc.exists:
        raise ValueError("Session not found or access denied")
        
    d = doc.to_dict()
    if d.get("patient_id") != patient_id:
        raise ValueError("Session not found or access denied")
        
    messages = []
    for msg in d.get("messages", []):
        created_at_val = msg.get("created_at")
        if isinstance(created_at_val, str):
            try:
                created_at_val = datetime.datetime.fromisoformat(created_at_val.replace("Z", "+00:00"))
            except Exception:
                created_at_val = datetime.datetime.now(datetime.UTC)
        elif not isinstance(created_at_val, datetime.datetime):
            created_at_val = datetime.datetime.now(datetime.UTC)
            
        messages.append(ChatMessage(
            role=msg["role"],
            content=msg["content"],
            created_at=created_at_val,
            sources=msg.get("sources", [])
        ))
        
    return ChatSessionDetailResponse(
        id=d["id"],
        patient_id=d["patient_id"],
        title=d["title"],
        created_at=d["created_at"],
        updated_at=d["updated_at"],
        messages=messages
    )

async def ask_inside_session(session_id: str, patient_id: str, prompt: str, db: firestore.AsyncClient, model: str | None = None) -> ChatResponse:
    """Queries the chatbot inside an active session, grounding with history and reports."""
    doc_ref = db.collection(settings.CHAT_SESSIONS_COLLECTION).document(session_id)
    doc_snap = await doc_ref.get()
    if not doc_snap.exists:
        raise ValueError("Session not found or access denied")
        
    d = doc_snap.to_dict()
    if d.get("patient_id") != patient_id:
        raise ValueError("Session not found or access denied")
        
    messages_list = d.get("messages", [])
    
    # 1. Tenant-isolated RAG logic
    docs_snap = await db.collection(settings.DOCUMENTS_COLLECTION) \
        .where("patientId", "==", patient_id) \
        .get()
        
    documents = []
    for doc in docs_snap:
        doc_data = doc.to_dict()
        if "embedding" in doc_data and doc_data["embedding"]:
            documents.append({
                "id": doc.id,
                "fileRef": doc_data.get("fileRef", "Unknown Report"),
                "summary": doc_data.get("summary", ""),
                "raw_text": doc_data.get("raw_text", ""),
                "embedding": doc_data["embedding"]
            })
            
    prompt_vector = generate_embeddings(prompt)
    
    ranked_docs = []
    for doc in documents:
        sim = cosine_similarity(prompt_vector, doc["embedding"])
        ranked_docs.append((sim, doc))
    ranked_docs.sort(key=lambda x: x[0], reverse=True)
    
    context_parts = []
    sources = []
    for sim, doc in ranked_docs[:2]:
        if sim > 0.1:
            context_parts.append(
                f"Report Reference: {doc['fileRef']}\n"
                f"Summary: {doc['summary']}\n"
                f"Detailed details: {doc['raw_text'][:800]}\n"
            )
            sources.append(doc["fileRef"].split("/")[-1])
            
    context_str = "\n---\n".join(context_parts) if context_parts else "No report records found matching this context."
    
    # 2. Extract conversation history (max 10 messages)
    history_lines = []
    for msg in messages_list[-10:]:
        role_label = "Patient" if msg["role"] == "user" else "AI Companion"
        history_lines.append(f"{role_label}: {msg['content']}")
    history_str = "\n".join(history_lines) if history_lines else "No previous conversation history."
    
    # 3. Grounded Gemini Call
    gemini_prompt = (
        "You are an empathetic, professional AI Health Companion. "
        "Your task is to answer the patient's questions about their health or reports. "
        "You MUST structure your responses under strict clinical safety rules:\n"
        "1. GROUNDING: Use the provided medical report context to answer if possible.\n"
        "2. NON-DIAGNOSTIC: You cannot make final medical diagnoses. Use safe phrasing like 'indicates', 'suggests'.\n"
        "3. DISCLAIMER: Always include a warm disclaimer reminding the patient to consult their doctor.\n"
        "4. LANGUAGE: Answer in clear, plain language.\n\n"
        f"--- Patient Medical Context ---\n{context_str}\n\n"
        f"--- Conversation History ---\n{history_str}\n\n"
        f"--- Patient Question ---\n{prompt}\n\n"
        "Response:"
    )
    
    reply = generate_gemini_content(gemini_prompt, json_response=False, model=model)

    
    # 4. Save messages to session history
    now = datetime.datetime.now(datetime.UTC)
    user_message = {
        "role": "user",
        "content": prompt,
        "created_at": now,
        "sources": sources
    }
    model_message = {
        "role": "model",
        "content": reply,
        "created_at": now,
        "sources": sources
    }
    
    messages_list.append(user_message)
    messages_list.append(model_message)
    
    await doc_ref.update({
        "messages": messages_list,
        "updated_at": now
    })
    
    return ChatResponse(
        reply=reply,
        sources=sources
    )
