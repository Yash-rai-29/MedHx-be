from pydantic import BaseModel, ConfigDict, Field
from typing import List, Optional
from datetime import datetime


class DashboardNextReminder(BaseModel):
    model_config = ConfigDict(json_schema_extra={
        "example": {
            "id": "a1b2c3d4-0000-0000-0000-111111111111",
            "title": "Take Metformin 500mg",
            "type": "medicine",
            "next_trigger_at": "2026-06-28T08:00:00+05:30",
        }
    })

    id:              str
    title:           str
    type:            str       # "medicine" | "follow_up"
    next_trigger_at: datetime


class DashboardDocument(BaseModel):
    model_config = ConfigDict(json_schema_extra={
        "example": {
            "id": "doc-abc123",
            "title": "Apollo Blood Test Results June 2026",
            "type": "lab_report",
            "doctor_name": "Dr. Anita Mehta",
            "document_date": "2026-06-20",
            "warnings": [],
            "created_at": "2026-06-20T14:30:00+05:30",
        }
    })

    id:            str
    title:         Optional[str] = None
    type:          str
    doctor_name:   Optional[str] = None
    document_date: Optional[str] = None
    warnings:      List[str]     = Field(
        default=[],
        examples=[["Patient name on document ('Rahul Sharma') does not match profile name ('Rahul S')."]],
        description="Processing warnings for this document (e.g. patient name mismatch)",
    )
    created_at:    datetime


class DashboardConsultation(BaseModel):
    model_config = ConfigDict(json_schema_extra={
        "example": {
            "id": "cons-xyz789",
            "title": "Diabetes Management and Medication Review",
            "summary": "Doctor reviewed blood sugar levels and adjusted Metformin dosage to 1000mg twice daily. HbA1c is at 7.2%, slightly above target. Follow-up in 4 weeks recommended.",
            "doctor_name": "Dr. Priya Nair",
            "key_diagnoses": ["Type 2 Diabetes Mellitus", "Hypertension"],
            "created_at": "2026-06-25T11:00:00+05:30",
        }
    })

    id:             str
    title:          Optional[str] = None
    summary:        Optional[str] = None
    doctor_name:    Optional[str] = None
    key_diagnoses:  List[str]     = Field(
        default=[],
        examples=[["Type 2 Diabetes Mellitus", "Hypertension"]],
        description="Diagnoses explicitly stated during the consultation",
    )
    created_at:     datetime


class DashboardHealthAlert(BaseModel):
    model_config = ConfigDict(json_schema_extra={
        "example": {
            "source_type": "document",
            "source_id": "doc-abc123",
            "message": "Haemoglobin is critically low (7.2 g/dL). Seek medical attention immediately.",
            "severity": "red_flag",
        }
    })

    source_type: str = Field(
        ...,
        description="Origin of the alert",
        examples=["document", "vitals"],
    )
    source_id:   str = Field(..., description="ID of the source document or patient UID for vitals")
    message:     str = Field(..., description="Human-readable alert message")
    severity:    str = Field(
        ...,
        description="Alert severity level",
        examples=["warning", "red_flag", "critical"],
    )


class DashboardAbnormalFlag(BaseModel):
    model_config = ConfigDict(json_schema_extra={
        "example": {
            "vital": "systolic",
            "value": 148.0,
            "status": "high",
            "message": "High blood pressure (130–139 mmHg). Consult your doctor.",
        }
    })

    vital:   str   = Field(..., description="Vital field name (e.g. systolic, heart_rate, hba1c)")
    value:   float = Field(..., description="Measured value")
    status:  str   = Field(
        ...,
        description="Deviation status",
        examples=["elevated", "high", "low", "critical_high", "critical_low"],
    )
    message: str   = Field(..., description="Human-readable interpretation with reference range")


class DashboardVitalsSnapshot(BaseModel):
    model_config = ConfigDict(json_schema_extra={
        "example": {
            "logged_at": "2026-06-27T07:30:00+05:30",
            "abnormal_flags": [
                {"vital": "systolic", "value": 148.0, "status": "high",
                 "message": "High blood pressure (130–139 mmHg). Consult your doctor."},
                {"vital": "heart_rate", "value": 108, "status": "high",
                 "message": "Tachycardia (>100 bpm)"},
            ],
        }
    })

    logged_at:      datetime
    abnormal_flags: List[DashboardAbnormalFlag] = Field(
        default=[],
        description="Vital flags with status other than 'normal'",
        examples=[[
            {"vital": "systolic", "value": 148.0, "status": "high",
             "message": "High blood pressure (130–139 mmHg). Consult your doctor."},
        ]],
    )


