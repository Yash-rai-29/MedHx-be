from google.cloud import firestore
from common_code.config import settings
from common_code.safety_engine import run_safety_checks
from doctor_service.patients.patients_func import validate_active_session_consent
from doctor_service.medication_safety.safety_model import (
    MedicineEntry,
    SafetyVerifyResponse,
    AllergyConflictDetail,
    DrugInteractionDetail,
    DuplicateTherapyDetail,
    DosingWarningDetail
)

async def check_medication_safety(
    doctor_uid: str,
    patient_id: str,
    medicines: list[MedicineEntry],
    db: firestore.AsyncClient
) -> SafetyVerifyResponse:
    """Runs all safety rules for the proposed list of prescribed medicines for this patient."""
    is_authorized = await validate_active_session_consent(doctor_uid, patient_id, db)
    if not is_authorized:
        raise PermissionError("Access unauthorized. Consent session has expired or is invalid.")
        
    # Get patient profile details (allergies, height, weight, current meds)
    pat_doc = await db.collection(settings.PATIENTS_COLLECTION).document(patient_id).get()
    if not pat_doc.exists:
        raise ValueError("Patient medical profile details not found.")
    patient_profile = pat_doc.to_dict()
    
    # Format prescribed medicines for the safety engine
    med_dicts = []
    for med in medicines:
        # Resolve mg values roughly if possible for paracetamol dosing warning
        mg_value = None
        name_lower = med.name.lower()
        if "650" in name_lower:
            mg_value = 650
        elif "500" in name_lower:
            mg_value = 500
            
        med_dicts.append({
            "name": med.name,
            "dosage": med.dosage,
            "meal_relation": med.meal_relation,
            "duration_days": med.duration_days,
            "mg_value": mg_value
        })
        
    # Execute checks
    results = await run_safety_checks(db, patient_id, med_dicts, patient_profile)
    
    # Map raw lists into typed pydantic details
    allergy_conflicts = [AllergyConflictDetail(**c) for c in results["allergy_conflicts"]]
    drug_interactions = [DrugInteractionDetail(**i) for i in results["drug_interactions"]]
    duplicate_therapies = [DuplicateTherapyDetail(**d) for d in results["duplicate_therapies"]]
    dosing_warnings = [DosingWarningDetail(**w) for w in results["dosing_warnings"]]
    
    return SafetyVerifyResponse(
        is_safe=results["is_safe"],
        allergy_conflicts=allergy_conflicts,
        drug_interactions=drug_interactions,
        duplicate_therapies=duplicate_therapies,
        dosing_warnings=dosing_warnings
    )
