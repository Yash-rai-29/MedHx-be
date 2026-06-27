import pytest
import datetime
from unittest.mock import patch
from common_code.config import settings

@pytest.fixture
def mock_ai():
    with patch("patient_service.chatbot.chatbot_func.async_generate_embeddings") as mock_embed, \
         patch("patient_service.chatbot.chatbot_func.async_generate_gemini_content") as mock_gemini:
        # Prompt search embedding matching target document
        mock_embed.return_value = [1.0] + [0.0] * 767
        mock_gemini.return_value = "Your blood test indicates healthy blood cells, but keep drinking water."
        yield {
            "embed": mock_embed,
            "gemini": mock_gemini
        }

def test_chatbot_rag_tenant_isolation(client, mock_db, mock_user, mock_ai):
    # Patient A (Authenticated): test-patient-123
    # Patient B (Attacker/Separate): patient-456
    
    mock_db.db_store[settings.DOCUMENTS_COLLECTION] = {
        # Document owned by Patient A
        "doc-a": {
            "patientId": "test-patient-123",
            "fileRef": "patient_a_report.pdf",
            "summary": "Patient A has mild fever.",
            "raw_text": "Symptoms: fever for 2 days. Diagnostic result: normal.",
            "status": "completed",
            "embedding": [1.0] + [0.0] * 767 # matches prompt vector exactly
        },
        # Document owned by Patient B
        "doc-b": {
            "patientId": "patient-456",
            "fileRef": "secret_patient_b_report.pdf",
            "summary": "Patient B has acute bronchitis.",
            "raw_text": "Secret details: Patient B diagnosed with tuberculosis CA01.",
            "status": "completed",
            "embedding": [1.0] + [0.0] * 767 # matches prompt vector exactly
        }
    }

    # Query the chatbot
    response = client.post("/chatbot/ask", json={"prompt": "Do I have bronchitis?"})
    assert response.status_code == 200
    data = response.json()
    assert data["reply"] is not None
    assert any(s["title"] == "patient_a_report.pdf" for s in data["sources"])
    assert not any(s["title"] == "secret_patient_b_report.pdf" for s in data["sources"]) # absolute tenant isolation enforced

    # Inspect RAG prompt construction sent to Gemini
    args, kwargs = mock_ai["gemini"].call_args
    grounding_prompt = args[0]
    
    # Must contain patient A context
    assert "Patient A has mild fever" in grounding_prompt
    # Must NOT leak patient B context under any circumstances
    assert "patient-456" not in grounding_prompt
    assert "bronchitis" not in grounding_prompt or "secret_patient_b_report.pdf" not in grounding_prompt
    assert "tuberculosis" not in grounding_prompt

def test_create_chatbot_session(client, mock_db, mock_user):
    payload = {"title": "My Health Concerns"}
    response = client.post("/chatbot/sessions", json=payload)
    assert response.status_code == 201
    data = response.json()
    assert data["id"] is not None
    assert data["patient_id"] == mock_user["uid"]
    assert data["title"] == "My Health Concerns"
    
    # Verify in Firestore
    session = mock_db.db_store[settings.CHAT_SESSIONS_COLLECTION][data["id"]]
    assert session["title"] == "My Health Concerns"
    assert session["patient_id"] == mock_user["uid"]
    assert session["messages"] == []

def test_list_chatbot_sessions(client, mock_db, mock_user):
    now = datetime.datetime.now(datetime.UTC)
    mock_db.db_store[settings.CHAT_SESSIONS_COLLECTION] = {
        "session-1": {
            "id": "session-1",
            "patient_id": mock_user["uid"],
            "title": "Diabetes Check",
            "created_at": now,
            "updated_at": now,
            "messages": []
        },
        "session-2": {
            "id": "session-2",
            "patient_id": "other-patient",
            "title": "Heart Check",
            "created_at": now,
            "updated_at": now,
            "messages": []
        }
    }
    
    response = client.get("/chatbot/sessions")
    assert response.status_code == 200
    data = response.json()
    assert len(data) == 1
    assert data[0]["id"] == "session-1"
    assert data[0]["title"] == "Diabetes Check"

