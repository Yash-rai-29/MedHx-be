import pytest
import datetime
from unittest.mock import AsyncMock, patch
from common_code.config import settings
from common_code.firebase_auth import require_role

def test_patient_registration_full(client, mock_db, mock_user):
    payload = {
        "name": "Arjun Singh",
        "country_code": "+91",
        "phone_number": "9876543210",
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
    assert data["country_code"] == "+91"
    assert data["phone_number"] == "9876543210"
    assert "phone" not in data
    assert data["email"] == "patient@example.com"
    assert data["location"] == "Mumbai, India"
    assert data["date_of_birth"] == "1990-05-15"
    assert data["onboarding_status"] == "pending"

    # Verify Firestore writes
    user_record = mock_db.db_store[settings.USERS_COLLECTION][mock_user["uid"]]
    assert user_record["name"] == "Arjun Singh"
    assert user_record["country_code"] == "+91"
    assert user_record["phone_number"] == "9876543210"
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
    assert data["country_code"] is None
    assert data["phone_number"] is None
    assert "phone" not in data
    assert data["email"] == mock_user["email"]
    assert data["name"] == "Social User"

    user_record = mock_db.db_store[settings.USERS_COLLECTION][mock_user["uid"]]
    assert user_record.get("phone_number") is None
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


def test_schedule_patient_account_deletion_endpoint(client, mock_db, mock_user):
    uid = mock_user["uid"]
    mock_db.db_store[settings.USERS_COLLECTION] = {uid: {"uid": uid, "name": "Active Patient", "email": "patient@example.com"}}

    resp = client.delete("/auth/delete-account")
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "pending_deletion"
    assert data["patient_id"] == uid
    assert "48 hours" in data["message"]

    # Verify stored in account_deletion_requests collection
    assert uid in mock_db.db_store[settings.ACCOUNT_DELETIONS_COLLECTION]
    req_doc = mock_db.db_store[settings.ACCOUNT_DELETIONS_COLLECTION][uid]
    assert req_doc["status"] == "pending_deletion"
    assert req_doc["scheduled_deletion_at"] > req_doc["requested_at"]

    # Verify user account flagged
    user_doc = mock_db.db_store[settings.USERS_COLLECTION][uid]
    assert user_doc["account_status"] == "pending_deletion"


def test_cancel_patient_account_deletion_endpoint(client, mock_db, mock_user):
    uid = mock_user["uid"]
    mock_db.db_store[settings.USERS_COLLECTION] = {uid: {"uid": uid, "name": "Active Patient", "account_status": "pending_deletion"}}
    mock_db.db_store[settings.ACCOUNT_DELETIONS_COLLECTION] = {
        uid: {
            "patient_id": uid,
            "status": "pending_deletion",
            "requested_at": datetime.datetime.now(datetime.UTC),
            "scheduled_deletion_at": datetime.datetime.now(datetime.UTC) + datetime.timedelta(hours=48),
        }
    }

    resp = client.post("/auth/cancel-delete-account")
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "cancelled"
    assert data["patient_id"] == uid

    # Verify status updated
    assert mock_db.db_store[settings.ACCOUNT_DELETIONS_COLLECTION][uid]["status"] == "cancelled"
    assert mock_db.db_store[settings.USERS_COLLECTION][uid]["account_status"] == "active"


@pytest.mark.anyio
async def test_account_deletion_cloud_job_purge(mock_db, monkeypatch):
    import common_code.firestore
    monkeypatch.setattr(common_code.firestore, "_db", mock_db)

    from account_deletion_job.main import run_deletion_job

    uid = "expired_user_789"
    # Seed account deletion request where scheduled_deletion_at is in the past
    past_time = datetime.datetime.now(datetime.UTC) - datetime.timedelta(hours=50)
    mock_db.db_store[settings.ACCOUNT_DELETIONS_COLLECTION] = {
        uid: {
            "patient_id": uid,
            "status": "pending_deletion",
            "requested_at": past_time,
            "scheduled_deletion_at": past_time + datetime.timedelta(hours=48),
        }
    }
    # Seed data in collections
    mock_db.db_store[settings.USERS_COLLECTION] = {uid: {"uid": uid, "name": "To Delete"}}
    mock_db.db_store[settings.PATIENTS_COLLECTION] = {uid: {"blood_group": "O+"}}
    mock_db.db_store[settings.VITALS_COLLECTION] = {"v1": {"patientId": uid, "systolic": 120}}
    mock_db.db_store[settings.DOCUMENTS_COLLECTION] = {"d1": {"patientId": uid, "title": "Blood Report"}}
    mock_db.db_store[settings.CONSULTATIONS_COLLECTION] = {"c1": {"patientId": uid, "summary": "Consult"}}
    mock_db.db_store[settings.REMINDERS_COLLECTION] = {"r1": {"patientId": uid, "title": "Meds"}}
    mock_db.db_store[settings.NOTIFICATIONS_COLLECTION] = {"n1": {"patient_id": uid, "title": "Alert"}}
    mock_db.db_store[settings.EXPORTS_COLLECTION] = {"e1": {"patient_id": uid, "status": "completed"}}

    with patch("account_deletion_job.purgers.storage_purger.async_delete_gcs_prefix", new_callable=AsyncMock) as mock_gcs_del, \
         patch("account_deletion_job.purgers.auth_purger.auth.delete_user") as mock_fb_del:

        exit_code = await run_deletion_job()
        assert exit_code == 0

        # Verify Firestore collections purged
        assert uid not in mock_db.db_store[settings.USERS_COLLECTION]
        assert uid not in mock_db.db_store[settings.PATIENTS_COLLECTION]
        assert "v1" not in mock_db.db_store[settings.VITALS_COLLECTION]
        assert "d1" not in mock_db.db_store[settings.DOCUMENTS_COLLECTION]
        assert "c1" not in mock_db.db_store[settings.CONSULTATIONS_COLLECTION]
        assert "r1" not in mock_db.db_store[settings.REMINDERS_COLLECTION]
        assert "n1" not in mock_db.db_store[settings.NOTIFICATIONS_COLLECTION]
        assert "e1" not in mock_db.db_store[settings.EXPORTS_COLLECTION]

        # Verify deletion request marked completed
        assert mock_db.db_store[settings.ACCOUNT_DELETIONS_COLLECTION][uid]["status"] == "completed"

        # Verify GCS & Auth calls
        assert mock_gcs_del.called
        assert mock_fb_del.called


def test_execute_deletion_endpoint_when_pending(client, mock_db):
    uid = "task_test_user_1"
    mock_db.db_store[settings.ACCOUNT_DELETIONS_COLLECTION] = {
        uid: {
            "patient_id": uid,
            "status": "pending_deletion",
        }
    }

    with patch("patient_service.auth.auth_func.trigger_account_deletion_job", new_callable=AsyncMock) as mock_trigger:
        resp = client.post(
            "/auth/execute-deletion",
            json={"patient_id": uid},
            headers={"X-Cloud-Tasks-Secret": "local-tasks-secret"},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "triggered"
        assert mock_trigger.called


def test_execute_deletion_endpoint_when_cancelled(client, mock_db):
    uid = "task_test_user_2"
    mock_db.db_store[settings.ACCOUNT_DELETIONS_COLLECTION] = {
        uid: {
            "patient_id": uid,
            "status": "cancelled",
        }
    }

    with patch("patient_service.auth.auth_func.trigger_account_deletion_job", new_callable=AsyncMock) as mock_trigger:
        resp = client.post(
            "/auth/execute-deletion",
            json={"patient_id": uid},
            headers={"X-Cloud-Tasks-Secret": "local-tasks-secret"},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "skipped"
        assert not mock_trigger.called


def test_update_user_account_success(client, mock_db, mock_user):
    # 1. Register user
    reg_payload = {
        "name": "Original Name",
        "country_code": "+91",
        "phone_number": "9876543210",
        "email": "user@example.com",
        "language_preference": "en",
        "date_of_birth": "1990-01-01",
        "location": "Delhi, India",
        "accepted_privacy_policy": True,
        "accepted_terms_of_service": True,
    }
    reg_resp = client.post("/auth/register", json=reg_payload)
    assert reg_resp.status_code == 201

    # 2. Update user profile via PATCH /auth/me
    patch_payload = {
        "name": "Updated Name",
        "country_code": "+91",
        "phone_number": "9999988888",
        "language_preference": "hi",
        "date_of_birth": "1992-06-15",
        "location": "Bengaluru, India",
    }
    patch_resp = client.patch("/auth/me", json=patch_payload)
    assert patch_resp.status_code == 200
    data = patch_resp.json()
    assert data["name"] == "Updated Name"
    assert data["country_code"] == "+91"
    assert data["phone_number"] == "9999988888"
    assert "phone" not in data
    assert data["language_preference"] == "hi"
    assert data["date_of_birth"] == "1992-06-15"
    assert data["location"] == "Bengaluru, India"
    assert data["email"] == mock_user["email"]  # Email remains untouched from registration

    # Verify Firestore users collection
    user_record = mock_db.db_store[settings.USERS_COLLECTION][mock_user["uid"]]
    assert user_record["name"] == "Updated Name"
    assert user_record["country_code"] == "+91"
    assert user_record["phone_number"] == "9999988888"
    assert user_record["language_preference"] == "hi"
    assert user_record["location"] == "Bengaluru, India"
    assert user_record["date_of_birth"] == "1992-06-15"

    # Verify Firestore patients collection sync
    patient_record = mock_db.db_store[settings.PATIENTS_COLLECTION][mock_user["uid"]]
    assert patient_record["location"] == "Bengaluru, India"
    assert patient_record["date_of_birth"] == "1992-06-15"
    assert patient_record["age"] is not None


def test_update_user_account_partial(client, mock_db, mock_user):
    # Register user
    reg_payload = {
        "name": "John Doe",
        "country_code": "+91",
        "phone_number": "1111111111",
        "accepted_privacy_policy": True,
        "accepted_terms_of_service": True,
    }
    client.post("/auth/register", json=reg_payload)

    # Partial update: only location
    patch_resp = client.patch("/auth/me", json={"location": "Pune, India"})
    assert patch_resp.status_code == 200
    data = patch_resp.json()
    assert data["name"] == "John Doe"
    assert data["country_code"] == "+91"
    assert data["phone_number"] == "1111111111"
    assert data["location"] == "Pune, India"


def test_update_user_account_future_dob(client, mock_db, mock_user):
    client.post("/auth/register", json={"accepted_privacy_policy": True, "accepted_terms_of_service": True})
    future_year = datetime.date.today().year + 2
    patch_resp = client.patch("/auth/me", json={"date_of_birth": f"{future_year}-01-01"})
    assert patch_resp.status_code == 400
    assert "future" in patch_resp.json()["detail"].lower()


def test_update_user_account_invalid_dob_format(client, mock_db, mock_user):
    client.post("/auth/register", json={"accepted_privacy_policy": True, "accepted_terms_of_service": True})
    patch_resp = client.patch("/auth/me", json={"date_of_birth": "15-05-1990"})
    assert patch_resp.status_code == 400
    assert "yyyy-mm-dd" in patch_resp.json()["detail"].lower()


def test_update_user_account_short_name(client, mock_db, mock_user):
    client.post("/auth/register", json={"accepted_privacy_policy": True, "accepted_terms_of_service": True})
    patch_resp = client.patch("/auth/me", json={"name": "A"})
    assert patch_resp.status_code in [400, 422]


def test_update_user_account_not_found(client, mock_db, mock_user):
    # Do not register user in mock_db
    patch_resp = client.patch("/auth/me", json={"location": "Chennai, India"})
    assert patch_resp.status_code == 404
    assert "does not exist" in patch_resp.json()["detail"].lower()


def test_update_user_account_alias_endpoint(client, mock_db, mock_user):
    client.post("/auth/register", json={"accepted_privacy_policy": True, "accepted_terms_of_service": True})
    patch_resp = client.patch("/auth/account", json={"name": "Alias Tester"})
    assert patch_resp.status_code == 200
    assert patch_resp.json()["name"] == "Alias Tester"


def test_register_with_separated_country_code_and_phone_number(client, mock_db, mock_user):
    reg_payload = {
        "name": "Arjun Sharma",
        "country_code": "+91",
        "phone_number": "9812345678",
        "accepted_privacy_policy": True,
        "accepted_terms_of_service": True,
    }
    resp = client.post("/auth/register", json=reg_payload)
    assert resp.status_code in [200, 201]
    data = resp.json()
    assert data["country_code"] == "+91"
    assert data["phone_number"] == "9812345678"
    assert "phone" not in data

    # Verify Firestore storage
    user_record = mock_db.db_store[settings.USERS_COLLECTION][mock_user["uid"]]
    assert user_record["country_code"] == "+91"
    assert user_record["phone_number"] == "9812345678"


def test_update_user_account_separated_phone(client, mock_db, mock_user):
    # 1. Register with initial phone
    client.post("/auth/register", json={
        "name": "Jane Doe",
        "country_code": "+91",
        "phone_number": "9800011122",
        "accepted_privacy_policy": True,
        "accepted_terms_of_service": True,
    })

    # 2. Update with separate country_code and phone_number
    patch_resp = client.patch("/auth/me", json={
        "country_code": "+1",
        "phone_number": "6505559876",
    })
    assert patch_resp.status_code == 200
    data = patch_resp.json()
    assert data["country_code"] == "+1"
    assert data["phone_number"] == "6505559876"
    assert "phone" not in data

    # Verify in Firestore
    user_record = mock_db.db_store[settings.USERS_COLLECTION][mock_user["uid"]]
    assert user_record["country_code"] == "+1"
    assert user_record["phone_number"] == "6505559876"


def test_legacy_user_document_migration(client, mock_db, mock_user):
    # Simulate legacy Firestore document with only 'phone' and no 'country_code'
    mock_db.db_store.setdefault(settings.USERS_COLLECTION, {})
    mock_db.db_store[settings.USERS_COLLECTION][mock_user["uid"]] = {
        "uid": mock_user["uid"],
        "name": "Legacy User",
        "phone": "9876543210",
        "email": mock_user["email"],
        "role": "patient",
        "language_preference": "en",
        "onboarding_status": "completed",
    }

    # Fetch user via GET /auth/me
    resp = client.get("/auth/me")
    assert resp.status_code == 200
    data = resp.json()
    assert data["phone_number"] == "9876543210"
    assert "phone" not in data









