import pytest
import datetime
from common_code.config import settings
from common_code.firebase_auth import require_role

def test_patient_registration_full(client, mock_db, mock_user):
    payload = {
        "name": "Arjun Singh",
        "phone": "+919876543210",
        "email": "patient@example.com",
        "language_preference": "hi",
        "date_of_birth": "1990-05-15",
        "location": "Mumbai, India",
        "accepted_privacy_policy": True,
        "accepted_terms_of_service": True
    }
    response = client.post("/auth/register", json=payload)
    assert response.status_code == 201
    data = response.json()
    assert data["uid"] == mock_user["uid"]
    assert data["name"] == "Arjun Singh"
    assert data["phone"] == "+919876543210"
    assert data["email"] == "patient@example.com"
    assert data["location"] == "Mumbai, India"
    assert data["date_of_birth"] == "1990-05-15"
    assert data["onboarding_status"] == "pending"

    # Verify Firestore writes
    user_record = mock_db.db_store[settings.USERS_COLLECTION][mock_user["uid"]]
    assert user_record["name"] == "Arjun Singh"
    assert user_record["phone"] == "+919876543210"
    assert user_record["role"] == "patient"
    assert user_record["location"] == "Mumbai, India"
    assert user_record["onboarding_status"] == "pending"
    assert user_record["accepted_privacy_policy"] is True

    patient_record = mock_db.db_store[settings.PATIENTS_COLLECTION][mock_user["uid"]]
    assert patient_record["location"] == "Mumbai, India"
    assert patient_record["date_of_birth"] == "1990-05-15"
    assert patient_record["onboarding_status"] == "pending"
    # age calculation: 2026 - 1990 = 36
    assert patient_record["age"] == 36 or patient_record["age"] == 35

def test_patient_registration_social_no_phone(client, mock_db, mock_user):
    payload = {
        "name": "Social User",
        "language_preference": "te",
        "date_of_birth": "1995-10-10",
        "location": "Hyderabad, India",
        "accepted_privacy_policy": True,
        "accepted_terms_of_service": True
    }
    response = client.post("/auth/register", json=payload)
    assert response.status_code == 201
    data = response.json()
    assert data["phone"] is None
    assert data["email"] == mock_user["email"]
    assert data["name"] == "Social User"

    user_record = mock_db.db_store[settings.USERS_COLLECTION][mock_user["uid"]]
    assert user_record["phone"] is None
    assert user_record["email"] == "patient@example.com"

def test_patient_registration_fallback_name(client, mock_db, mock_user):
    # Omit name from request payload entirely - resolves from mock token: "Test Patient"
    payload = {
        "accepted_privacy_policy": True,
        "accepted_terms_of_service": True
    }
    response = client.post("/auth/register", json=payload)
    assert response.status_code == 201
    data = response.json()
    assert data["name"] == "Test Patient"

def test_patient_registration_missing_name_both(client, mock_db, mock_user):
    # Remove name from token too
    mock_user["name"] = None
    payload = {
        "accepted_privacy_policy": True,
        "accepted_terms_of_service": True
    }
    response = client.post("/auth/register", json=payload)
    assert response.status_code == 400
    assert "name" in response.json()["detail"].lower()

def test_patient_registration_duplicate(client, mock_db, mock_user):
    payload = {
        "name": "Arjun Singh",
        "language_preference": "en",
        "accepted_privacy_policy": True,
        "accepted_terms_of_service": True
    }
    response = client.post("/auth/register", json=payload)
    assert response.status_code == 201

    # Try registering again under same uid
    response_duplicate = client.post("/auth/register", json=payload)
    assert response_duplicate.status_code == 400
    assert "already registered" in response_duplicate.json()["detail"].lower()

def test_patient_registration_invalid_dob(client, mock_db, mock_user):
    payload = {
        "name": "Arjun Singh",
        "language_preference": "en",
        "date_of_birth": "15-05-1990",
        "accepted_privacy_policy": True,
        "accepted_terms_of_service": True
    }
    response = client.post("/auth/register", json=payload)
    assert response.status_code == 400
    assert "format" in response.json()["detail"].lower()

def test_patient_registration_future_dob(client, mock_db, mock_user):
    payload = {
        "name": "Arjun Singh",
        "language_preference": "en",
        "date_of_birth": "2050-01-01",
        "accepted_privacy_policy": True,
        "accepted_terms_of_service": True
    }
    response = client.post("/auth/register", json=payload)
    assert response.status_code == 400
    assert "future" in response.json()["detail"].lower()

def test_patient_registration_invalid_email(client, mock_db, mock_user):
    mock_user["email"] = None
    payload = {
        "name": "Arjun Singh",
        "language_preference": "en",
        "email": "not_an_email",
        "accepted_privacy_policy": True,
        "accepted_terms_of_service": True
    }
    response = client.post("/auth/register", json=payload)
    assert response.status_code == 400
    assert "email" in response.json()["detail"].lower()

