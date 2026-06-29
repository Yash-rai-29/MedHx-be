import pytest
import datetime
from common_code.config import settings

def test_get_and_update_patient_profile(client, mock_db, mock_user):
    # Initialize profile
    mock_db.db_store[settings.USERS_COLLECTION] = {
        mock_user["uid"]: {
            "uid": mock_user["uid"],
            "name": "Arjun Singh",
            "phone": "+919876543210",
            "email": "patient@example.com",
            "role": "patient",
            "language_preference": "hi",
            "auth_provider": "google.com",
            "accepted_privacy_policy": True,
            "accepted_terms_of_service": True
        }
    }
    mock_db.db_store[settings.PATIENTS_COLLECTION] = {
        mock_user["uid"]: {
            "allergies": ["Peanuts"],
            "chronic_conditions": ["Asthma"],
            "current_medications": [],
            "blood_group": "O+",
            "past_surgeries": [],
            "family_history": [],
            "meal_times": {
                "breakfast": "08:30",
                "lunch": "13:30",
                "dinner": "20:30"
            },
            "age": 30,
            "gender": "Male",
            "date_of_birth": "1996-01-01",
            "location": "Delhi",
            "onboarding_status": "pending"
        }
    }

    # Verify Get Profile
    response = client.get("/profile")
    assert response.status_code == 200
    data = response.json()
    assert data["blood_group"] == "O+"
    assert "Asthma" in data["chronic_conditions"]
    assert data["location"] == "Delhi"
    assert data["name"] == "Arjun Singh"
    assert data["auth_provider"] == "google.com"
    assert data["accepted_privacy_policy"] is True


    # Update Profile
    update_payload = {
        "allergies": ["Peanuts", "Dust"],
        "location": "Pune, India",
        "date_of_birth": "1994-01-01" # age recalculates: 2026 - 1994 = 32
    }
    update_resp = client.patch("/profile", json=update_payload)
    assert update_resp.status_code == 200
    updated_data = update_resp.json()
    assert "Dust" in updated_data["allergies"]
    assert updated_data["location"] == "Pune, India"
    assert updated_data["age"] == 32
    assert updated_data["onboarding_status"] == "completed"
    
    # Verify in Mock DB stores
    assert mock_db.db_store[settings.PATIENTS_COLLECTION][mock_user["uid"]]["onboarding_status"] == "completed"
    assert mock_db.db_store[settings.USERS_COLLECTION][mock_user["uid"]]["onboarding_status"] == "completed"

def test_vitals_logging_indian_bmi(client, mock_db, mock_user):
    # Initialize profile
    mock_db.db_store[settings.PATIENTS_COLLECTION] = {
        mock_user["uid"]: {
            "meal_times": {
                "breakfast": "08:30",
                "lunch": "13:30",
                "dinner": "20:30"
            }
        }
    }

    # Log Normal weight/height (height 175cm, weight 65kg -> BMI ~ 21.2)
    payload_normal = {"height": 175.0, "weight": 65.0}
    response = client.post("/profile/vitals", json=payload_normal)
    assert response.status_code == 201
    data = response.json()
    assert data["bmi"] == 21.22
    assert data["category"] == "Normal"

    # Log Overweight for Indian context (height 175cm, weight 72kg -> BMI ~ 23.5)
    # Under Indian cut-offs, 23.0 to 24.9 is Overweight (Standard WHO is normal)
    payload_overweight = {"height": 175.0, "weight": 72.0}
    response_over = client.post("/profile/vitals", json=payload_overweight)
    assert response_over.status_code == 201
    assert response_over.json()["category"] == "Overweight"

    # Log Obese for Indian context (height 175cm, weight 80kg -> BMI ~ 26.12 >= 25.0)
    payload_obese = {"height": 175.0, "weight": 80.0}
    response_obese = client.post("/profile/vitals", json=payload_obese)
    assert response_obese.status_code == 201
    assert response_obese.json()["category"] == "Obese"

    # Retrieve history
    history_resp = client.get("/profile/vitals/history")
    assert history_resp.status_code == 200
    assert len(history_resp.json()) == 3

