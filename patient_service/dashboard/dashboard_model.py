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

    # ── Health ───────────────────────────────────────────────────
    latest_vitals: Optional[DashboardVitalsSnapshot] = Field(
        None,
        description="Most recent vitals log with only abnormal flags included",
    )

    generated_at: datetime = Field(..., description="UTC timestamp when this response was assembled")
