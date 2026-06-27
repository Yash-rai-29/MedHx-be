import sys
import os

# Add workspace directory to python path
sys.path.insert(0, os.path.abspath(os.path.dirname(__file__)))

print("--- VERIFYING IMPORTS & SCHEMAS ---")

try:
    from common_code.config import settings
    print("✓ common_code.config imported successfully.")
    
    from common_code.firestore import get_db, log_audit_event
    print("✓ common_code.firestore imported successfully.")
    
    from common_code.firebase_auth import get_current_user, require_role
    print("✓ common_code.firebase_auth imported successfully.")
    
    from common_code.gcp_clients import (
        generate_signed_upload_url,
        transcribe_audio,
        parse_medical_document,
        translate_text,
        synthesize_speech,
        generate_gemini_content
    )
    print("✓ common_code.gcp_clients imported successfully.")
    
    from common_code.safety_engine import run_safety_checks
    print("✓ common_code.safety_engine imported successfully.")
    
    # ------------------ Patient Service ------------------
    from patient_service.app import app as patient_app
    print("✓ patient_service.app successfully initialized.")
    
    from patient_service.auth.auth_model import PatientRegisterRequest
    from patient_service.profile.profile_model import PatientProfileUpdateRequest
    from patient_service.profile.profile_func import compute_indian_bmi_category
    
    # Verify BMI calculations
    assert compute_indian_bmi_category(22.5) == "Normal"
    assert compute_indian_bmi_category(24.0) == "Overweight"
    assert compute_indian_bmi_category(26.0) == "Obese"
    print("✓ compute_indian_bmi_category logic verified.")
    
    # ------------------ Doctor Service ------------------
    from doctor_service.app import app as doctor_app
    print("✓ doctor_service.app successfully initialized.")
    
    from doctor_service.auth.auth_model import DoctorRegisterRequest
    from doctor_service.consultations.consultations_model import StartConsultationRequest
    from doctor_service.medication_safety.safety_model import SafetyVerifyRequest
    
    print("\nALL SERVICES COMPILED AND IMPORTED SUCCESSFULLY!")
    sys.exit(0)

except Exception as e:
    import traceback
    print("\nFAIL: An error occurred during compilation checks:")
    traceback.print_exc()
    sys.exit(1)