def test_get_chatbot_session_details(client, mock_db, mock_user):
    now = datetime.datetime.now(datetime.UTC)
    mock_db.db_store[settings.CHAT_SESSIONS_COLLECTION] = {
        "session-1": {
            "id": "session-1",
            "patient_id": mock_user["uid"],
            "title": "Fever Check",
            "created_at": now,
            "updated_at": now,
            "messages": [
                {"role": "user", "content": "I have fever", "created_at": now, "sources": []},
                {"role": "model", "content": "Take paracetamol", "created_at": now, "sources": []}
            ]
        }
    }
    
    response = client.get("/chatbot/sessions/session-1")
    assert response.status_code == 200
    data = response.json()
    assert data["id"] == "session-1"
    assert len(data["messages"]) == 2
    assert data["messages"][0]["role"] == "user"
    assert data["messages"][0]["content"] == "I have fever"

def test_ask_inside_chatbot_session(client, mock_db, mock_user, mock_ai):
    now = datetime.datetime.now(datetime.UTC)
    mock_db.db_store[settings.CHAT_SESSIONS_COLLECTION] = {
        "session-1": {
            "id": "session-1",
            "patient_id": mock_user["uid"],
            "title": "Fever Check",
            "created_at": now,
            "updated_at": now,
            "messages": []
        }
    }
    
    response = client.post("/chatbot/sessions/session-1/ask", json={"prompt": "Do I have high fever?"})
    assert response.status_code == 200
    data = response.json()
    assert data["reply"] is not None
    
    # Check messages array updated in db
    session = mock_db.db_store[settings.CHAT_SESSIONS_COLLECTION]["session-1"]
    assert len(session["messages"]) == 2
    assert session["messages"][0]["role"] == "user"
    assert session["messages"][0]["content"] == "Do I have high fever?"
    assert session["messages"][1]["role"] == "model"
    assert session["messages"][1]["content"] == "Your blood test indicates healthy blood cells, but keep drinking water."

def test_chatbot_session_access_control(client, mock_db, mock_user):
    now = datetime.datetime.now(datetime.UTC)
    # Session owned by a different user
    mock_db.db_store[settings.CHAT_SESSIONS_COLLECTION] = {
        "session-other": {
            "id": "session-other",
            "patient_id": "different-user-123",
            "title": "Secret Session",
            "created_at": now,
            "updated_at": now,
            "messages": []
        }
    }
    
    # Try fetching session details
    response_details = client.get("/chatbot/sessions/session-other")
    assert response_details.status_code == 404
    
    # Try asking question
    response_ask = client.post("/chatbot/sessions/session-other/ask", json={"prompt": "What is my diagnosis?"})
    assert response_ask.status_code == 404

def test_chatbot_voice_websocket(client, mock_db, mock_user, mock_ai):
    now = datetime.datetime.now(datetime.UTC)
    mock_db.db_store[settings.CHAT_SESSIONS_COLLECTION] = {
        "session-ws": {
            "id": "session-ws",
            "patient_id": mock_user["uid"],
            "title": "Voice Session",
            "created_at": now,
            "updated_at": now,
            "messages": []
        }
    }
    
    with patch("patient_service.chatbot.chatbot_router.transcribe_audio_bytes", return_value={"full_text": "Am I healthy?"}) as mock_transcribe, \
         patch("patient_service.chatbot.chatbot_router.synthesize_speech", return_value=b"SYNTHESIZED_MP3_SPEECH") as mock_synth:
         
        with client.websocket_connect("/chatbot/sessions/session-ws/ws/voice?token=mock-valid-token") as websocket:
            # 1. Test sending binary audio data (must be >100 bytes to pass length guard)
            websocket.send_bytes(b"INPUT_AUDIO_RAW_BYTES" * 10)  # 210 bytes
            
            # Receive immediate transcription confirmation
            transcribed_event = websocket.receive_json()
            assert transcribed_event["event"] == "transcribed"
            assert transcribed_event["user_text"] == "Am I healthy?"

            # Receive full metadata response (JSON)
            response_metadata = websocket.receive_json()
            assert response_metadata["event"] == "response"
            assert response_metadata["user_text"] == "Am I healthy?"
            assert "water" in response_metadata["ai_text"].lower()
            
            # Receive synthesized voice bytes (binary)
            voice_bytes = websocket.receive_bytes()
            assert voice_bytes == b"SYNTHESIZED_MP3_SPEECH"
            
            # 2. Test sending text message (JSON format)
            websocket.send_text('{"prompt": "Is everything fine?"}')
            response_text_metadata = websocket.receive_json()
            assert response_text_metadata["event"] == "response"
            assert response_text_metadata["user_text"] == "Is everything fine?"
            
            voice_bytes_text = websocket.receive_bytes()
            assert voice_bytes_text == b"SYNTHESIZED_MP3_SPEECH"

