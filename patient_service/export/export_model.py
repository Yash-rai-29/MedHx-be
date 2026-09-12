from datetime import date, datetime
from enum import Enum
from typing import List, Optional
from pydantic import BaseModel, ConfigDict, Field


class ExportCategory(str, Enum):
    all = "all"
    consultations = "consultations"
    documents = "documents"
    vitals = "vitals"
    reminders = "reminders"
    profile = "profile"


class ExportStatus(str, Enum):
    pending = "pending"
    processing = "processing"
    completed = "completed"
    failed = "failed"


class ExportCreateRequest(BaseModel):
    """
    Request model for initiating a patient clinical data export.
    Supports granular category and date filtering.
    """
    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "categories": ["all"],
                "start_date": "2026-01-01",
                "end_date": "2026-08-15",
                "include_raw_files": True,
            }
        }
    )

    categories: List[ExportCategory] = Field(
        default=[ExportCategory.all],
        description="Data categories to export (consultations, documents, vitals, reminders, profile, or all)",
    )
    start_date: Optional[date] = Field(
        None,
        description="Optional filter: export data created on or after this date (YYYY-MM-DD)",
    )
    end_date: Optional[date] = Field(
        None,
        description="Optional filter: export data created on or before this date (YYYY-MM-DD)",
    )
    include_raw_files: bool = Field(
        default=True,
        description="Whether to include original uploaded document files (PDFs/images) in the zip archive",
    )


class ExportItemCounts(BaseModel):
    profile: int = Field(0, description="Number of profile records")
    consultations: int = Field(0, description="Number of doctor and audio consultations")
    documents: int = Field(0, description="Number of clinical documents and lab reports")
    vitals: int = Field(0, description="Number of vital sign logs")
    reminders: int = Field(0, description="Number of medication and follow-up reminders")
    raw_files: int = Field(0, description="Number of binary files (PDFs/images) bundled in zip")


class ExportStatusResponse(BaseModel):
    """
    Status and download details for a patient data export request.
    """
    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "export_id": "exp_7f9a12b4e8",
                "patient_id": "JHjCVAYCZVQ7Fixs1ivTuiP3idl1",
                "status": "completed",
                "categories": ["all"],
                "start_date": "2026-01-01",
                "end_date": "2026-08-15",
                "created_at": "2026-08-16T10:00:00Z",
                "completed_at": "2026-08-16T10:00:15Z",
                "download_url": "https://storage.googleapis.com/medhx-care-media/exports/...",
                "download_url_expires_at": "2026-08-17T10:00:15Z",
                "file_size_bytes": 1048576,
                "file_name": "medhx_export_JHjCVAY_20260816.zip",
                "exported_counts": {
                    "profile": 1,
                    "consultations": 4,
                    "documents": 12,
                    "vitals": 124,
                    "reminders": 3,
                    "raw_files": 12,
                },
                "error_message": None,
            }
        }
    )

    export_id: str = Field(..., description="Unique ID for this export job")
    patient_id: str = Field(..., description="Patient UID who requested the export")
    status: ExportStatus = Field(..., description="Current status: pending, processing, completed, failed")
    categories: List[str] = Field(..., description="Categories included in this export")
    start_date: Optional[date] = Field(None, description="Start date filter applied")
    end_date: Optional[date] = Field(None, description="End date filter applied")
    created_at: datetime = Field(..., description="Timestamp when export was requested")
    completed_at: Optional[datetime] = Field(None, description="Timestamp when export finished processing")
    download_url: Optional[str] = Field(None, description="24-hour secure V4 signed download URL for the ZIP file")
    download_url_expires_at: Optional[datetime] = Field(None, description="Exact expiry time for the signed URL (24h from generation)")
    file_size_bytes: Optional[int] = Field(None, description="Size of the exported ZIP archive in bytes")
    file_name: Optional[str] = Field(None, description="Human-readable filename of the archive")
    exported_counts: Optional[ExportItemCounts] = Field(None, description="Count of exported items per domain")
    error_message: Optional[str] = Field(None, description="Error details if job failed")


class ExportHistoryResponse(BaseModel):
    """
    Paginated list of previous exports for the authenticated patient.
    """
    exports: List[ExportStatusResponse] = Field(..., description="List of previous export requests, newest first")
    total_count: int = Field(..., description="Total number of export requests found")
