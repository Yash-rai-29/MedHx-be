from pydantic import BaseModel, Field, model_validator
from typing import Any, Dict, List, Optional
from datetime import datetime
from enum import Enum

from patient_service.auth.auth_model import AccountStatus

class PlatformEnum(str, Enum):
    ios = "ios"
    android = "android"
    web = "web"

class MealTimes(BaseModel):
    breakfast: str = Field("08:30", description="Breakfast time in 24hr format (HH:MM)")
    lunch: str = Field("13:30", description="Lunch time in 24hr format (HH:MM)")
    dinner: str = Field("20:30", description="Dinner time in 24hr format (HH:MM)")

class EmergencyContact(BaseModel):
    name: str = Field(..., description="Full name of the emergency contact person")
    phone: str = Field(..., description="Phone number of the emergency contact person")

class PatientProfileResponse(BaseModel):
    # User account properties
    uid: Optional[str] = Field(None, description="Unique Firebase User ID")
    name: Optional[str] = Field(None, description="Patient's full name")
    country_code: Optional[str] = Field(None, description="Patient's country calling code")
    phone_number: Optional[str] = Field(None, description="Patient's phone number without country code")
    email: Optional[str] = Field(None, description="Patient's email address")
    role: Optional[str] = Field(None, description="Access role for user, e.g. patient")
    language_preference: Optional[str] = Field(None, description="Preferred app interface language (en, hi, ta, te)")
    auth_provider: Optional[str] = Field(None, description="Auth provider like google.com, password")
    accepted_privacy_policy: Optional[bool] = Field(None, description="Whether the user accepted the privacy policy")
    accepted_terms_of_service: Optional[bool] = Field(None, description="Whether the user accepted the terms of service")
    account_status: Optional[AccountStatus] = Field(AccountStatus.active, description="Current account state: 'active' or 'pending_deletion'")
    deletion_scheduled_at: Optional[datetime] = Field(None, description="Timestamp when account is scheduled for permanent purge if deletion requested")

    # Clinical profile properties
    blood_group: Optional[str] = Field(None, description="Blood group (e.g. A+, O-)")
    allergies: List[str] = Field(default=[], description="List of drug, food, or general allergies")
    chronic_conditions: List[str] = Field(default=[], description="List of current chronic conditions (e.g. Hypertension, Diabetes)")
    current_medications: List[str] = Field(default=[], description="List of current ongoing medications")
    past_surgeries: List[str] = Field(default=[], description="List of past surgical history")
    family_history: List[str] = Field(default=[], description="Family medical history details")
    meal_times: MealTimes = Field(..., description="Default/configured breakfast, lunch, and dinner meal timings")
    emergency_contact: Optional[EmergencyContact] = Field(None, description="Emergency contact details")
    age: Optional[int] = Field(None, description="Calculated age of the patient")
    gender: Optional[str] = Field(None, description="Patient's gender")
    date_of_birth: Optional[str] = Field(None, description="Patient's date of birth in YYYY-MM-DD format")
    location: Optional[str] = Field(None, description="Patient's current location/region")
    onboarding_status: str = Field("pending", description="Onboarding status, e.g. completed, skipped, pending")

    @model_validator(mode="before")
    @classmethod
    def _populate_phone_fields(cls, data: Any) -> Any:
        if isinstance(data, dict):
            if not data.get("phone_number") and data.get("phone"):
                data["phone_number"] = data.get("phone")
        return data


