from typing import List, Dict, Any
from common_code.config import settings

# Curated reference drug interaction database for robust fallback
MOCK_DRUG_KNOWLEDGE = {
    "aspirin": {
        "interactions": {
            "warfarin": "High risk of severe bleeding. Concomitant use is contraindicated without close monitoring.",
            "ibuprofen": "May decrease cardioprotective effect of aspirin and increase GI bleeding risk."
        },
        "contraindications": ["active bleeding", "hemophilia"],
        "dosing": {"max_daily_mg": 4000, "pediatric_warning": True}
    },
    "warfarin": {
        "interactions": {
            "aspirin": "High risk of severe bleeding.",
            "nsaids": "Increased bleeding risk."
        },
        "contraindications": ["pregnancy", "severe uncontrolled hypertension"],
        "dosing": {"max_daily_mg": 10, "pediatric_warning": True}
    },
    "amoxicillin": {
        "interactions": {
            "methotrexate": "May increase methotrexate toxicity. Monitor levels closely."
        },
        "contraindications": ["penicillin allergy"],
        "dosing": {"pediatric_warning": False}
    },
    "paracetamol": {
        "interactions": {
            "alcohol": "Increased risk of hepatotoxicity with chronic heavy alcohol consumption."
        },
        "contraindications": ["severe hepatic impairment"],
        "dosing": {"max_daily_mg": 4000, "pediatric_warning": False}
    }
}

async def run_safety_checks(
    db: Any,
    patient_id: str,
    prescribed_medicines: List[Dict[str, Any]],
    patient_profile: Dict[str, Any]
) -> Dict[str, Any]:
    """
    Orchestrates and returns the result of duplicate therapy, drug-drug interaction,
    allergy conflict, and age/weight dosing validation checks.
    
    Args:
        db: Firestore AsyncClient.
        patient_id: Firestore ID of the patient.
        prescribed_medicines: List of dicts representing new medicines to be prescribed.
            Example: [{"name": "Aspirin", "dosage": "75mg", "mg_value": 75}]
        patient_profile: Firestore patient document data.
    """
    allergies = patient_profile.get("allergies", [])
    current_meds = patient_profile.get("current_medications", [])
    age = patient_profile.get("age") or 30 # default
    weight = patient_profile.get("weight") or 60.0 # default
    
    # Run individual checks
    allergy_conflicts = check_allergy_conflicts(allergies, prescribed_medicines)
    drug_interactions = await check_drug_interactions(db, current_meds, prescribed_medicines)
    duplicate_therapies = check_duplicate_therapies(current_meds, prescribed_medicines)
    dosing_warnings = check_dosing_warnings(age, weight, prescribed_medicines)
    
    is_safe = not (allergy_conflicts or drug_interactions or duplicate_therapies or dosing_warnings)
    
    return {
        "is_safe": is_safe,
        "allergy_conflicts": allergy_conflicts,
        "drug_interactions": drug_interactions,
        "duplicate_therapies": duplicate_therapies,
        "dosing_warnings": dosing_warnings
    }


