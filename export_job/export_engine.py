import datetime
import io
import logging
import zipfile
from typing import Any, Dict, Optional
from google.cloud import firestore

from common_code.config import settings
from common_code.firestore import get_db, log_audit_event
from export_job.fetchers.consultations_fetcher import fetch_and_bundle_consultations
from export_job.fetchers.documents_fetcher import fetch_and_bundle_documents
from export_job.fetchers.profile_fetcher import fetch_profile_data
from export_job.fetchers.reminders_fetcher import fetch_reminders_data
from export_job.fetchers.vitals_fetcher import fetch_vitals_data
from export_job.gcs_storage import create_24h_signed_url, sanitize_filename, upload_export_zip
from export_job.notifier import send_export_completed_notification
from export_job.pdf_generator import (
    generate_comprehensive_medical_dossier_pdf,
    generate_consultations_pdf,
    generate_documents_index_pdf,
    generate_profile_pdf,
    generate_reminders_pdf,
    generate_single_consultation_pdf,
    generate_vitals_pdf,
)

logger = logging.getLogger(__name__)


def generate_readme(
    export_id: str,
    patient_id: str,
    patient_name: str,
    categories: list[str],
    start_date_str: Optional[str],
    end_date_str: Optional[str],
    counts: dict,
    zip_file: zipfile.ZipFile,
) -> None:
    """Generates a human-friendly README.txt into the root of the ZIP archive."""
    readme_content = f"""================================================================================
MEDHX AI HEALTH COMPANION — PATIENT MEDICAL DATA DOSSIER
================================================================================
Patient Name   : {patient_name or 'Patient'}
Patient ID/UHID: {patient_id}
Export ID      : {export_id}
Generated On   : {datetime.datetime.now(datetime.UTC).strftime("%d %B %Y at %H:%M:%S UTC")}
Date Filter    : {start_date_str or 'Earliest'} to {end_date_str or 'Latest'}
Categories     : {', '.join(categories)}

ARCHIVE CONTENTS & STRUCTURE:
--------------------------------------------------------------------------------
1. Comprehensive_Medical_Summary.pdf
   - Complete executive health dossier combining profile, vitals, doctor
     consultations, active prescriptions, and medical report inventories.

2. Categorized Medical PDF Reports:
   - 1_Patient_Profile_and_Emergency_Passport.pdf
     Personal baseline, blood group, allergies, chronic conditions, and emergency card.
   - 2_Vitals_and_Biometrics_Log.pdf
     Complete historical log of Blood Pressure, Blood Glucose, Heart Rate, SpO2, and BMI.
   - 3_Clinical_Consultations_and_Prescriptions.pdf
     Chronological master history of all doctor & AI consultations and prescriptions.
   - 4_Medication_and_Reminders_Schedule.pdf
     Active prescription timings, dosage routines, and meal relationships.
   - 5_Uploaded_Documents_and_Reports_Index.pdf
     Index of all uploaded medical documents, lab tests, and AI OCR summaries.

3. Individual Encounters & Raw Assets:
   • Consultations/
     Dedicated standalone encounter PDF report for EACH individual consultation.
   • Documents/
     Original lab test reports, PDF scans, prescriptions, and images uploaded by you.
   • Consultation_Audio/
     Original audio consultation recordings.

TOTAL ARCHIVED ITEMS:
--------------------------------------------------------------------------------
• Vitals Logs Recorded        : {counts.get('vitals', 0)}
• Consultations & Visits      : {counts.get('consultations', 0)}
• Medication Schedules        : {counts.get('reminders', 0)}
• Uploaded Documents / Scans  : {counts.get('documents', 0)} ({counts.get('document_files', 0)} original files)
• Audio Consult Recordings    : {counts.get('audio_files', 0)}

SECURITY & CONFIDENTIALITY NOTICE:
This archive contains Protected Health Information (PHI). Please keep this archive
in a secure and encrypted storage environment.
================================================================================
"""
    zip_file.writestr("README.txt", readme_content)


