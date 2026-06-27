from pydantic import BaseModel, Field
from typing import List, Optional
from datetime import datetime
from enum import Enum

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
    phone: Optional[str] = Field(None, description="Patient's contact phone number")
    email: Optional[str] = Field(None, description="Patient's email address")
    role: Optional[str] = Field(None, description="Access role for user, e.g. patient")
    language_preference: Optional[str] = Field(None, description="Preferred app interface language (en, hi, ta, te)")
    auth_provider: Optional[str] = Field(None, description="Auth provider like google.com, password")
    accepted_privacy_policy: Optional[bool] = Field(None, description="Whether the user accepted the privacy policy")
    accepted_terms_of_service: Optional[bool] = Field(None, description="Whether the user accepted the terms of service")

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
    phone: Optional[str] = Field(None, description="Phone number")
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
    phone: Optional[str] = Field(None, description="Patient's contact phone number")
    email: Optional[str] = Field(None, description="Patient's email address")
    role: str = Field(..., description="Access control role (patient)")
    language_preference: str = Field(..., description="Language preference selection (en, hi, ta, te)")
    onboarding_status: str = Field(..., description="Onboarding status: completed, skipped")

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
    blood_group: Optional[str] = Field(None, description="Patient's blood group")
    allergies: List[str] = Field(..., description="List of patient's documented allergies")
    chronic_conditions: List[str] = Field(..., description="List of patient's documented chronic illnesses")
    current_medications: List[str] = Field(..., description="List of patient's current medications")
    emergency_contact: Optional[EmergencyContact] = Field(None, description="Emergency contact details")
    qr_redirect_url: Optional[str] = Field(None, description="Dynamic URL meant for the scannable QR SOS card redirecting to public SOS details page")

class FCMTokenUpdateRequest(BaseModel):
    fcm_token: str = Field(..., description="The Firebase Cloud Messaging device registration token")
    platform: Optional[PlatformEnum] = Field(None, description="Operating system platform: 'ios', 'android', or 'web'")

class FCMTokenUpdateResponse(BaseModel):
    success: bool = Field(..., description="Indicates if the FCM token was updated successfully")
