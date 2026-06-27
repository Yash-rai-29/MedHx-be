import pytest
from unittest.mock import patch, AsyncMock
from common_code.config import settings

@pytest.fixture
def mock_gcp_services():
    with patch("patient_service.documents.documents_func.parse_medical_document", new_callable=AsyncMock) as mock_parse, \
         patch("patient_service.documents.documents_func.async_generate_gemini_content", new_callable=AsyncMock) as mock_gemini, \
         patch("patient_service.documents.documents_func.async_generate_embeddings", new_callable=AsyncMock) as mock_embed, \
         patch("patient_service.documents.documents_func.translate_text") as mock_translate, \
         patch("patient_service.documents.documents_func.synthesize_speech") as mock_tts, \
         patch("patient_service.documents.documents_func.async_upload_bytes_to_gcs", new_callable=AsyncMock) as mock_upload:
        
        mock_parse.return_value = "RAW MEDICAL REPORT: Hemoglobin: 14.5, WBC: 6000"
        mock_gemini.return_value = '{"category": "lab_report", "summary": "Layman translation: Your blood count is completely normal."}'
        mock_embed.return_value = [0.1] * 768
        mock_translate.return_value = "Hindi translated text"
        mock_tts.return_value = b"MP3_BYTES_HERE"
        mock_upload.return_value = "gs://mock-bucket/reports/file"
        
        yield {
            "parse": mock_parse,
            "gemini": mock_gemini,
            "embed": mock_embed,
            "translate": mock_translate,
            "tts": mock_tts,
            "upload": mock_upload
        }

def test_direct_upload_document(client, mock_db, mock_user, mock_gcp_services):
    file_content = b"%PDF-1.4 mock pdf data"
    files = {"file": ("my_report.pdf", file_content, "application/pdf")}
    form_data = {
        "title": "My Report Title",
        "description": "This is a report description"
    }
    
    with patch("common_code.notification_dispatcher.dispatch_notification") as mock_dispatch:
        mock_dispatch.return_value = True
        
        response = client.post("/documents/upload", files=files, data=form_data)
        assert response.status_code == 201
        data = response.json()
        assert data["id"] is not None
        assert "my_report.pdf" in data["file_path"]
        assert data["status"] == "in_progress"
        assert data["title"] == "My Report Title"
        assert data["description"] == "This is a report description"
        
        # Verify Firestore document was written and background task completed parsing
        doc_record = mock_db.db_store[settings.DOCUMENTS_COLLECTION][data["id"]]
        assert doc_record["patientId"] == mock_user["uid"]
        assert doc_record["status"] == "completed"
        assert doc_record["type"] == "lab_report"
        assert "normal" in doc_record["summary"].lower()
        assert doc_record["embedding"] == [0.1] * 768
        assert doc_record["title"] == "My Report Title"
        assert doc_record["description"] == "This is a report description"
        mock_dispatch.assert_called_once()


def test_get_patient_documents(client, mock_db, mock_user):
    import datetime
    mock_db.db_store[settings.DOCUMENTS_COLLECTION] = {
        "doc-1": {
            "patientId": mock_user["uid"],
            "fileRef": "report1.pdf",
            "type": "lab_report",
            "raw_text": "Sample text",
            "summary": "Sample summary",
            "createdAt": datetime.datetime.utcnow()
        }
    }
    response = client.get("/documents")
    assert response.status_code == 200
    data = response.json()
    assert len(data) == 1
    assert data[0]["id"] == "doc-1"
    assert data[0]["summary"] == "Sample summary"

def test_translate_document_summary(client, mock_db, mock_user, mock_gcp_services):
    mock_db.db_store[settings.DOCUMENTS_COLLECTION] = {
        "doc-2": {
            "patientId": mock_user["uid"],
            "summary": "This is normal.",
            "translations": {}
        }
    }
    response = client.post("/documents/doc-2/translate", json={"target_language": "hi"})
    assert response.status_code == 200
    data = response.json()
    assert data["translated_summary"] == "Hindi translated text"
    assert data["language"] == "hi"

    # Subsequent translation requests should serve cached results
    mock_gcp_services["translate"].side_effect = Exception("Should call cache")
    response_cached = client.post("/documents/doc-2/translate", json={"target_language": "hi"})
    assert response_cached.status_code == 200
    assert response_cached.json()["translated_summary"] == "Hindi translated text"

def test_listen_document_summary(client, mock_db, mock_user, mock_gcp_services):
    mock_db.db_store[settings.DOCUMENTS_COLLECTION] = {
        "doc-3": {
            "patientId": mock_user["uid"],
            "summary": "Everything is normal.",
            "translations": {"hi": "Translated Hindi"}
        }
    }
    # Listen in Hindi
    response = client.get("/documents/doc-3/listen?lang=hi")
    assert response.status_code == 200
    assert response.content == b"MP3_BYTES_HERE"
