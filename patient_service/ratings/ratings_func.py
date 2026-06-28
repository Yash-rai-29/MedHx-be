import datetime
from datetime import UTC
from google.cloud import firestore
from common_code.config import settings
from patient_service.ratings.ratings_model import RatingCreateRequest, RatingResponse

async def create_doctor_rating(
    patient_id: str,
    req: RatingCreateRequest,
    db: firestore.AsyncClient
) -> RatingResponse:
    """
    Submits a consultation rating for a doctor and updates the doctor's aggregate stats.
    """
    # Verify the consultation exists and belongs to this patient
    consult_ref = db.collection(settings.CONSULTATIONS_COLLECTION).document(req.consultation_id)
    consult_snap = await consult_ref.get()
    
    if not consult_snap.exists:
        raise ValueError("Associated consultation not found.")
        
    c_data = consult_snap.to_dict()
    if c_data.get("patientId") != patient_id:
        raise PermissionError("You can only rate your own consultations.")
        
    if c_data.get("doctorId") != req.doctor_id:
        raise ValueError("Consultation is not associated with this doctor.")
        
    # Check if a rating already exists for this consultation to prevent duplicates
    existing_ratings = await db.collection(settings.RATINGS_COLLECTION) \
        .where("consultation_id", "==", req.consultation_id) \
        .limit(1) \
        .get()
        
    if len(existing_ratings) > 0:
        raise ValueError("A rating has already been submitted for this consultation.")
        
    # Create rating document
    created_at = datetime.datetime.now(UTC)
    rating_doc = {
        "patient_id": patient_id,
        "doctor_id": req.doctor_id,
        "consultation_id": req.consultation_id,
        "stars": req.stars,
        "comments": req.comments,
        "created_at": created_at
    }
    
    doc_ref = await db.collection(settings.RATINGS_COLLECTION).add(rating_doc)
    rating_id = doc_ref[1].id
    
    # Update doctor's aggregate rating/count
    await update_doctor_aggregate_ratings(req.doctor_id, db)
    
    return RatingResponse(
        id=rating_id,
        patient_id=patient_id,
        doctor_id=req.doctor_id,
        consultation_id=req.consultation_id,
        stars=req.stars,
        comments=req.comments,
        created_at=created_at
    )

async def get_patient_ratings_history(patient_id: str, db: firestore.AsyncClient) -> list[RatingResponse]:
    """Retrieves all feedback ratings submitted by a patient."""
    docs = await db.collection(settings.RATINGS_COLLECTION) \
        .where("patient_id", "==", patient_id) \
        .order_by("created_at", direction=firestore.Query.DESCENDING) \
        .get()
        
    ratings = []
    for doc in docs:
        d = doc.to_dict()
        ratings.append(RatingResponse(
            id=doc.id,
            patient_id=d["patient_id"],
            doctor_id=d["doctor_id"],
            consultation_id=d["consultation_id"],
            stars=d["stars"],
            comments=d.get("comments"),
            created_at=d["created_at"]
        ))
    return ratings

async def update_doctor_aggregate_ratings(doctor_id: str, db: firestore.AsyncClient):
    """Re-calculates and updates average rating and ratings count for a doctor."""
    ratings_query = await db.collection(settings.RATINGS_COLLECTION) \
        .where("doctor_id", "==", doctor_id) \
        .get()
        
    total_stars = 0
    count = len(ratings_query)
    
    if count > 0:
        for r_doc in ratings_query:
            total_stars += r_doc.to_dict().get("stars", 0)
        average = round(total_stars / count, 2)
    else:
        average = 5.0  # Default baseline
        
    await db.collection(settings.DOCTORS_COLLECTION).document(doctor_id).update({
        "average_rating": average,
        "ratings_count": count
    })
