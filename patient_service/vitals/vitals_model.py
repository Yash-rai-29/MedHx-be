from pydantic import BaseModel, ConfigDict, Field, model_validator
from typing import List, Optional
from datetime import datetime, date
from enum import Enum


class VitalType(str, Enum):
    blood_pressure   = "blood_pressure"
    blood_glucose    = "blood_glucose"
    heart_rate       = "heart_rate"
    spo2             = "spo2"
    temperature      = "temperature"
    weight_bmi       = "weight_bmi"
    respiratory_rate = "respiratory_rate"


class DeviceSource(str, Enum):
    manual      = "manual"
    wearable    = "wearable"
    glucometer  = "glucometer"
    bp_monitor  = "bp_monitor"
    oximeter    = "oximeter"
    thermometer = "thermometer"


class VitalFlag(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    vital:   str   = Field(..., description="Vital field name, e.g. 'systolic'")
    value:   float = Field(...)
    status:  str   = Field(..., description="normal | elevated | low | high | critical_high | critical_low")
    message: str   = Field(..., description="Human-readable interpretation with Indian clinical reference range")


class VitalsLogRequest(BaseModel):
    """
    Streamlined daily vitals logging request for patient mobile app and wearables.
    At least one vital measurement field must be provided.
    """
    model_config = ConfigDict(json_schema_extra={
        "example": {
            "systolic":        125,
            "diastolic":       82,
            "heart_rate":      74,
            "spo2":            98.0,
            "temperature":     36.8,
            "weight":          72.0,
            "height":          170.0,
            "glucose_fasting": 95.0,
            "notes":           "After morning walk",
            "measured_at":     "2026-08-15T07:30:00Z",
            "device_source":   "manual",
        }
    })

    # Blood Pressure (mmHg)
    systolic:          Optional[int]   = Field(None, ge=40,  le=300,  description="Systolic BP (mmHg)")
    diastolic:         Optional[int]   = Field(None, ge=20,  le=200,  description="Diastolic BP (mmHg)")

    # Heart Rate (bpm)
    heart_rate:        Optional[int]   = Field(None, ge=20,  le=300,  description="Heart rate / pulse (bpm)")

    # Blood Glucose (mg/dL)
    glucose_fasting:   Optional[float] = Field(None, ge=20,  le=600,  description="Fasting blood glucose (mg/dL)")
    glucose_post_meal: Optional[float] = Field(None, ge=20,  le=600,  description="Post-meal blood glucose (mg/dL)")
    glucose_random:    Optional[float] = Field(None, ge=20,  le=600,  description="Random blood glucose (mg/dL)")

    # Oxygen Saturation & Respiratory
    spo2:              Optional[float] = Field(None, ge=50,  le=100,  description="Oxygen saturation SpO2 (%)")
    respiratory_rate:  Optional[int]   = Field(None, ge=1,   le=60,   description="Breaths per minute")

    # Body Temperature (°C or °F auto-converted)
    temperature:       Optional[float] = Field(None, ge=30,  le=115,  description="Body temperature (°C or °F; values >45 auto-converted to °C)")

    # Anthropometry (auto-computes BMI when both provided)
    weight:            Optional[float] = Field(None, gt=0,   le=500,  description="Body weight (kg)")
    height:            Optional[float] = Field(None, gt=0,   le=300,  description="Height (cm)")

    # Context & Source
    notes:             Optional[str]          = Field(None, max_length=500, description="Optional patient notes")
    measured_at:       Optional[datetime]     = Field(None, description="Timestamp of reading (supports backdating)")
    device_source:     Optional[DeviceSource] = Field(DeviceSource.manual, description="Measurement source/device")

    @model_validator(mode="after")
    def validate_and_convert(self):
        vital_fields = [
            self.systolic, self.diastolic, self.heart_rate, self.spo2,
            self.temperature, self.weight, self.height, self.respiratory_rate,
            self.glucose_fasting, self.glucose_post_meal, self.glucose_random,
        ]
        if not any(v is not None for v in vital_fields):
            raise ValueError("At least one vital measurement field must be provided.")

        # Auto-convert temperature if entered in Fahrenheit (e.g. 98.6°F -> 37.0°C)
        if self.temperature is not None and self.temperature > 45.0:
            converted_c = round((self.temperature - 32.0) * 5.0 / 9.0, 1)
            self.temperature = converted_c

        return self


class VitalsLogResponse(BaseModel):
    model_config = ConfigDict(
        populate_by_name=True,
        json_schema_extra={
            "example": {
                "id": "vtl_abc123",
                "patient_id": "test-patient-123",
                "vital_types": ["blood_pressure", "heart_rate"],
                "systolic": 125,
                "diastolic": 82,
                "heart_rate": 74,
                "spo2": 98.0,
                "temperature": 36.8,
                "weight": 70.0,
                "height": 175.0,
                "bmi": 22.9,
                "bmi_category": "Normal",
                "respiratory_rate": 16,
                "glucose_fasting": 95.0,
                "glucose_post_meal": None,
                "glucose_random": None,
                "notes": "Morning checkup",
                "device_source": "manual",
                "measured_at": "2026-08-15T07:30:00Z",
                "logged_at": "2026-08-15T07:35:00Z",
                "flags": [
                    {
                        "vital": "systolic",
                        "value": 125.0,
                        "status": "elevated",
                        "message": "Elevated blood pressure (120–129 mmHg)"
                    }
                ]
            }
        }
    )

    id:                str             = Field(...)
    patient_id:        str             = Field(...)
    vital_types:       List[str]       = Field(..., description="Which vital categories are present in this entry")

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

    notes:             Optional[str]   = None
    device_source:     Optional[str]   = None
    measured_at:       datetime        = Field(...)
    logged_at:         datetime        = Field(...)
    flags:             List[VitalFlag] = Field(default=[], description="Clinical flags and reference warnings")


class VitalsListResponse(BaseModel):
    """Paginated response for vitals log entries."""
    model_config = ConfigDict(
        populate_by_name=True,
        json_schema_extra={
            "example": {
                "items": [
                    {
                        "id": "vtl_abc123",
                        "patient_id": "test-patient-123",
                        "vital_types": ["blood_pressure", "heart_rate"],
                        "systolic": 125,
                        "diastolic": 82,
                        "heart_rate": 74,
                        "spo2": 98.0,
                        "temperature": 36.8,
                        "weight": 70.0,
                        "height": 175.0,
                        "bmi": 22.9,
                        "bmi_category": "Normal",
                        "respiratory_rate": 16,
                        "glucose_fasting": 95.0,
                        "glucose_post_meal": None,
                        "glucose_random": None,
                        "notes": "Morning checkup",
                        "device_source": "manual",
                        "measured_at": "2026-08-15T07:30:00Z",
                        "logged_at": "2026-08-15T07:35:00Z",
                        "flags": [
                            {
                                "vital": "systolic",
                                "value": 125.0,
                                "status": "elevated",
                                "message": "Elevated blood pressure (120–129 mmHg)"
                            }
                        ]
                    }
                ],
                "next_cursor": "1786756800.0",
                "has_more": True,
                "total_count": 1
            }
        }
    )

    items:       List[VitalsLogResponse] = Field(..., description="List of vitals logs, newest first")
    next_cursor: Optional[str]           = Field(None, description="Cursor for next page")
    has_more:    bool                    = Field(False, description="True if more entries exist")
    total_count: int                     = Field(0, description="Total count in current page")


class VitalLatestResponse(BaseModel):
    """Most recent reading for a single vital category — used for dashboard summary cards."""
    model_config = ConfigDict(
        populate_by_name=True,
        json_schema_extra={
            "example": {
                "vital_type": "blood_pressure",
                "measured_at": "2026-08-15T07:30:00Z",
                "logged_at": "2026-08-15T07:35:00Z",
                "values": {"systolic": 125, "diastolic": 82},
                "flags": [
                    {
                        "vital": "systolic",
                        "value": 125.0,
                        "status": "elevated",
                        "message": "Elevated blood pressure (120–129 mmHg)"
                    }
                ]
            }
        }
    )

    vital_type:  str             = Field(...)
    measured_at: datetime        = Field(...)
    logged_at:   datetime        = Field(...)
    values:      dict            = Field(..., description="Field values for this vital type")
    flags:       List[VitalFlag] = Field(default=[], description="Clinical flags for latest values")


class VitalTrendPoint(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    measured_at: datetime        = Field(...)
    values:      dict            = Field(..., description="Vital values at this point in time")
    flags:       List[VitalFlag] = Field(default=[], description="Flags for this data point")


class VitalTrendResponse(BaseModel):
    model_config = ConfigDict(
        populate_by_name=True,
        json_schema_extra={
            "example": {
                "vital_type": "blood_pressure",
                "unit": "mmHg",
                "points": [
                    {
                        "measured_at": "2026-08-10T07:30:00Z",
                        "values": {"systolic": 118, "diastolic": 76},
                        "flags": [{"vital": "systolic", "value": 118.0, "status": "normal", "message": "Normal (<120 mmHg)"}]
                    },
                    {
                        "measured_at": "2026-08-15T07:30:00Z",
                        "values": {"systolic": 125, "diastolic": 82},
                        "flags": [{"vital": "systolic", "value": 125.0, "status": "elevated", "message": "Elevated blood pressure (120–129 mmHg)"}]
                    }
                ]
            }
        }
    )

    vital_type: str                   = Field(...)
    unit:       str                   = Field(..., description="Display unit for this vital type")
    points:     List[VitalTrendPoint] = Field(..., description="Ordered oldest → newest")