class PatientProfileUpdateRequest(BaseModel):
    blood_group: Optional[str] = Field(None, description="Patient's blood group (e.g. A+, O-)")
    allergies: Optional[List[str]] = Field(None, description="Updated list of food, drug, or other allergies")
    chronic_conditions: Optional[List[str]] = Field(None, description="Updated list of ongoing chronic illnesses")
    current_medications: Optional[List[str]] = Field(None, description="Updated list of current medications")
    past_surgeries: Optional[List[str]] = Field(None, description="Updated list of past surgeries")
    family_history: Optional[List[str]] = Field(None, description="Updated family medical history")
    meal_times: Optional[MealTimes] = Field(None, description="Updated breakfast, lunch, or dinner timing rules")
    emergency_contact: Optional[EmergencyContact] = Field(None, description="Updated emergency contact information")
    age: Optional[int] = Field(None, description="Updated age")
    gender: Optional[str] = Field(None, description="Updated gender selection")
    date_of_birth: Optional[str] = Field(None, description="Updated date of birth (YYYY-MM-DD)")
    location: Optional[str] = Field(None, description="Updated location/region details")

class PatientOnboardingRequest(BaseModel):
    skip: bool = Field(..., description="If True, skip onboarding and make clinical fields optional. If False, enforce required fields.")
    
    # User demographic details
    name: Optional[str] = Field(None, description="Patient's full name")
    country_code: Optional[str] = Field(None, description="Country calling code (e.g. '+91')")
    phone_number: Optional[str] = Field(None, description="Phone number without country code")
    language_preference: Optional[str] = Field(None, description="Preferred language (en, hi, ta, te)")
    
    # Clinical/background details
    date_of_birth: Optional[str] = Field(None, description="Date of birth (YYYY-MM-DD)")
    gender: Optional[str] = Field(None, description="Gender selection")
    location: Optional[str] = Field(None, description="Location/Region")
    blood_group: Optional[str] = Field(None, description="Blood group")
    allergies: Optional[List[str]] = Field(None, description="List of food/drug allergies")
    chronic_conditions: Optional[List[str]] = Field(None, description="List of chronic conditions")
    current_medications: Optional[List[str]] = Field(None, description="List of current medications")
    past_surgeries: Optional[List[str]] = Field(None, description="List of past surgeries")
    family_history: Optional[List[str]] = Field(None, description="Family medical history")
    meal_times: Optional[MealTimes] = Field(None, description="Meal timings configuration")
    emergency_contact: Optional[EmergencyContact] = Field(None, description="Emergency contact details")
    
    # Vitals
    height: Optional[float] = Field(None, description="Height in cm")
    weight: Optional[float] = Field(None, description="Weight in kg")

class UserOnboardResponse(BaseModel):
    uid: str = Field(..., description="Unique Firebase User ID")
    name: str = Field(..., description="Patient's full name")
    country_code: Optional[str] = Field(None, description="Country calling code")
    phone_number: Optional[str] = Field(None, description="Phone number without country code")
    email: Optional[str] = Field(None, description="Patient's email address")
    role: str = Field(..., description="Access control role (patient)")
    language_preference: str = Field(..., description="Language preference selection (en, hi, ta, te)")
    onboarding_status: str = Field(..., description="Onboarding status: completed, skipped")

    @model_validator(mode="before")
    @classmethod
    def _populate_phone_fields(cls, data: Any) -> Any:
        if isinstance(data, dict):
            if not data.get("phone_number") and data.get("phone"):
                data["phone_number"] = data.get("phone")
        return data

class OnboardingResponse(BaseModel):
    onboarding_status: str = Field(..., description="Final onboarding state: completed, skipped")
    profile: Optional[PatientProfileResponse] = Field(None, description="Newly updated patient clinical profile")
    user: Optional[UserOnboardResponse] = Field(None, description="Newly updated user metadata")

class VitalsLogRequest(BaseModel):
    height: float = Field(..., gt=0, description="Height in cm")
    weight: float = Field(..., gt=0, description="Weight in kg")

class VitalsLogResponse(BaseModel):
    id: str = Field(..., description="Unique document ID of the logged vitals entry")
    height: float = Field(..., description="Logged height in cm")
    weight: float = Field(..., description="Logged weight in kg")
    bmi: float = Field(..., description="Calculated Body Mass Index")
    category: str = Field(..., description="BMI category according to Indian context standards")
    recorded_at: datetime = Field(..., description="Timestamp when the vitals were recorded")

