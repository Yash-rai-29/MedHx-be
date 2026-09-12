from pydantic import BaseModel, Field, ConfigDict
from typing import Optional, List
from datetime import datetime, date
from enum import Enum


class TimelineItemType(str, Enum):
    consult_record = "consult_record"
    document       = "document"


class TimelineFilterType(str, Enum):
    all            = "all"
    consult_record = "consult_record"
    document       = "document"


class TimelineItem(BaseModel):
    """
    Standardized timeline item representing a patient consultation or medical document.
    Designed for high-performance timeline cards and activity feeds.
    """
    model_config = ConfigDict(
        populate_by_name=True,
        json_schema_extra={
            "example": {
                "id": "audio-789",
                "type": "consult_record",
                "category": "audio_consultation",
                "title": "Routine Cardiology Review",
                "timestamp": "2026-08-12T11:00:00Z",
                "date_formatted": "12 Aug 2026",
                "time_formatted": "04:30 PM IST",
                "month_group": "August 2026",
                "month_key": "2026-08",
                "doctor_name": "Dr. Verma",
                "summary_preview": "Doctor reviewed BP history and advised continuing current medication.",
                "status": "completed",
                "language": "en",
                "highlights": ["Audio Consultation", "Hypertension", "1 Rx Medicine", "1 Linked Report"],
                "has_audio": True,
                "has_file": True,
                "file_path": "patients/uid/audio_consultations/rec_1.mp3",
                "attached_documents_count": 1,
                "consultation_id": None,
                "metadata": {
                    "key_diagnoses": ["Hypertension"],
                    "medicines_count": 1,
                    "reminders_count": 1
                }
            }
        }
    )

    id:                       str               = Field(..., description="Unique document or consultation ID")
    type:                     TimelineItemType  = Field(..., description="Item type: 'consult_record' or 'document'")
    category:                 str               = Field(..., description="Clinical category: prescription, lab_report, audio_consultation, discharge_summary, etc.")
    title:                    str               = Field(..., description="Human-friendly title")
    timestamp:                datetime          = Field(..., description="UTC timestamp for chronological sorting")
    date_formatted:           str               = Field(..., description="Human-readable date, e.g. '15 Aug 2026'")
    time_formatted:           str               = Field(..., description="Human-readable time in IST, e.g. '09:30 AM IST'")
    month_group:              str               = Field(..., description="Month grouping for timeline section headers, e.g. 'August 2026'")
    month_key:                str               = Field(..., description="Machine-readable month key, e.g. '2026-08'")
    doctor_name:              Optional[str]     = Field(None, description="Doctor or clinic name if present")
    summary_preview:          Optional[str]     = Field(None, description="Concise summary snippet for timeline preview (max ~160 chars)")
    status:                   str               = Field("completed", description="Status: completed, in_progress, failed, published")
    language:                 str               = Field("en", description="Language code: en, hi, ta, etc.")
    highlights:               List[str]         = Field(..., description="Key badges/chips (e.g. ['3 Medicines', '1 Abnormal Lab', 'Cardiology'])")
    has_audio:                bool              = Field(False, description="True if this item has audio recordings available")
    has_file:                 bool              = Field(False, description="True if this item has an attached PDF or report file")
    file_path:                Optional[str]     = Field(None, description="GCS file path if available")
    attached_documents_count: int               = Field(0, description="Number of linked documents/reports")
    consultation_id:          Optional[str]     = Field(None, description="Linked consultation ID if this is an attached document")
    metadata:                 dict              = Field(..., description="Additional structured properties")


class TimelineMonthGroup(BaseModel):
    """
    Groups timeline records by calendar month for clean frontend section rendering.
    """
    model_config = ConfigDict(
        populate_by_name=True,
        json_schema_extra={
            "example": {
                "month_group": "August 2026",
                "month_key": "2026-08",
                "year": 2026,
                "month": 8,
                "count": 2,
                "items": [
                    {
                        "id": "audio-789",
                        "type": "consult_record",
                        "category": "audio_consultation",
                        "title": "Routine Cardiology Review",
                        "timestamp": "2026-08-12T11:00:00Z",
                        "date_formatted": "12 Aug 2026",
                        "time_formatted": "04:30 PM IST",
                        "month_group": "August 2026",
                        "month_key": "2026-08",
                        "doctor_name": "Dr. Verma",
                        "summary_preview": "Doctor reviewed BP history and advised continuing current medication.",
                        "status": "completed",
                        "language": "en",
                        "highlights": ["Audio Consultation", "Hypertension", "1 Rx Medicine", "1 Linked Report"],
                        "has_audio": True,
                        "has_file": True,
                        "file_path": "patients/uid/audio_consultations/rec_1.mp3",
                        "attached_documents_count": 1,
                        "consultation_id": None,
                        "metadata": {
                            "key_diagnoses": ["Hypertension"],
                            "medicines_count": 1,
                            "reminders_count": 1
                        }
                    },
                    {
                        "id": "doc-123",
                        "type": "document",
                        "category": "lab_report",
                        "title": "Complete Blood Count Test",
                        "timestamp": "2026-08-10T10:30:00Z",
                        "date_formatted": "10 Aug 2026",
                        "time_formatted": "04:00 PM IST",
                        "month_group": "August 2026",
                        "month_key": "2026-08",
                        "doctor_name": "Dr. Sharma",
                        "summary_preview": "Patient has slightly low haemoglobin. Other parameters are normal.",
                        "status": "completed",
                        "language": "en",
                        "highlights": ["1 Abnormal Lab", "Lab Report"],
                        "has_audio": False,
                        "has_file": True,
                        "file_path": "patients/uid/reports/cbc_test.pdf",
                        "attached_documents_count": 0,
                        "consultation_id": None,
                        "metadata": {
                            "document_date": "2026-08-10",
                            "medications_count": 0,
                            "abnormal_labs_count": 1,
                            "red_flags_count": 0
                        }
                    }
                ]
            }
        }
    )

    month_group: str                = Field(..., description="Display month name and year, e.g. 'August 2026'")
    month_key:   str                = Field(..., description="Machine-readable key, e.g. '2026-08'")
    year:        int                = Field(..., description="Year, e.g. 2026")
    month:       int                = Field(..., description="Month integer (1-12), e.g. 8")
    count:       int                = Field(..., description="Number of events in this month")
    items:       List[TimelineItem] = Field(..., description="List of timeline items belonging to this month")


