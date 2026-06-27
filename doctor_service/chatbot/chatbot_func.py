from google.cloud import firestore
from common_code.config import settings
from common_code.gcp_clients import generate_gemini_content
from doctor_service.patients.patients_func import validate_active_session_consent
from doctor_service.chatbot.chatbot_model import DoctorChatResponse

async def answer_doctor_consult_query(
    doctor_uid: str,
    patient_id: str,
    prompt: str,
    db: firestore.AsyncClient
) -> DoctorChatResponse:
    """
    Formulates a grounded clinical answer for the doctor using details extracted from the
    open patient profile and consultation timeline histories.
    """
    is_authorized = await validate_active_session_consent(doctor_uid, patient_id, db)
    if not is_authorized:
        raise PermissionError("Access unauthorized. Consent session has expired or is invalid.")
        
    # 1. Fetch patient profile details
    pat_snap = await db.collection(settings.PATIENTS_COLLECTION).document(patient_id).get()
    if not pat_snap.exists:
        raise ValueError("Patient medical profile details not found.")
    p = pat_snap.to_dict()
    
    # 2. Fetch past consultations summaries
    consult_docs = await db.collection(settings.CONSULTATIONS_COLLECTION) \
        .where("patientId", "==", patient_id) \
        .get()
        
    consults_summary = []
    for doc in consult_docs:
        d = doc.to_dict()
        if d.get("status") == "published":
            consults_summary.append(
                f"Date: {d.get('createdAt')}\n"
                f"Diagnoses: {d.get('diagnoses')}\n"
                f"Medicines Prescribed: {d.get('medicines')}\n"
                f"Doctor Summary: {d.get('summary_en')}\n"
            )
            
    history_str = "\n---\n".join(consults_summary) if consults_summary else "No historical consultation records found."
    
    # 3. Consolidate background clinical data
    background = (
        f"Allergies: {p.get('allergies', [])}\n"
        f"Chronic Conditions: {p.get('chronic_conditions', [])}\n"
        f"Ongoing Medications: {p.get('current_medications', [])}\n"
        f"Past Surgeries: {p.get('past_surgeries', [])}\n"
        f"Family Medical History: {p.get('family_history', [])}\n"
        f"Height: {p.get('height')} cm, Weight: {p.get('weight')} kg\n"
    )
    
    # 4. Construct Gemini prompt grounded for physician utility
    gemini_prompt = (
        "You are a clinical decision support assistant. You are assisting a doctor in analyzing a patient's chart. "
        "Your responses should be precise, clear, and clinical. Do not explain basic terms. "
        "Focus on safety, correlations, and treatment history details.\n\n"
        f"--- Patient Clinical Profile ---\n{background}\n"
        f"--- Historical Consultation Records ---\n{history_str}\n\n"
        f"--- Doctor's Query ---\n{prompt}\n\n"
        "Assistant Response:"
    )
    
    reply = generate_gemini_content(gemini_prompt, json_response=False)
    
    return DoctorChatResponse(reply=reply)
