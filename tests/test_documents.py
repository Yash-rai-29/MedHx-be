import json

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
            "title": "Lab Report Title",
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
    assert data[0]["file_path"] == "report1.pdf"
    assert data[0]["title"] == "Lab Report Title"

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


def test_upload_prescription_generates_reminder_suggestions(client, mock_db, mock_user, mock_gcp_services):
    prescription_text = (
        "Dr Mehta Prescription\n"
        "Patient: Test User\n"
        "Tab Metformin 500mg twice daily after meals for 5 days\n"
        "Cap Dolo 650 once daily after breakfast for 3 days"
    )
    gemini_payload = {
        "category": "prescription",
        "summary": "This prescription contains two medicines and should be followed exactly as advised by your doctor.",
        "doctor_name": "Dr Mehta",
        "document_date": "2026-07-04",
        "patient_name": "Test User",
        "medications": [
            {
                "name": "Metformin",
                "dosage": "500mg",
                "frequency": "twice daily after meals",
                "instructions": "after meals"
            },
            {
                "name": "Dolo 650",
                "dosage": "650mg",
                "frequency": "once daily after breakfast",
                "instructions": "after breakfast"
            }
        ],
        "abnormal_labs": [],
        "red_flags": [],
        "actionable_steps": ["Take medicines exactly as prescribed."],
        "reminder_suggestions": [
            {
                "type": "medicine",
                "title": "Take Metformin",
                "notes": None,
                "notification_enabled": True,
                "schedule": {
                    "recurrence": "daily",
                    "time_of_day": "09:00",
                    "start_date": None,
                    "end_date": None,
                    "meal_timing": "after_meals"
                },
                "medicine_details": {
                    "name": "Metformin",
                    "dosage": "500mg",
                    "frequency": "twice daily after meals",
                    "instructions": "after meals"
                },
                "follow_up_details": None
            },
            {
                "type": "medicine",
                "title": "Take Dolo 650",
                "notes": None,
                "notification_enabled": True,
                "schedule": {
                    "recurrence": "daily",
                    "time_of_day": "08:30",
                    "start_date": None,
                    "end_date": None,
                    "meal_timing": "after_breakfast"
                },
                "medicine_details": {
                    "name": "Dolo 650",
                    "dosage": "650mg",
                    "frequency": "once daily after breakfast",
                    "instructions": "after breakfast"
                },
                "follow_up_details": None
            }
        ]
    }
    mock_gcp_services["parse"].return_value = prescription_text
    mock_gcp_services["gemini"].return_value = json.dumps(gemini_payload)

    file_content = b"%PDF-1.4 mock prescription pdf data"
    files = {"file": ("prescription.pdf", file_content, "application/pdf")}

    with patch("common_code.notification_dispatcher.dispatch_notification") as mock_dispatch:
        mock_dispatch.return_value = True

        response = client.post("/documents/upload", files=files, data={})
        assert response.status_code == 201
        doc_id = response.json()["id"]

        doc_record = mock_db.db_store[settings.DOCUMENTS_COLLECTION][doc_id]
        assert doc_record["status"] == "completed"
        assert doc_record["type"] == "prescription"
        assert len(doc_record["reminder_suggestions"]) == 2

        first = doc_record["reminder_suggestions"][0]
        second = doc_record["reminder_suggestions"][1]

        assert first["title"] == "Take Metformin"
        assert first["schedule"]["meal_timing"] is None
        assert first["schedule"]["start_date"] is not None

        assert second["title"] == "Take Dolo 650"
        assert second["schedule"]["meal_timing"] == "after_breakfast"
        assert second["schedule"]["start_date"] is not None

        detail_response = client.get(f"/documents/{doc_id}")
        assert detail_response.status_code == 200
        detail_data = detail_response.json()
        assert len(detail_data["reminder_suggestions"]) == 2
        assert detail_data["reminder_suggestions"][0]["title"] == "Take Metformin"
        assert detail_data["reminder_suggestions"][1]["title"] == "Take Dolo 650"


