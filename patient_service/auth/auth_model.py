from pydantic import BaseModel, Field
from typing import Optional
from datetime import datetime
from enum import Enum


# ── Enums ──────────────────────────────────────────────────────────────────────

class OnboardingStatus(str, Enum):
    pending   = "pending"
    completed = "completed"
    skipped   = "skipped"

class LanguagePreference(str, Enum):
    en = "en"
    hi = "hi"
    ta = "ta"
    te = "te"
    mr = "mr"
    bn = "bn"


# ── Models ──────────────────────────────────────────────────────────────────────

class PatientRegisterRequest(BaseModel):
    name:                    Optional[str]         = Field(None, min_length=2, description="Patient's full name")
    phone:                   Optional[str]         = Field(None, description="Optional phone number with country code")
    email:                   Optional[str]         = Field(None, description="Email address associated with the user account")
    language_preference:     LanguagePreference    = Field(LanguagePreference.en, description="Default language preference")
    date_of_birth:           Optional[str]         = Field(None, description="Patient's Date of Birth (YYYY-MM-DD)")
    location:                Optional[str]         = Field(None, description="Patient's Location/Region (e.g. Hyderabad, India)")
    accepted_privacy_policy: bool                  = Field(..., description="User must accept the privacy policy")
    accepted_terms_of_service: bool                = Field(..., description="User must accept the terms of service")


class UserResponse(BaseModel):
    uid:                     str                   = Field(..., description="Unique Firebase User ID")
    name:                    str                   = Field(..., description="Patient's full name")
    phone:                   Optional[str]         = Field(None, description="Optional phone number with country code")
    email:                   Optional[str]         = Field(None, description="Email address associated with the user account")
    role:                    str                   = Field(..., description="User role, always 'patient' for this backend")
    language_preference:     str                   = Field("en", description="Patient's preferred language (en, hi, ta, te)")
    date_of_birth:           Optional[str]         = Field(None, description="Patient's date of birth in YYYY-MM-DD format")
    location:                Optional[str]         = Field(None, description="Patient's physical location / city")
    onboarding_status:       str                   = Field("pending", description="Current onboarding flow status")
    auth_provider:           Optional[str]         = Field(None, description="Firebase authentication provider used (e.g. google.com, password)")


class LegalDocumentResponse(BaseModel):
    doc_type:         str      = Field(..., description="Document type: privacy_policy or terms_of_service")
    title:            str      = Field(..., description="Title of the document")
    content_markdown: str      = Field(..., description="The content of the document in markdown format")
    version:          str      = Field(..., description="Version of the document, e.g. 1.0.0")
    updated_at:       datetime = Field(..., description="Timestamp when the document was last updated")
