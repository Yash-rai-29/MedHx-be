from pydantic import BaseModel, ConfigDict, Field, model_validator
from typing import List, Optional
from datetime import datetime, UTC
from enum import Enum


class VitalType(str, Enum):
    blood_pressure      = "blood_pressure"
    blood_glucose       = "blood_glucose"
    heart_rate          = "heart_rate"
    spo2                = "spo2"
    temperature         = "temperature"
    weight_bmi          = "weight_bmi"
    respiratory_rate    = "respiratory_rate"
    hba1c               = "hba1c"
    cholesterol         = "cholesterol"
    uric_acid           = "uric_acid"
    creatinine          = "creatinine"
    hemoglobin          = "hemoglobin"
    waist_circumference = "waist_circumference"


class DeviceSource(str, Enum):
    manual      = "manual"
    wearable    = "wearable"
    glucometer  = "glucometer"
    bp_monitor  = "bp_monitor"
    oximeter    = "oximeter"
    thermometer = "thermometer"


class VitalFlag(BaseModel):
    vital:   str   = Field(..., description="Vital field name, e.g. 'systolic'")
    value:   float = Field(...)
    status:  str   = Field(..., description="normal | elevated | low | high | critical_high | critical_low")
    message: str   = Field(..., description="Human-readable interpretation with Indian reference range")


class VitalsLogRequest(BaseModel):
    model_config = ConfigDict(json_schema_extra={
        "example": {
            "systolic":    125,
            "diastolic":   82,
            "heart_rate":  74,
            "spo2":        98.0,
            "temperature": 36.8,
            "weight":      72.0,
            "height":      170.0,
            "notes":       "After morning walk",
            "measured_at": "2025-01-15T07:30:00Z",
            "device_source": "manual",
        }
    })

    # Priority 1 — core vitals
    systolic:          Optional[int]   = Field(None, ge=40,  le=300,  description="Systolic BP (mmHg)")
    diastolic:         Optional[int]   = Field(None, ge=20,  le=200,  description="Diastolic BP (mmHg)")
    heart_rate:        Optional[int]   = Field(None, ge=20,  le=300,  description="Heart rate (bpm)")
    spo2:              Optional[float] = Field(None, ge=50,  le=100,  description="Oxygen saturation (%)")
    temperature:       Optional[float] = Field(None, ge=30,  le=45,   description="Body temperature (°C)")
    weight:            Optional[float] = Field(None, gt=0,   le=500,  description="Body weight (kg)")
    height:            Optional[float] = Field(None, gt=0,   le=300,  description="Height (cm)")
    respiratory_rate:  Optional[int]   = Field(None, ge=1,   le=60,   description="Breaths per minute")
    glucose_fasting:   Optional[float] = Field(None, ge=20,  le=600,  description="Fasting blood glucose (mg/dL)")
    glucose_post_meal: Optional[float] = Field(None, ge=20,  le=600,  description="Post-meal blood glucose (mg/dL)")
    glucose_random:    Optional[float] = Field(None, ge=20,  le=600,  description="Random blood glucose (mg/dL)")

    # Priority 2 — extended labs
    hba1c:               Optional[float] = Field(None, ge=2,   le=20,   description="HbA1c (%)")
    cholesterol_total:   Optional[float] = Field(None, ge=50,  le=600,  description="Total cholesterol (mg/dL)")
    cholesterol_ldl:     Optional[float] = Field(None, ge=20,  le=400,  description="LDL cholesterol (mg/dL)")
    cholesterol_hdl:     Optional[float] = Field(None, ge=10,  le=200,  description="HDL cholesterol (mg/dL)")
    triglycerides:       Optional[float] = Field(None, ge=20,  le=1000, description="Triglycerides (mg/dL)")
    uric_acid:           Optional[float] = Field(None, ge=1,   le=20,   description="Uric acid (mg/dL)")
    creatinine:          Optional[float] = Field(None, ge=0.1, le=20,   description="Serum creatinine (mg/dL)")
    egfr:                Optional[float] = Field(None, ge=0,   le=200,  description="Estimated GFR (mL/min/1.73m²)")
    hemoglobin:          Optional[float] = Field(None, ge=1,   le=25,   description="Haemoglobin (g/dL)")
    waist_circumference: Optional[float] = Field(None, ge=30,  le=250,  description="Waist circumference (cm)")

    # Context
    notes:         Optional[str]          = Field(None, max_length=500)
    measured_at:   Optional[datetime]     = Field(None, description="When the reading was taken — supports backdating")
    device_source: Optional[DeviceSource] = Field(DeviceSource.manual)

    @model_validator(mode="after")
    def at_least_one_vital(self):
        vital_fields = [
            self.systolic, self.diastolic, self.heart_rate, self.spo2,
            self.temperature, self.weight, self.height, self.respiratory_rate,
            self.glucose_fasting, self.glucose_post_meal, self.glucose_random,
            self.hba1c, self.cholesterol_total, self.cholesterol_ldl, self.cholesterol_hdl,
            self.triglycerides, self.uric_acid, self.creatinine, self.egfr,
            self.hemoglobin, self.waist_circumference,
        ]
        if not any(v is not None for v in vital_fields):
            raise ValueError("At least one vital measurement field must be provided.")
        return self