class TimelineFilterMetadata(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    type:      TimelineFilterType = TimelineFilterType.all
    year:      Optional[int]      = None
    month:     Optional[int]      = None
    from_date: Optional[date]     = None
    to_date:   Optional[date]     = None
    limit:     int                = 20


class TimelineResponse(BaseModel):
    """
    Paginated medical history timeline containing month-grouped activity cards.
    """
    model_config = ConfigDict(
        populate_by_name=True,
        json_schema_extra={
            "example": {
                "groups": [
                    {
                        "month_group": "August 2026",
                        "month_key": "2026-08",
                        "year": 2026,
                        "month": 8,
                        "count": 2,
                        "items": [
                            {
                                "id": "audio-789",
                                "type": "consult_record",
                                "category": "audio_consultation",
                                "title": "Routine Cardiology Review",
                                "timestamp": "2026-08-12T11:00:00Z",
                                "date_formatted": "12 Aug 2026",
                                "time_formatted": "04:30 PM IST",
                                "month_group": "August 2026",
                                "month_key": "2026-08",
                                "doctor_name": "Dr. Verma",
                                "summary_preview": "Doctor reviewed BP history and advised continuing current medication.",
                                "status": "completed",
                                "language": "en",
                                "highlights": ["Audio Consultation", "Hypertension", "1 Rx Medicine", "1 Linked Report"],
                                "has_audio": True,
                                "has_file": True,
                                "file_path": "patients/uid/audio_consultations/rec_1.mp3",
                                "attached_documents_count": 1,
                                "consultation_id": None,
                                "metadata": {
                                    "key_diagnoses": ["Hypertension"],
                                    "medicines_count": 1,
                                    "reminders_count": 1
                                }
                            },
                            {
                                "id": "doc-123",
                                "type": "document",
                                "category": "lab_report",
                                "title": "Complete Blood Count Test",
                                "timestamp": "2026-08-10T10:30:00Z",
                                "date_formatted": "10 Aug 2026",
                                "time_formatted": "04:00 PM IST",
                                "month_group": "August 2026",
                                "month_key": "2026-08",
                                "doctor_name": "Dr. Sharma",
                                "summary_preview": "Patient has slightly low haemoglobin. Other parameters are normal.",
                                "status": "completed",
                                "language": "en",
                                "highlights": ["1 Abnormal Lab", "Lab Report"],
                                "has_audio": False,
                                "has_file": True,
                                "file_path": "patients/uid/reports/cbc_test.pdf",
                                "attached_documents_count": 0,
                                "consultation_id": None,
                                "metadata": {
                                    "document_date": "2026-08-10",
                                    "medications_count": 0,
                                    "abnormal_labs_count": 1,
                                    "red_flags_count": 0
                                }
                            }
                        ]
                    }
                ],
                # "items": [...],
                "next_cursor": "1786357800.0",
                "has_more": True,
                "total_count": 2,
                "filters_applied": {
                    "type": "all",
                    "year": 2026,
                    "month": None,
                    "from_date": None,
                    "to_date": None,
                    "limit": 20
                }
            }
        }
    )

    groups:          List[TimelineMonthGroup] = Field(..., description="Timeline items grouped by month, newest month first (ideal for month sections)")
    # items:           List[TimelineItem]       = Field(..., description="Flat list of all timeline items in this page, newest first")
    next_cursor:     Optional[str]            = Field(None, description="Cursor for next page (epoch timestamp float string). Pass as 'cursor' to get older events.")
    has_more:        bool                     = Field(False, description="True if older items exist beyond this page")
    total_count:     int                      = Field(0, description="Total number of items returned in this page")
    filters_applied: TimelineFilterMetadata   = Field(..., description="Applied query filters")