def check_allergy_conflicts(allergies: List[str], prescribed_medicines: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Checks prescribed medicines against patient allergies."""
    conflicts = []
    normalized_allergies = [a.lower().strip() for a in allergies]
    
    for med in prescribed_medicines:
        med_name = med.get("name", "").lower().strip()
        
        # Exact match or substring match (e.g. "penicillin" in drug name "amoxicillin (penicillin family)")
        for allergy in normalized_allergies:
            if allergy in med_name or med_name in allergy:
                conflicts.append({
                    "medicine_name": med.get("name"),
                    "allergy": allergy,
                    "severity": "CRITICAL",
                    "message": f"Patient is allergic to '{allergy}'. Prescribing '{med.get('name')}' is dangerous."
                })
            # Special check for penicillin class
            elif "penicillin" in allergy and "amox" in med_name:
                conflicts.append({
                    "medicine_name": med.get("name"),
                    "allergy": allergy,
                    "severity": "CRITICAL",
                    "message": f"Cross-reactivity warning: Patient allergic to '{allergy}', medicine '{med.get('name')}' is in the penicillin class."
                })
    return conflicts


async def check_drug_interactions(
    db: Any,
    current_meds: List[str],
    prescribed_medicines: List[Dict[str, Any]]
) -> List[Dict[str, Any]]:
    """Checks newly prescribed medicines against ongoing current medicines."""
    interactions = []
    
    # Consolidate list of drugs to check
    all_ongoing_normalized = [m.lower().strip() for m in current_meds]
    
    for med in prescribed_medicines:
        new_med_name = med.get("name", "").lower().strip()
        
        for ongoing in all_ongoing_normalized:
            # Check Firestore first, otherwise fallback to local dictionary
            message = None
            try:
                # Firestore query helper
                doc = await db.collection(settings.DRUG_KNOWLEDGE_COLLECTION).document(new_med_name).get()
                if doc.exists:
                    drug_info = doc.to_dict()
                    message = drug_info.get("interactions", {}).get(ongoing)
            except Exception:
                pass # Fallback to local
                
            if not message:
                # Check local dict
                if new_med_name in MOCK_DRUG_KNOWLEDGE:
                    message = MOCK_DRUG_KNOWLEDGE[new_med_name]["interactions"].get(ongoing)
                elif ongoing in MOCK_DRUG_KNOWLEDGE:
                    message = MOCK_DRUG_KNOWLEDGE[ongoing]["interactions"].get(new_med_name)
                    
            if message:
                interactions.append({
                    "medicine_a": med.get("name"),
                    "medicine_b": ongoing,
                    "severity": "HIGH",
                    "message": message
                })
    return interactions


def check_duplicate_therapies(current_meds: List[str], prescribed_medicines: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Flags if doctor is prescribing something the patient is already taking."""
    duplicates = []
    all_ongoing_normalized = [m.lower().strip() for m in current_meds]
    
    # Class mapping for generic duplicates
    generic_groups = {
        "paracetamol": ["paracetamol", "acetaminophen", "calpol", "dolo"],
        "ibuprofen": ["ibuprofen", "advil", "motrin", "brufen"],
        "amoxicillin": ["amoxicillin", "mox", "clavum"]
    }
    
    def get_generic_class(name: str) -> str:
        name_lower = name.lower()
        for key, aliases in generic_groups.items():
            if key in name_lower or any(alias in name_lower for alias in aliases):
                return key
        return name_lower
        
    for med in prescribed_medicines:
        new_med_name = med.get("name", "")
        new_class = get_generic_class(new_med_name)
        
        for ongoing in all_ongoing_normalized:
            ongoing_class = get_generic_class(ongoing)
            if new_class == ongoing_class:
                duplicates.append({
                    "medicine_name": new_med_name,
                    "existing_medicine": ongoing,
                    "severity": "MEDIUM",
                    "message": f"Duplicate therapy detected: Prescribed '{new_med_name}' belongs to the same therapeutic class as current medicine '{ongoing}'."
                })
    return duplicates


def check_dosing_warnings(age: float, weight: float, prescribed_medicines: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Enforces clinical age/weight limit warnings."""
    warnings = []
    
    for med in prescribed_medicines:
        med_name = med.get("name", "").lower()
        
        # Pediatric checks
        if age < 12:
            # Check pediatric warning
            warning_needed = False
            if "aspirin" in med_name:
                warning_needed = True
                msg = f"Aspirin contraindicated in patients under 12 years of age due to risk of Reye's syndrome."
            elif "warfarin" in med_name:
                warning_needed = True
                msg = f"Warfarin requires extremely strict specialized dosing and monitoring in pediatric patients."
                
            if warning_needed:
                warnings.append({
                    "medicine_name": med.get("name"),
                    "type": "PEDIATRIC_WARNING",
                    "severity": "HIGH",
                    "message": msg
                })
                
        # Dosing limit check by weight (e.g. Paracetamol max single dose or daily limits)
        if "paracetamol" in med_name or "dolo" in med_name:
            # If weight is small, e.g. < 40 kg, check if the single dose is too high
            mg_val = med.get("mg_value") or 650 # default to 650mg
            if weight < 40.0 and mg_val > 500:
                warnings.append({
                    "medicine_name": med.get("name"),
                    "type": "DOSING_LIMIT",
                    "severity": "MEDIUM",
                    "message": f"Patient weight is low ({weight} kg). Single dose of Paracetamol {mg_val}mg exceeds the recommended pediatric threshold of 500mg."
                })
    return warnings