def test_qr_passport(client, mock_db, mock_user):
    mock_db.db_store[settings.USERS_COLLECTION] = {
        mock_user["uid"]: {
            "uid": mock_user["uid"],
            "name": "Arjun Kumar"
        }
    }
    mock_db.db_store[settings.PATIENTS_COLLECTION] = {
        mock_user["uid"]: {
            "blood_group": "A-",
            "allergies": ["Shellfish"],
            "chronic_conditions": ["Hypertension"],
            "current_medications": ["Amlodipine 5mg"],
            "meal_times": {},
            "emergency_contact": {"name": "Wife", "phone": "+911234567890"}
        }
    }
    response = client.get("/profile/passport")
    assert response.status_code == 200
    data = response.json()
    assert data["name"] == "Arjun Kumar"
    assert data["blood_group"] == "A-"
    assert "Shellfish" in data["allergies"]
    assert data["emergency_contact"]["name"] == "Wife"
    assert "qr_redirect_url" in data
    assert f"/profile/sos/{mock_user['uid']}" in data["qr_redirect_url"]

def test_patient_onboarding_skip(client, mock_db, mock_user):
    # Initialize basic registration status in DB
    mock_db.db_store[settings.USERS_COLLECTION] = {
        mock_user["uid"]: {
            "uid": mock_user["uid"],
            "name": "Arjun Kumar",
            "phone": None,
            "email": "arjun@example.com",
            "role": "patient",
            "language_preference": "en",
            "onboarding_status": "pending"
        }
    }
    mock_db.db_store[settings.PATIENTS_COLLECTION] = {
        mock_user["uid"]: {
            "meal_times": {},
            "onboarding_status": "pending"
        }
    }

    payload = {"skip": True}
    response = client.post("/profile/onboard", json=payload)
    assert response.status_code == 200
    data = response.json()
    assert data["onboarding_status"] == "skipped"
    assert data["user"]["onboarding_status"] == "skipped"
    assert data["profile"]["onboarding_status"] == "skipped"

    # Verify db stores
    assert mock_db.db_store[settings.USERS_COLLECTION][mock_user["uid"]]["onboarding_status"] == "skipped"
    assert mock_db.db_store[settings.PATIENTS_COLLECTION][mock_user["uid"]]["onboarding_status"] == "skipped"

def test_patient_onboarding_continue_success(client, mock_db, mock_user):
    # Initialize basic registration status in DB
    mock_db.db_store[settings.USERS_COLLECTION] = {
        mock_user["uid"]: {
            "uid": mock_user["uid"],
            "name": "Arjun Kumar",
            "phone": None,
            "email": "arjun@example.com",
            "role": "patient",
            "language_preference": "en",
            "onboarding_status": "pending"
        }
    }
    mock_db.db_store[settings.PATIENTS_COLLECTION] = {
        mock_user["uid"]: {
            "meal_times": {},
            "onboarding_status": "pending"
        }
    }

    payload = {
        "skip": False,
        "name": "Arjun Kumar Updated",
        "phone": "+919876543210",
        "language_preference": "hi",
        "date_of_birth": "1990-05-15",
        "gender": "Male",
        "location": "Delhi, India",
        "blood_group": "B+",
        "allergies": ["Penicillin"],
        "chronic_conditions": [],
        "current_medications": [],
        "height": 180.0,
        "weight": 75.0,
        "meal_times": {
            "breakfast": "08:00",
            "lunch": "13:00",
            "dinner": "20:00"
        },
        "emergency_contact": {"name": "Brother", "phone": "+919999988888"}
    }
    
    response = client.post("/profile/onboard", json=payload)
    assert response.status_code == 200
    data = response.json()
    assert data["onboarding_status"] == "completed"
    assert data["user"]["onboarding_status"] == "completed"
    assert data["profile"]["onboarding_status"] == "completed"
    assert data["profile"]["gender"] == "Male"
    assert data["profile"]["blood_group"] == "B+"
    assert "Penicillin" in data["profile"]["allergies"]

    # Check that vitals were logged & height/weight updated in db
    patient_doc = mock_db.db_store[settings.PATIENTS_COLLECTION][mock_user["uid"]]
    assert patient_doc["height"] == 180.0
    assert patient_doc["weight"] == 75.0
    assert patient_doc["onboarding_status"] == "completed"