class VitalsLogResponse(BaseModel):
    model_config = ConfigDict(json_schema_extra={
        "example": {
            "id":          "vtl_abc123",
            "patient_id":  "uid_001",
            "vital_types": ["blood_pressure", "heart_rate"],
            "systolic":    125,
            "diastolic":   82,
            "heart_rate":  74,
            "spo2":        None,
            "temperature": None,
            "weight":      None,
            "height":      None,
            "bmi":         None,
            "bmi_category": None,
            "respiratory_rate":   None,
            "glucose_fasting":    None,
            "glucose_post_meal":  None,
            "glucose_random":     None,
            "hba1c":              None,
            "cholesterol_total":  None,
            "cholesterol_ldl":    None,
            "cholesterol_hdl":    None,
            "triglycerides":      None,
            "uric_acid":          None,
            "creatinine":         None,
            "egfr":               None,
            "hemoglobin":         None,
            "waist_circumference": None,
            "notes":         "After morning walk",
            "device_source": "manual",
            "measured_at":   "2025-01-15T07:30:00Z",
            "logged_at":     "2025-01-15T07:35:00Z",
            "flags": [
                {"vital": "systolic", "value": 125, "status": "elevated",
                 "message": "Elevated blood pressure (120–129 mmHg)"},
            ],
        }
    })

    id:          str       = Field(...)
    patient_id:  str       = Field(...)
    vital_types: List[str] = Field(..., description="Which vital categories are present in this entry")

    # P1
    systolic:          Optional[int]   = None
    diastolic:         Optional[int]   = None
    heart_rate:        Optional[int]   = None
    spo2:              Optional[float] = None
    temperature:       Optional[float] = None
    weight:            Optional[float] = None
    height:            Optional[float] = None
    bmi:               Optional[float] = None
    bmi_category:      Optional[str]   = None
    respiratory_rate:  Optional[int]   = None
    glucose_fasting:   Optional[float] = None
    glucose_post_meal: Optional[float] = None
    glucose_random:    Optional[float] = None

    # P2
    hba1c:               Optional[float] = None
    cholesterol_total:   Optional[float] = None
    cholesterol_ldl:     Optional[float] = None
    cholesterol_hdl:     Optional[float] = None
    triglycerides:       Optional[float] = None
    uric_acid:           Optional[float] = None
    creatinine:          Optional[float] = None
    egfr:                Optional[float] = None
    hemoglobin:          Optional[float] = None
    waist_circumference: Optional[float] = None

    notes:         Optional[str] = None
    device_source: Optional[str] = None
    measured_at:   datetime      = Field(...)
    logged_at:     datetime      = Field(...)
    flags:         List[VitalFlag] = Field(default=[])


class VitalLatestResponse(BaseModel):
    """Most recent reading for a single vital type — used for dashboard cards."""
    model_config = ConfigDict(json_schema_extra={
        "example": {
            "vital_type":  "blood_pressure",
            "measured_at": "2025-01-15T07:30:00Z",
            "logged_at":   "2025-01-15T07:35:00Z",
            "values":      {"systolic": 125, "diastolic": 82},
            "flags": [
                {"vital": "systolic", "value": 125, "status": "elevated",
                 "message": "Elevated blood pressure (120–129 mmHg)"},
            ],
        }
    })

    vital_type:  str            = Field(...)
    measured_at: datetime       = Field(...)
    logged_at:   datetime       = Field(...)
    values:      dict           = Field(..., description="Field values for this vital type")
    flags:       List[VitalFlag] = Field(default=[])


class VitalTrendPoint(BaseModel):
    measured_at: datetime       = Field(...)
    values:      dict           = Field(..., description="Vital field values at this point in time")
    flags:       List[VitalFlag] = Field(default=[])


class VitalTrendResponse(BaseModel):
    model_config = ConfigDict(json_schema_extra={
        "example": {
            "vital_type": "blood_pressure",
            "unit":       "mmHg",
            "points": [
                {"measured_at": "2025-01-10T07:30:00Z",
                 "values": {"systolic": 118, "diastolic": 76},
                 "flags": [{"vital": "systolic", "value": 118, "status": "normal", "message": "Normal (<120 mmHg)"}]},
                {"measured_at": "2025-01-15T07:30:00Z",
                 "values": {"systolic": 125, "diastolic": 82},
                 "flags": [{"vital": "systolic", "value": 125, "status": "elevated", "message": "Elevated (120–129 mmHg)"}]},
            ],
        }
    })

    vital_type: str                   = Field(...)
    unit:       str                   = Field(..., description="Display unit for this vital type")
    points:     List[VitalTrendPoint] = Field(..., description="Ordered oldest → newest")
