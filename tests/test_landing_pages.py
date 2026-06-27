import pytest
from fastapi.testclient import TestClient
from patient_service.app import app as patient_app
from doctor_service.app import app as doctor_app

def test_patient_service_landing_page():
    # Use a clean test client to test the patient root endpoint
    with TestClient(patient_app) as test_client:
        response = test_client.get("/")
        assert response.status_code == 200
        assert "text/html" in response.headers["content-type"]
        
        # Verify custom HTML tags/content are present
        html = response.text
        assert "AI Health Companion" in html
        assert "Patient Backend Service" in html
        assert "Swagger API Docs" in html
        assert "ReDoc Specs" in html
        assert "Environment" in html

def test_doctor_service_landing_page():
    # Use a clean test client to test the doctor root endpoint
    with TestClient(doctor_app) as test_client:
        response = test_client.get("/")
        assert response.status_code == 200
        assert "text/html" in response.headers["content-type"]
        
        # Verify custom HTML tags/content are present
        html = response.text
        assert "AI Health Companion" in html
        assert "Doctor Backend Service" in html
        assert "Swagger API Docs" in html
        assert "ReDoc Specs" in html
        assert "Environment" in html