def test_non_prescription_document_returns_no_reminder_suggestions(client, mock_db, mock_user, mock_gcp_services):
    lab_text = "Lab Report HbA1c 7.1 percent"
    gemini_payload = {
        "category": "lab_report",
        "summary": "This is a lab report.",
        "doctor_name": "Dr Lab",
        "document_date": "2026-07-04",
        "patient_name": "Test User",
        "medications": [],
        "abnormal_labs": [
            {
                "parameter_name": "HbA1c",
                "value": "7.1%",
                "reference_range": "< 5.7%",
                "status": "High"
            }
        ],
        "red_flags": [],
        "actionable_steps": ["Discuss sugar control with your doctor."],
        "reminder_suggestions": [
            {
                "type": "medicine",
                "title": "Take Fake Medicine",
                "notes": None,
                "notification_enabled": True,
                "schedule": {
                    "recurrence": "daily",
                    "time_of_day": "09:00",
                    "start_date": None,
                    "end_date": None,
                    "meal_timing": None
                },
                "medicine_details": {
                    "name": "Fake Medicine",
                    "dosage": "10mg",
                    "frequency": "daily",
                    "instructions": None
                },
                "follow_up_details": None
            }
        ]
    }
    mock_gcp_services["parse"].return_value = lab_text
    mock_gcp_services["gemini"].return_value = json.dumps(gemini_payload)

    file_content = b"%PDF-1.4 mock lab report pdf data"
    files = {"file": ("lab-report.pdf", file_content, "application/pdf")}

    with patch("common_code.notification_dispatcher.dispatch_notification") as mock_dispatch:
        mock_dispatch.return_value = True

        response = client.post("/documents/upload", files=files, data={})
        assert response.status_code == 201
        doc_id = response.json()["id"]

        doc_record = mock_db.db_store[settings.DOCUMENTS_COLLECTION][doc_id]
        assert doc_record["type"] == "lab_report"
        assert doc_record["reminder_suggestions"] == []

        detail_response = client.get(f"/documents/{doc_id}")
        assert detail_response.status_code == 200
        assert detail_response.json()["reminder_suggestions"] == []


def test_sanitize_firestore_payload_handles_date():
    import datetime
    from common_code.firestore import sanitize_firestore_payload
    from pydantic import BaseModel

    class MockItem(BaseModel):
        dt: datetime.date
        name: str

    raw = {
        "date_field": datetime.date(2026, 8, 16),
        "datetime_field": datetime.datetime(2026, 8, 16, 12, 0, 0, tzinfo=datetime.timezone.utc),
        "nested_list": [
            datetime.date(2026, 9, 1),
            {"inner_date": datetime.date(2026, 10, 15)}
        ],
        "pydantic_obj": MockItem(dt=datetime.date(2026, 11, 20), name="test")
    }

    sanitized = sanitize_firestore_payload(raw)

    assert sanitized["date_field"] == "2026-08-16"
    assert isinstance(sanitized["datetime_field"], datetime.datetime)
    assert sanitized["nested_list"][0] == "2026-09-01"
    assert sanitized["nested_list"][1]["inner_date"] == "2026-10-15"
    assert sanitized["pydantic_obj"]["dt"] == "2026-11-20"


def test_prescription_reminder_suggestions_date_serialization(client, mock_db, mock_user, mock_gcp_services):
    presc_text = "Doctor prescription: Tab. Amoxicillin 500mg once daily for 5 days."
    gemini_payload = {
        "category": "prescription",
        "title": "Amoxicillin Prescription",
        "summary": "Take Amoxicillin 500mg daily for 5 days.",
        "doctor_name": "Dr. Sharma",
        "document_date": "2026-08-16",
        "patient_name": "Test Patient",
        "medications": [{"name": "Amoxicillin", "dosage": "500mg", "frequency": "daily", "instructions": "after breakfast"}],
        "abnormal_labs": [],
        "red_flags": [],
        "actionable_steps": ["Complete 5-day course"],
        "reminder_suggestions": [
            {
                "type": "medicine",
                "title": "Take Amoxicillin",
                "notes": None,
                "notification_enabled": True,
                "schedule": {
                    "recurrence": "daily",
                    "time_of_day": "09:00",
                    "start_date": None,
                    "end_date": None,
                    "meal_timing": "after_breakfast"
                },
                "medicine_details": {
                    "name": "Amoxicillin",
                    "dosage": "500mg",
                    "frequency": "daily",
                    "instructions": "after breakfast",
                    "duration": "5 days"
                },
                "follow_up_details": None
            }
        ]
    }
    mock_gcp_services["parse"].return_value = presc_text
    mock_gcp_services["gemini"].return_value = json.dumps(gemini_payload)

    file_content = b"%PDF-1.4 mock prescription pdf"
    files = {"file": ("prescription.pdf", file_content, "application/pdf")}

    with patch("common_code.notification_dispatcher.dispatch_notification") as mock_dispatch:
        mock_dispatch.return_value = True

        response = client.post("/documents/upload", files=files, data={})
        assert response.status_code == 201
        doc_id = response.json()["id"]

        doc_record = mock_db.db_store[settings.DOCUMENTS_COLLECTION][doc_id]
        assert doc_record["status"] == "completed"
        assert doc_record["type"] == "prescription"
        suggestions = doc_record["reminder_suggestions"]
        assert len(suggestions) == 1
        sched = suggestions[0]["schedule"]
        # Verify dates are strings, not datetime.date objects
        assert isinstance(sched["start_date"], str)
        assert isinstance(sched["end_date"], str)

 