class DashboardStats(BaseModel):
    model_config = ConfigDict(json_schema_extra={
        "example": {
            "patient_name": "Harsh Kumar",
            "active_reminders_count": 4,
            "medicine_reminders_count": 3,
            "followup_reminders_count": 1,
            "next_reminder": {
                "id": "a1b2c3d4-0000-0000-0000-111111111111",
                "title": "Take Metformin 500mg",
                "type": "medicine",
                "next_trigger_at": "2026-06-28T08:00:00+05:30",
            },
            "active_medicine_names": ["Metformin", "Amlodipine", "Telmisartan"],
            "recent_documents": [
                {
                    "id": "doc-abc123",
                    "title": "Apollo Blood Test Results June 2026",
                    "type": "lab_report",
                    "doctor_name": "Dr. Anita Mehta",
                    "document_date": "2026-06-20",
                    "warnings": [],
                    "created_at": "2026-06-20T14:30:00+05:30",
                }
            ],
            "recent_consultation": {
                "id": "cons-xyz789",
                "title": "Diabetes Management and Medication Review",
                "summary": "Doctor reviewed blood sugar levels and adjusted Metformin dosage.",
                "doctor_name": "Dr. Priya Nair",
                "key_diagnoses": ["Type 2 Diabetes Mellitus"],
                "created_at": "2026-06-25T11:00:00+05:30",
            },
            "health_alerts": [
                {
                    "source_type": "document",
                    "source_id": "doc-abc123",
                    "message": "Haemoglobin critically low (7.2 g/dL). Seek medical attention immediately.",
                    "severity": "red_flag",
                }
            ],
            "latest_vitals": {
                "logged_at": "2026-06-27T07:30:00+05:30",
                "abnormal_flags": [
                    {"vital": "systolic", "value": 148.0, "status": "high",
                     "message": "High blood pressure (130–139 mmHg)."},
                ],
            },
            "generated_at": "2026-06-28T06:45:00+05:30",
        }
    })

    patient_name: Optional[str] = Field(None, description="Patient's full name from profile")

    # ── Reminders ────────────────────────────────────────────────
    active_reminders_count:   int = Field(0, description="Total active reminders", examples=[4])
    medicine_reminders_count: int = Field(0, description="Active medicine reminders", examples=[3])
    followup_reminders_count: int = Field(0, description="Active follow-up reminders", examples=[1])
    next_reminder:            Optional[DashboardNextReminder] = Field(
        None,
        description="The upcoming reminder with the earliest next_trigger_at",
    )
    active_medicine_names: List[str] = Field(
        default=[],
        description="Deduplicated medicine names from active medicine reminders",
        examples=[["Metformin", "Amlodipine", "Telmisartan"]],
    )

    # ── Documents ────────────────────────────────────────────────
    recent_documents: List[DashboardDocument] = Field(
        default=[],
        description="Up to 5 most recently uploaded documents",
        examples=[[
            {
                "id": "doc-abc123",
                "title": "Apollo Blood Test Results June 2026",
                "type": "lab_report",
                "doctor_name": "Dr. Anita Mehta",
                "document_date": "2026-06-20",
                "warnings": [],
                "created_at": "2026-06-20T14:30:00+05:30",
            }
        ]],
    )

    # ── Consultations ────────────────────────────────────────────
    recent_consultation: Optional[DashboardConsultation] = Field(
        None,
        description="Most recent completed audio consultation",
    )

    # ── Health ───────────────────────────────────────────────────
    health_alerts: List[DashboardHealthAlert] = Field(
        default=[],
        description="Aggregated alerts from document red flags, document warnings, and critical vitals",
        examples=[[
            {
                "source_type": "document",
                "source_id": "doc-abc123",
                "message": "Haemoglobin critically low (7.2 g/dL). Seek medical attention immediately.",
                "severity": "red_flag",
            }
        ]],
    )
    latest_vitals: Optional[DashboardVitalsSnapshot] = Field(
        None,
        description="Most recent vitals log with only abnormal flags included",
    )

    generated_at: datetime = Field(..., description="UTC timestamp when this response was assembled")