class QRPassportResponse(BaseModel):
    name: str = Field(..., description="Patient's full name")
    email: Optional[str] = Field(None, description="Patient's email address")
    blood_group: Optional[str] = Field(None, description="Patient's blood group")
    allergies: List[str] = Field(..., description="List of patient's documented allergies")
    chronic_conditions: List[str] = Field(..., description="List of patient's documented chronic illnesses")
    current_medications: List[str] = Field(..., description="List of patient's current medications")
    emergency_contact: Optional[EmergencyContact] = Field(None, description="Emergency contact details")
    token: str = Field(..., description="Cryptographically signed 30-minute access token")
    token_expires_at: datetime = Field(..., description="UTC expiration timestamp of the token")
    validity_minutes: int = Field(30, description="Validity duration in minutes")
    qr_redirect_url: str = Field(..., description="Dynamic URL pointing to frontend web app doctor view")

class DoctorViewVerifyRequest(BaseModel):
    token: str = Field(..., description="30-minute time-limited QR access token")
    email: str = Field(..., description="Patient email address for identity verification")

class DoctorConsultationSummary(BaseModel):
    id: str = Field(..., description="Consultation document ID")
    title: Optional[str] = Field(None, description="Consultation encounter title")
    doctor_name: Optional[str] = Field(None, description="Attending physician / specialist")
    date: Optional[str] = Field(None, description="Encounter date")
    summary: Optional[str] = Field(None, description="Clinical summary and findings")
    diagnoses: List[str] = Field(default_factory=list, description="Primary & secondary diagnoses")
    medications: List[Any] = Field(default_factory=list, description="Prescribed medications")

class DoctorDocumentSummary(BaseModel):
    id: str = Field(..., description="Document ID")
    title: Optional[str] = Field(None, description="Document / lab test title")
    type: Optional[str] = Field(None, description="Document type (lab_report, prescription, scan)")
    date: Optional[str] = Field(None, description="Report date")
    summary: Optional[str] = Field(None, description="OCR and clinical summary")
    abnormal_labs: List[Any] = Field(default_factory=list, description="Abnormal lab markers")

class DoctorViewResponse(BaseModel):
    valid: bool = Field(True, description="Indicates if token is valid and active")
    patient_id: str = Field(..., description="Patient UID")
    name: str = Field(..., description="Patient's full name")
    email: Optional[str] = Field(None, description="Patient's email address")
    phone: Optional[str] = Field(None, description="Patient's phone number")
    age: Optional[int] = Field(None, description="Patient's age in years")
    gender: Optional[str] = Field(None, description="Patient's gender")
    blood_group: Optional[str] = Field(None, description="Patient's blood group")
    emergency_contact: Optional[EmergencyContact] = Field(None, description="Emergency contact details")
    allergies: List[str] = Field(default_factory=list, description="Allergies")
    chronic_conditions: List[str] = Field(default_factory=list, description="Chronic illnesses")
    current_medications: List[Any] = Field(default_factory=list, description="Current medications")
    recent_vitals: List[Dict[str, Any]] = Field(default_factory=list, description="Recent biometric vital readings")
    recent_consultations: List[DoctorConsultationSummary] = Field(default_factory=list, description="Recent clinical consultations")
    recent_documents: List[DoctorDocumentSummary] = Field(default_factory=list, description="Recent test reports & prescriptions")
    token_expires_at: datetime = Field(..., description="UTC expiration timestamp of the token")
    remaining_seconds: int = Field(..., description="Remaining seconds before token expiry")

class FCMTokenUpdateRequest(BaseModel):
    fcm_token: str = Field(..., description="The Firebase Cloud Messaging device registration token")
    platform: Optional[PlatformEnum] = Field(None, description="Operating system platform: 'ios', 'android', or 'web'")

class FCMTokenUpdateResponse(BaseModel):
    success: bool = Field(..., description="Indicates if the FCM token was updated successfully")