def test_patient_registration_missing_policy_acceptance(client, mock_db, mock_user):
    payload = {
        "name": "Arjun Singh",
        "language_preference": "en",
        "accepted_privacy_policy": False,
        "accepted_terms_of_service": True
    }
    response = client.post("/auth/register", json=payload)
    assert response.status_code == 400
    assert "accept" in response.json()["detail"].lower()

def test_get_legal_document(client, mock_db, mock_user):
    now = datetime.datetime.now(datetime.UTC)
    mock_db.db_store[settings.LEGAL_COLLECTION] = {
        "privacy_policy_1.0.0": {
            "doc_type": "privacy_policy",
            "title": "Privacy Policy Test",
            "content_markdown": "# Privacy Policy Content",
            "version": "1.0.0",
            "updated_at": now
        }
    }
    
    response = client.get("/auth/legal/privacy_policy")
    assert response.status_code == 200
    data = response.json()
    assert data["title"] == "Privacy Policy Test"
    assert data["content_markdown"] == "# Privacy Policy Content"

    # Test invalid document type
    response_invalid = client.get("/auth/legal/invalid_doc_type")
    assert response_invalid.status_code == 404

def test_list_legal_documents(client, mock_db, mock_user):
    now = datetime.datetime.now(datetime.UTC)
    mock_db.db_store[settings.LEGAL_COLLECTION] = {
        "privacy_policy_1.0.0": {
            "doc_type": "privacy_policy",
            "title": "Privacy Policy",
            "content_markdown": "# PP",
            "version": "1.0.0",
            "updated_at": now
        },
        "terms_of_service_1.0.0": {
            "doc_type": "terms_of_service",
            "title": "Terms",
            "content_markdown": "# Terms",
            "version": "1.0.0",
            "updated_at": now
        }
    }
    response = client.get("/auth/legal")
    assert response.status_code == 200
    data = response.json()
    assert len(data) == 2
    assert data[0]["title"] in ["Privacy Policy", "Terms"]

def test_get_me_profile(client, mock_db, mock_user):
    # Setup mock user in db first
    mock_db.db_store[settings.USERS_COLLECTION] = {
        mock_user["uid"]: {
            "uid": mock_user["uid"],
            "name": "Registered User",
            "phone": "+919999999999",
            "email": "patient@example.com",
            "role": "patient",
            "language_preference": "ta",
            "onboarding_status": "pending"
        }
    }
    response = client.get("/auth/me")
    assert response.status_code == 200
    data = response.json()
    assert data["name"] == "Registered User"
    assert data["role"] == "patient"
    assert data["onboarding_status"] == "pending"

def test_patient_login(client, mock_db, mock_user):
    # Try logging in unregistered user
    response_unreg = client.post("/auth/login")
    assert response_unreg.status_code == 404
    
    # Setup user in db
    mock_db.db_store[settings.USERS_COLLECTION] = {
        mock_user["uid"]: {
            "uid": mock_user["uid"],
            "name": "Registered User",
            "phone": "+919999999999",
            "email": "patient@example.com",
            "role": "patient",
            "language_preference": "ta",
            "onboarding_status": "pending"
        }
    }
    mock_db.db_store[settings.PATIENTS_COLLECTION] = {
        mock_user["uid"]: {
            "meal_times": {},
            "onboarding_status": "pending"
        }
    }
    
    response = client.post("/auth/login")
    assert response.status_code == 200
    data = response.json()
    assert data["name"] == "Registered User"
    assert data["role"] == "patient"

@pytest.mark.anyio
async def test_require_role_firestore_fallback(mock_db, mock_user, monkeypatch):
    import common_code.firestore
    monkeypatch.setattr(common_code.firestore, "_db", mock_db)
    
    # Put user in database
    mock_user["role"] = None
    mock_db.db_store[settings.USERS_COLLECTION] = {
        mock_user["uid"]: {
            "uid": mock_user["uid"],
            "name": "Registered User",
            "role": "patient"
        }
    }
    
    # Get require_role dependency
    dep = require_role(["patient"])
    res = await dep(mock_user)
    assert res["role"] == "patient"

def test_patient_registration_with_google_provider(client, mock_db, mock_user):
    mock_user["firebase"] = {"sign_in_provider": "google.com"}
    payload = {
        "name": "Google User",
        "email": "patient@example.com",
        "language_preference": "en",
        "accepted_privacy_policy": True,
        "accepted_terms_of_service": True
    }
    response = client.post("/auth/register", json=payload)
    assert response.status_code == 201
    data = response.json()
    assert data["auth_provider"] == "google.com"
    
    # Assert Firestore records have auth_provider
    user_record = mock_db.db_store[settings.USERS_COLLECTION][mock_user["uid"]]
    assert user_record["auth_provider"] == "google.com"

