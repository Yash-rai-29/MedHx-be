from pydantic import BaseModel, Field, model_validator
from typing import Optional, Any
from datetime import datetime
from enum import Enum


# ── Enums ──────────────────────────────────────────────────────────────────────

class OnboardingStatus(str, Enum):
    pending   = "pending"
    completed = "completed"
    skipped   = "skipped"


class AccountStatus(str, Enum):
    active           = "active"
    pending_deletion = "pending_deletion"


class AccountDeletionStatus(str, Enum):
    pending_deletion = "pending_deletion"
    cancelled = "cancelled"
    processing = "processing"
    completed = "completed"
    failed = "failed"


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
    country_code:            Optional[str]         = Field(None, description="Country calling code (e.g. '+91')")
    phone_number:            Optional[str]         = Field(None, description="Phone number without country code (e.g. '9876543210')")
    email:                   Optional[str]         = Field(None, description="Email address associated with the user account")
    language_preference:     LanguagePreference    = Field(LanguagePreference.en, description="Default language preference")
    date_of_birth:           Optional[str]         = Field(None, description="Patient's Date of Birth (YYYY-MM-DD)")
    location:                Optional[str]         = Field(None, description="Patient's Location/Region (e.g. Hyderabad, India)")
    accepted_privacy_policy: bool                  = Field(..., description="User must accept the privacy policy")
    accepted_terms_of_service: bool                = Field(..., description="User must accept the terms of service")


class UserUpdateRequest(BaseModel):
    name:                Optional[str]               = Field(None, min_length=2, description="Patient's updated full name")
    country_code:        Optional[str]               = Field(None, description="Updated country calling code (e.g. '+91')")
    phone_number:        Optional[str]               = Field(None, description="Updated phone number without country code")
    language_preference: Optional[LanguagePreference] = Field(None, description="Updated language preference (en, hi, ta, te, mr, bn)")
    date_of_birth:       Optional[str]               = Field(None, description="Updated date of birth in YYYY-MM-DD format")
    location:            Optional[str]               = Field(None, description="Updated location / city")


class UserResponse(BaseModel):
    uid:                     str                   = Field(..., description="Unique Firebase User ID")
    name:                    str                   = Field(..., description="Patient's full name")
    country_code:            Optional[str]         = Field(None, description="Country calling code (e.g. '+91')")
    phone_number:            Optional[str]         = Field(None, description="Phone number without country code")
    email:                   Optional[str]         = Field(None, description="Email address associated with the user account")
    role:                    str                   = Field(..., description="User role, always 'patient' for this backend")
    language_preference:     str                   = Field("en", description="Patient's preferred language (en, hi, ta, te)")
    date_of_birth:           Optional[str]         = Field(None, description="Patient's date of birth in YYYY-MM-DD format")
    location:                Optional[str]         = Field(None, description="Patient's physical location / city")
    onboarding_status:       str                   = Field("pending", description="Current onboarding flow status")
    auth_provider:           Optional[str]         = Field(None, description="Firebase authentication provider used (e.g. google.com, password)")
    account_status:          Optional[AccountStatus] = Field(AccountStatus.active, description="Current account state: 'active' or 'pending_deletion'")
    deletion_scheduled_at:   Optional[datetime]    = Field(None, description="Timestamp when account is scheduled for permanent purge if deletion requested")

    @model_validator(mode="before")
    @classmethod
    def _populate_phone_fields(cls, data: Any) -> Any:
        if isinstance(data, dict):
            if not data.get("phone_number") and data.get("phone"):
                data["phone_number"] = data.get("phone")
        return data


class LegalDocumentResponse(BaseModel):
    doc_type:         str      = Field(..., description="Document type: privacy_policy or terms_of_service")
    title:            str      = Field(..., description="Title of the document")
    content_markdown: str      = Field(..., description="The content of the document in markdown format")
    version:          str      = Field(..., description="Version of the document, e.g. 1.0.0")
    updated_at:       datetime = Field(..., description="Timestamp when the document was last updated")


class DeleteAccountRequest(BaseModel):
    reason: Optional[str] = Field(None, description="Optional feedback or reason for requesting account deletion")


class DeleteAccountResponse(BaseModel):
    status: str = Field("pending_deletion", description="Deletion request status")
    message: str = Field(
        "Your account deletion request has been scheduled. Your account and all associated health data will be permanently deleted in the next 48 hours.",
        description="User confirmation message"
    )
    patient_id: str = Field(..., description="UID of patient account")
    requested_at: datetime = Field(..., description="Timestamp when deletion was requested")
    scheduled_deletion_at: datetime = Field(..., description="Timestamp when permanent purge will be executed")


class CancelDeleteAccountResponse(BaseModel):
    status: str = Field("cancelled", description="Deletion cancellation status")
    message: str = Field("Your account deletion request has been cancelled. Your account and health records will remain active.", description="Confirmation message")
    patient_id: str = Field(..., description="UID of patient account")
    cancelled_at: datetime = Field(..., description="Timestamp when deletion was cancelled")