async def execute_export(
    export_id: str,
    patient_id: str,
    db: Optional[firestore.AsyncClient] = None,
) -> Dict[str, Any]:
    """
    Main export execution engine:
    1. Validates ownership.
    2. Fetches clinical data across all requested categories.
    3. Generates beautiful, styled, human-readable PDF reports.
    4. Preserves original user-uploaded documents and audio recordings.
    5. Packages everything into a clean user ZIP archive (without raw JSON/CSV).
    6. Uploads to GCS and generates 24-hour signed download URL.
    7. Dispatches FCM completion notification.
    """
    if db is None:
        db = get_db()

    logger.info(f"Starting PDF medical data export: export_id={export_id}, patient_id={patient_id}")
    export_ref = db.collection(settings.EXPORTS_COLLECTION).document(export_id)
    doc_snap = await export_ref.get()

    if not doc_snap.exists:
        raise ValueError(f"Export request document '{export_id}' does not exist.")

    export_meta = doc_snap.to_dict() or {}

    # Strict ownership validation
    if export_meta.get("patient_id") != patient_id:
        raise PermissionError(f"Unauthorized: Export '{export_id}' does not belong to patient '{patient_id}'.")

    await export_ref.update({
        "status": "processing",
        "updated_at": datetime.datetime.now(datetime.UTC),
    })

    try:
        categories = export_meta.get("categories", ["all"])
        export_all = "all" in categories

        start_date_str = export_meta.get("start_date")
        end_date_str = export_meta.get("end_date")
        include_raw_files = export_meta.get("include_raw_files", True)

        start_dt: Optional[datetime.datetime] = None
        end_dt: Optional[datetime.datetime] = None

        if start_date_str:
            d = datetime.date.fromisoformat(start_date_str)
            start_dt = datetime.datetime.combine(d, datetime.time.min, tzinfo=datetime.UTC)
        if end_date_str:
            d = datetime.date.fromisoformat(end_date_str)
            end_dt = datetime.datetime.combine(d, datetime.time.max, tzinfo=datetime.UTC)

        counts = {
            "profile": 0,
            "consultations": 0,
            "documents": 0,
            "vitals": 0,
            "reminders": 0,
            "audio_files": 0,
            "document_files": 0,
            "raw_files": 0,
            "pdf_reports": 0,
        }

        zip_buffer = io.BytesIO()
        with zipfile.ZipFile(zip_buffer, "w", zipfile.ZIP_DEFLATED) as zip_file:

            # 1. Fetch Profile Data
            user_data, patient_data = await fetch_profile_data(patient_id=patient_id, db=db)
            patient_name = user_data.get("name") or patient_data.get("name") or "Patient"

            # 2. Fetch Vitals Data
            vitals_list = []
            if export_all or "vitals" in categories:
                vitals_list = await fetch_vitals_data(patient_id=patient_id, db=db, start_dt=start_dt, end_dt=end_dt)
                counts["vitals"] = len(vitals_list)

            # 3. Fetch Consultations & Raw Audio Recordings
            consults_list = []
            if export_all or "consultations" in categories:
                consults_list, audio_count = await fetch_and_bundle_consultations(
                    patient_id=patient_id,
                    db=db,
                    zip_file=zip_file,
                    start_dt=start_dt,
                    end_dt=end_dt,
                    include_audio_files=include_raw_files,
                )
                counts["consultations"] = len(consults_list)
                counts["audio_files"] = audio_count

            # 4. Fetch Documents & Raw Uploaded Files
            docs_list = []
            if export_all or "documents" in categories:
                docs_list, doc_files_count = await fetch_and_bundle_documents(
                    patient_id=patient_id,
                    db=db,
                    zip_file=zip_file,
                    start_dt=start_dt,
                    end_dt=end_dt,
                    include_raw_files=include_raw_files,
                )
                counts["documents"] = len(docs_list)
                counts["document_files"] = doc_files_count

            # 5. Fetch Reminders
            reminders_list = []
            if export_all or "reminders" in categories:
                reminders_list = await fetch_reminders_data(patient_id=patient_id, db=db)
                counts["reminders"] = len(reminders_list)

            counts["raw_files"] = counts["audio_files"] + counts["document_files"]

            # ──────────────────────────────────────────────────────────────────
            # GENERATE BEAUTIFUL USER-READABLE PDF REPORTS
            # ──────────────────────────────────────────────────────────────────

            # Master Dossier
            dossier_pdf = generate_comprehensive_medical_dossier_pdf(
                user_data=user_data,
                patient_data=patient_data,
                vitals_list=vitals_list,
                consultations_list=consults_list,
                reminders_list=reminders_list,
                documents_list=docs_list,
            )
            zip_file.writestr("Comprehensive_Medical_Summary.pdf", dossier_pdf)
            counts["pdf_reports"] += 1

            # Individual Profile PDF
            if export_all or "profile" in categories:
                profile_pdf = generate_profile_pdf(user_data=user_data, patient_data=patient_data)
                zip_file.writestr("1_Patient_Profile_and_Emergency_Passport.pdf", profile_pdf)
                counts["profile"] = 1
                counts["pdf_reports"] += 1

            # Individual Vitals PDF
            if export_all or "vitals" in categories:
                vitals_pdf = generate_vitals_pdf(vitals_list=vitals_list, patient_name=patient_name, patient_id=patient_id)
                zip_file.writestr("2_Vitals_and_Biometrics_Log.pdf", vitals_pdf)
                counts["pdf_reports"] += 1

            # Consultations PDFs (Master summary + individual encounter PDFs)
            if export_all or "consultations" in categories:
                consult_pdf = generate_consultations_pdf(consultations_list=consults_list, patient_name=patient_name, patient_id=patient_id)
                zip_file.writestr("3_Clinical_Consultations_and_Prescriptions.pdf", consult_pdf)
                counts["pdf_reports"] += 1

                # Generate a separate individual PDF for EACH consultation encounter
                for idx, c in enumerate(consults_list):
                    c_single_pdf = generate_single_consultation_pdf(
                        consultation_data=c,
                        patient_name=patient_name,
                        patient_id=patient_id,
                    )
                    c_at = c.get("created_at") or c.get("createdAt")
                    date_prefix = c_at.strftime("%Y%m%d") if isinstance(c_at, datetime.datetime) else "encounter"
                    raw_title = c.get("title") or c.get("doctor_name") or "consultation"
                    safe_title = sanitize_filename(f"{date_prefix}_{raw_title}_{idx + 1}")
                    
                    zip_file.writestr(f"Consultations/{safe_title}.pdf", c_single_pdf)
                    counts["pdf_reports"] += 1

            # Individual Reminders PDF
            if export_all or "reminders" in categories:
                reminders_pdf = generate_reminders_pdf(reminders_list=reminders_list, patient_name=patient_name, patient_id=patient_id)
                zip_file.writestr("4_Medication_and_Reminders_Schedule.pdf", reminders_pdf)
                counts["pdf_reports"] += 1

            # Individual Documents Index PDF
            if export_all or "documents" in categories:
                docs_pdf = generate_documents_index_pdf(documents_list=docs_list, patient_name=patient_name, patient_id=patient_id)
                zip_file.writestr("5_Uploaded_Documents_and_Reports_Index.pdf", docs_pdf)
                counts["pdf_reports"] += 1

            # User README
            generate_readme(
                export_id=export_id,
                patient_id=patient_id,
                patient_name=patient_name,
                categories=categories,
                start_date_str=start_date_str,
                end_date_str=end_date_str,
                counts=counts,
                zip_file=zip_file,
            )

        zip_bytes = zip_buffer.getvalue()
        file_size = len(zip_bytes)
        zip_filename = f"medhx_medical_archive_{patient_id[:8]}_{datetime.datetime.now(datetime.UTC).strftime('%Y%m%d_%H%M%S')}.zip"
        gcs_blob_name = f"exports/{patient_id}/{export_id}.zip"

        logger.info(f"Uploading PDF medical archive to GCS: {gcs_blob_name} ({file_size} bytes, {counts['pdf_reports']} PDFs)...")
        await upload_export_zip(blob_name=gcs_blob_name, zip_bytes=zip_bytes)

        # Generate 24-hour V4 signed download URL (1440 minutes)
        signed_url = create_24h_signed_url(blob_name=gcs_blob_name)
        expires_at = datetime.datetime.now(datetime.UTC) + datetime.timedelta(hours=24)

        # Update Firestore
        update_payload = {
            "status": "completed",
            "download_url": signed_url,
            "download_url_expires_at": expires_at,
            "file_size_bytes": file_size,
            "file_name": zip_filename,
            "gcs_blob_name": gcs_blob_name,
            "exported_counts": counts,
            "completed_at": datetime.datetime.now(datetime.UTC),
            "updated_at": datetime.datetime.now(datetime.UTC),
        }
        await export_ref.update(update_payload)
        logger.info(f"Export {export_id} completed successfully. Signed URL valid for 24h.")

        # Dispatch FCM Push Notification
        await send_export_completed_notification(
            patient_id=patient_id,
            export_id=export_id,
            download_url=signed_url,
            expires_at=expires_at,
            file_size_bytes=file_size,
        )

        # Audit log
        await log_audit_event(
            actor=patient_id,
            action="EXPORT_USER_DATA",
            target=patient_id,
            details={"export_id": export_id, "file_size_bytes": file_size, "counts": counts},
        )

        return update_payload

    except Exception as e:
        logger.error(f"Export {export_id} failed: {e}", exc_info=True)
        await export_ref.update({
            "status": "failed",
            "error_message": str(e),
            "updated_at": datetime.datetime.now(datetime.UTC),
        })
        raise