def test_patient_onboarding_continue_validation_errors(client, mock_db, mock_user):
    # Initialize basic registration status in DB
    mock_db.db_store[settings.USERS_COLLECTION] = {
        mock_user["uid"]: {
            "uid": mock_user["uid"],
            "name": "Arjun Kumar",
            "onboarding_status": "pending"
        }
    }
    mock_db.db_store[settings.PATIENTS_COLLECTION] = {
        mock_user["uid"]: {
            "meal_times": {},
            "onboarding_status": "pending"
        }
    }

    # Missing date_of_birth and gender
    payload_missing = {
        "skip": False,
        "phone": "+919876543210",
        "allergies": [],
        "height": 180.0,
        "weight": 75.0
    }
    response = client.post("/profile/onboard", json=payload_missing)
    assert response.status_code == 400
    assert "missing" in response.json()["detail"].lower()

    # Invalid DOB format
    payload_invalid_dob = {
        "skip": False,
        "phone": "+919876543210",
        "date_of_birth": "15-05-1990", # invalid
        "gender": "Male",
        "allergies": [],
        "height": 180.0,
        "weight": 75.0
    }
    response_dob = client.post("/profile/onboard", json=payload_invalid_dob)
    assert response_dob.status_code == 400
    assert "format" in response_dob.json()["detail"].lower()

    # Future DOB
    payload_future_dob = {
        "skip": False,
        "phone": "+919876543210",
        "date_of_birth": "2050-01-01", # future
        "gender": "Male",
        "allergies": [],
        "height": 180.0,
        "weight": 75.0
    }
    response_future = client.post("/profile/onboard", json=payload_future_dob)
    assert response_future.status_code == 400
    assert "future" in response_future.json()["detail"].lower()

def test_update_fcm_token(client, mock_db, mock_user):
    mock_db.db_store[settings.USERS_COLLECTION] = {
        mock_user["uid"]: {
            "uid": mock_user["uid"],
            "name": "Arjun Kumar",
            "fcm_tokens": {}
        }
    }

    # Register iOS token
    payload_ios = {"fcm_token": "mock-fcm-token-ios", "platform": "ios"}
    response_ios = client.post("/profile/fcm-token", json=payload_ios)
    assert response_ios.status_code == 200
    assert response_ios.json()["success"] is True

    user_record = mock_db.db_store[settings.USERS_COLLECTION][mock_user["uid"]]
    # Stored under fcm_tokens.ios, not a flat key
    assert user_record.get("fcm_tokens", {}).get("ios") == "mock-fcm-token-ios"

    # Register Android token for the same user (both should coexist)
    payload_android = {"fcm_token": "mock-fcm-token-android", "platform": "android"}
    response_android = client.post("/profile/fcm-token", json=payload_android)
    assert response_android.status_code == 200
    assert response_android.json()["success"] is True

    user_record = mock_db.db_store[settings.USERS_COLLECTION][mock_user["uid"]]
    assert user_record.get("fcm_tokens", {}).get("ios") == "mock-fcm-token-ios"
    assert user_record.get("fcm_tokens", {}).get("android") == "mock-fcm-token-android"


def test_public_sos_landing_page(client, mock_db, mock_user):
    mock_db.db_store[settings.USERS_COLLECTION] = {
        "emergency-patient-abc": {
            "uid": "emergency-patient-abc",
            "name": "Emergency Patient"
        }
    }
    mock_db.db_store[settings.PATIENTS_COLLECTION] = {
        "emergency-patient-abc": {
            "blood_group": "AB-",
            "allergies": ["Nuts"],
            "chronic_conditions": ["Diabetes"],
            "current_medications": ["Insulin"],
            "meal_times": {},
            "emergency_contact": {"name": "Father", "phone": "+919999900000"}
        }
    }
    
    response = client.get("/profile/sos/emergency-patient-abc")
    assert response.status_code == 200
    assert response.headers["content-type"] == "text/html; charset=utf-8"
    html_text = response.text
    assert "Emergency Patient" in html_text
    assert "AB-" in html_text
    assert "Nuts" in html_text
    assert "Diabetes" in html_text
    assert "Insulin" in html_text
    assert "Father" in html_text
    assert "+919999900000" in html_text

