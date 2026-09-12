import datetime
import io
from typing import Any, Dict, List, Optional

from reportlab.lib import colors
from reportlab.lib.pagesizes import letter
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import inch
from reportlab.pdfgen import canvas
from reportlab.platypus import (
    HRFlowable,
    KeepTogether,
    PageBreak,
    Paragraph,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)


class NumberedCanvas(canvas.Canvas):
    """
    Two-pass canvas to dynamically compute and print 'Page X of Y' along with
    a sleek confidential medical report footer.
    """
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._saved_page_states = []

    def showPage(self):
        self._saved_page_states.append(dict(self.__dict__))
        self._startPage()

    def save(self):
        num_pages = len(self._saved_page_states)
        for state in self._saved_page_states:
            self.__dict__.update(state)
            self.draw_page_decorations(num_pages)
            super().showPage()
        super().save()

    def draw_page_decorations(self, page_count: int):
        self.saveState()
        self.setFont("Helvetica", 8)
        self.setFillColor(colors.HexColor("#64748B"))

        # Top subtle running header (on pages > 1)
        if self._pageNumber > 1:
            self.drawString(40, 762, "MedHx AI Health Companion — Confidential Medical Dossier")
            self.setStrokeColor(colors.HexColor("#E2E8F0"))
            self.setLineWidth(0.5)
            self.line(40, 756, 572, 756)

        # Bottom footer
        self.setStrokeColor(colors.HexColor("#E2E8F0"))
        self.setLineWidth(0.5)
        self.line(40, 40, 572, 40)

        # Footer Text
        footer_text = f"Generated on {datetime.datetime.now(datetime.UTC).strftime('%d %b %Y, %H:%M UTC')} • Strictly Confidential (Personal Health Record)"
        self.drawString(40, 28, footer_text)
        page_str = f"Page {self._pageNumber} of {page_count}"
        self.drawRightString(572, 28, page_str)

        self.restoreState()


def get_custom_styles():
    """Generates typography and hierarchy styles for medical reports."""
    styles = getSampleStyleSheet()

    title_style = ParagraphStyle(
        "DocTitle",
        parent=styles["Normal"],
        fontName="Helvetica-Bold",
        fontSize=20,
        leading=24,
        textColor=colors.HexColor("#0F172A"),
        spaceAfter=4,
    )

    subtitle_style = ParagraphStyle(
        "DocSubTitle",
        parent=styles["Normal"],
        fontName="Helvetica",
        fontSize=10,
        leading=13,
        textColor=colors.HexColor("#0F766E"),
    )

    h1_style = ParagraphStyle(
        "SectionH1",
        parent=styles["Normal"],
        fontName="Helvetica-Bold",
        fontSize=13,
        leading=16,
        textColor=colors.HexColor("#0F766E"),
        spaceBefore=12,
        spaceAfter=6,
    )

    h2_style = ParagraphStyle(
        "SectionH2",
        parent=styles["Normal"],
        fontName="Helvetica-Bold",
        fontSize=10,
        leading=13,
        textColor=colors.HexColor("#1E293B"),
        spaceBefore=6,
        spaceAfter=4,
    )

    body_style = ParagraphStyle(
        "BodyTextDark",
        parent=styles["Normal"],
        fontName="Helvetica",
        fontSize=8.5,
        leading=11.5,
        textColor=colors.HexColor("#334155"),
    )

    body_bold = ParagraphStyle(
        "BodyTextBold",
        parent=styles["Normal"],
        fontName="Helvetica-Bold",
        fontSize=8.5,
        leading=11.5,
        textColor=colors.HexColor("#0F172A"),
    )

    badge_normal = ParagraphStyle(
        "BadgeNormal",
        parent=styles["Normal"],
        fontName="Helvetica-Bold",
        fontSize=7.5,
        leading=9,
        textColor=colors.HexColor("#15803D"),
        alignment=1, # Center
    )

    badge_alert = ParagraphStyle(
        "BadgeAlert",
        parent=styles["Normal"],
        fontName="Helvetica-Bold",
        fontSize=7.5,
        leading=9,
        textColor=colors.HexColor("#BE123C"),
        alignment=1,
    )

    badge_neutral = ParagraphStyle(
        "BadgeNeutral",
        parent=styles["Normal"],
        fontName="Helvetica",
        fontSize=7.5,
        leading=9,
        textColor=colors.HexColor("#475569"),
        alignment=1,
    )

    table_header = ParagraphStyle(
        "TableHeader",
        parent=styles["Normal"],
        fontName="Helvetica-Bold",
        fontSize=8,
        leading=10,
        textColor=colors.HexColor("#0F172A"),
    )

    table_cell = ParagraphStyle(
        "TableCell",
        parent=styles["Normal"],
        fontName="Helvetica",
        fontSize=7.5,
        leading=10,
        textColor=colors.HexColor("#334155"),
    )

    return {
        "title": title_style,
        "subtitle": subtitle_style,
        "h1": h1_style,
        "h2": h2_style,
        "body": body_style,
        "body_bold": body_bold,
        "badge_normal": badge_normal,
        "badge_alert": badge_alert,
        "badge_neutral": badge_neutral,
        "table_header": table_header,
        "table_cell": table_cell,
    }


def _create_header_banner(title: str, subtitle: str, patient_name: str, patient_id: str, styles: dict) -> List[Any]:
    """Builds a modern medical dossier brand header."""
    header_data = [
        [
            Paragraph(f"<b>MedHx</b> <font color='#0D9488'>AI Health</font>", styles["title"]),
            Paragraph(f"<b>PATIENT:</b> {patient_name or 'Patient'}<br/><font color='#64748B'>UHID: {patient_id}</font>", styles["subtitle"]),
        ],
        [
            Paragraph(f"<b>{title}</b>", styles["h1"]),
            Paragraph(f"<b>Generated:</b> {datetime.datetime.now(datetime.UTC).strftime('%d %B %Y')}<br/><font color='#64748B'>{subtitle}</font>", styles["body"]),
        ]
    ]

    t = Table(header_data, colWidths=[3.6 * inch, 3.8 * inch])
    t.setStyle(TableStyle([
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("ALIGN", (1, 0), (1, -1), "RIGHT"),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
        ("TOPPADDING", (0, 0), (-1, -1), 2),
    ]))
    return [t, HRFlowable(width="100%", thickness=1.5, color=colors.HexColor("#0D9488"), spaceAfter=10, spaceBefore=4)]


def generate_profile_pdf(user_data: dict, patient_data: dict) -> bytes:
    """Generates the Patient Profile & Emergency Passport PDF."""
    buffer = io.BytesIO()
    doc = SimpleDocTemplate(
        buffer,
        pagesize=letter,
        leftMargin=36,
        rightMargin=36,
        topMargin=36,
        bottomMargin=46,
    )
    styles = get_custom_styles()
    story = []

    patient_name = user_data.get("name") or patient_data.get("name") or "Patient"
    patient_id = user_data.get("uid") or patient_data.get("id") or "N/A"

    story.extend(_create_header_banner("Patient Health Passport & Profile", "Comprehensive Clinical Baseline", patient_name, patient_id, styles))

    # Demographics & Account info table
    story.append(Paragraph("1. Personal & Contact Information", styles["h1"]))

    dob = patient_data.get("date_of_birth") or user_data.get("date_of_birth") or "Not recorded"
    age = patient_data.get("age") or user_data.get("age") or "N/A"
    gender = patient_data.get("gender") or "Not recorded"
    blood_group = patient_data.get("blood_group") or "Not recorded"
    phone = user_data.get("phone") or "Not recorded"
    email = user_data.get("email") or "Not recorded"
    location = patient_data.get("location") or user_data.get("location") or "Not recorded"
    lang = (user_data.get("language_preference") or "en").upper()

    demo_data = [
        [
            Paragraph("<b>Full Name:</b>", styles["body_bold"]), Paragraph(str(patient_name), styles["body"]),
            Paragraph("<b>Blood Group:</b>", styles["body_bold"]), Paragraph(f"<font color='#BE123C'><b>{blood_group}</b></font>", styles["body"]),
        ],
        [
            Paragraph("<b>Date of Birth:</b>", styles["body_bold"]), Paragraph(str(dob), styles["body"]),
            Paragraph("<b>Calculated Age:</b>", styles["body_bold"]), Paragraph(f"{age} yrs", styles["body"]),
        ],
        [
            Paragraph("<b>Gender:</b>", styles["body_bold"]), Paragraph(str(gender).capitalize(), styles["body"]),
            Paragraph("<b>Language:</b>", styles["body_bold"]), Paragraph(str(lang), styles["body"]),
        ],
        [
            Paragraph("<b>Phone:</b>", styles["body_bold"]), Paragraph(str(phone), styles["body"]),
            Paragraph("<b>Email:</b>", styles["body_bold"]), Paragraph(str(email), styles["body"]),
        ],
        [
            Paragraph("<b>Location / City:</b>", styles["body_bold"]), Paragraph(str(location), styles["body"]),
            Paragraph("<b>Account Status:</b>", styles["body_bold"]), Paragraph(str(user_data.get("account_status", "active")).title(), styles["body"]),
        ],
    ]

    t_demo = Table(demo_data, colWidths=[1.5 * inch, 2.2 * inch, 1.5 * inch, 2.2 * inch])
    t_demo.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, -1), colors.HexColor("#F8FAFC")),
        ("BOX", (0, 0), (-1, -1), 0.5, colors.HexColor("#E2E8F0")),
        ("INNERGRID", (0, 0), (-1, -1), 0.5, colors.HexColor("#E2E8F0")),
        ("TOPPADDING", (0, 0), (-1, -1), 4),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
    ]))
    story.append(t_demo)
    story.append(Spacer(1, 10))

    # Emergency Contact
    ec = patient_data.get("emergency_contact") or {}
    ec_name = ec.get("name") if isinstance(ec, dict) else "None documented"
    ec_phone = ec.get("phone") if isinstance(ec, dict) else "N/A"

    story.append(Paragraph("2. Emergency Contact", styles["h1"]))
    ec_data = [
        [Paragraph("<b>Emergency Contact Person:</b>", styles["body_bold"]), Paragraph(str(ec_name), styles["body"])],
        [Paragraph("<b>Emergency Phone Number:</b>", styles["body_bold"]), Paragraph(f"<font color='#0D9488'><b>{ec_phone}</b></font>", styles["body"])],
    ]
    t_ec = Table(ec_data, colWidths=[2.2 * inch, 5.2 * inch])
    t_ec.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, -1), colors.HexColor("#F0FDFA")),
        ("BOX", (0, 0), (-1, -1), 0.5, colors.HexColor("#99F6E4")),
        ("INNERGRID", (0, 0), (-1, -1), 0.5, colors.HexColor("#CCFBF1")),
        ("TOPPADDING", (0, 0), (-1, -1), 4),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
    ]))
    story.append(t_ec)
    story.append(Spacer(1, 10))

    # Clinical Baseline & Medical History
    story.append(Paragraph("3. Clinical Baseline & Medical History", styles["h1"]))

    allergies = ", ".join(patient_data.get("allergies", [])) or "No known drug/food allergies"
    chronic = ", ".join(patient_data.get("chronic_conditions", [])) or "None documented"
    meds = ", ".join(patient_data.get("current_medications", [])) or "None documented"
    surgeries = ", ".join(patient_data.get("past_surgeries", [])) or "None documented"
    family = ", ".join(patient_data.get("family_history", [])) or "None documented"

    clin_data = [
        [Paragraph("<b>Known Allergies:</b>", styles["body_bold"]), Paragraph(f"<font color='#BE123C'><b>{allergies}</b></font>", styles["body"])],
        [Paragraph("<b>Chronic Conditions:</b>", styles["body_bold"]), Paragraph(str(chronic), styles["body"])],
        [Paragraph("<b>Ongoing Medications:</b>", styles["body_bold"]), Paragraph(str(meds), styles["body"])],
        [Paragraph("<b>Past Surgeries:</b>", styles["body_bold"]), Paragraph(str(surgeries), styles["body"])],
        [Paragraph("<b>Family Medical History:</b>", styles["body_bold"]), Paragraph(str(family), styles["body"])],
    ]

    t_clin = Table(clin_data, colWidths=[2.2 * inch, 5.2 * inch])
    t_clin.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, -1), colors.HexColor("#F8FAFC")),
        ("BOX", (0, 0), (-1, -1), 0.5, colors.HexColor("#E2E8F0")),
        ("INNERGRID", (0, 0), (-1, -1), 0.5, colors.HexColor("#E2E8F0")),
        ("TOPPADDING", (0, 0), (-1, -1), 5),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
    ]))
    story.append(t_clin)
    story.append(Spacer(1, 10))

    # Meal Times Routine
    mt = patient_data.get("meal_times", {}) or {}
    story.append(Paragraph("4. Daily Meal Timing Routine", styles["h1"]))
    mt_data = [
        [
            Paragraph("<b>Breakfast Time:</b>", styles["body_bold"]), Paragraph(mt.get("breakfast", "08:30"), styles["body"]),
            Paragraph("<b>Lunch Time:</b>", styles["body_bold"]), Paragraph(mt.get("lunch", "13:30"), styles["body"]),
            Paragraph("<b>Dinner Time:</b>", styles["body_bold"]), Paragraph(mt.get("dinner", "20:30"), styles["body"]),
        ]
    ]
    t_mt = Table(mt_data, colWidths=[1.3 * inch, 1.1 * inch, 1.3 * inch, 1.1 * inch, 1.3 * inch, 1.3 * inch])
    t_mt.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, -1), colors.HexColor("#F8FAFC")),
        ("BOX", (0, 0), (-1, -1), 0.5, colors.HexColor("#E2E8F0")),
        ("INNERGRID", (0, 0), (-1, -1), 0.5, colors.HexColor("#E2E8F0")),
        ("TOPPADDING", (0, 0), (-1, -1), 4),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
    ]))
    story.append(t_mt)

    doc.build(story, canvasmaker=NumberedCanvas)
    return buffer.getvalue()


def generate_vitals_pdf(vitals_list: List[dict], patient_name: str, patient_id: str) -> bytes:
    """Generates the Vitals & Biometric Logs Report PDF."""
    buffer = io.BytesIO()
    doc = SimpleDocTemplate(
        buffer,
        pagesize=letter,
        leftMargin=36,
        rightMargin=36,
        topMargin=36,
        bottomMargin=46,
    )
    styles = get_custom_styles()
    story = []

    story.extend(_create_header_banner("Vitals & Biometric Readings Log", f"Total Logged Measurements: {len(vitals_list)}", patient_name, patient_id, styles))

    if not vitals_list:
        story.append(Paragraph("<i>No vital sign logs recorded for the selected period.</i>", styles["body"]))
        doc.build(story, canvasmaker=NumberedCanvas)
        return buffer.getvalue()

    # Vitals Table
    headers = [
        Paragraph("<b>Date & Time</b>", styles["table_header"]),
        Paragraph("<b>Blood Pressure</b><br/><font color='#64748B' size=6>(mmHg)</font>", styles["table_header"]),
        Paragraph("<b>Heart Rate</b><br/><font color='#64748B' size=6>(bpm)</font>", styles["table_header"]),
        Paragraph("<b>Blood Sugar</b><br/><font color='#64748B' size=6>(mg/dL)</font>", styles["table_header"]),
        Paragraph("<b>SpO2 / Temp</b>", styles["table_header"]),
        Paragraph("<b>Weight / BMI</b>", styles["table_header"]),
        Paragraph("<b>Notes / Source</b>", styles["table_header"]),
    ]

    table_data = [headers]

    for idx, v in enumerate(vitals_list):
        m_at = v.get("measured_at") or v.get("recordedAt") or v.get("logged_at")
        date_str = ""
        if isinstance(m_at, datetime.datetime):
            date_str = m_at.strftime("%d %b %Y<br/>%H:%M")
        elif isinstance(m_at, str):
            date_str = m_at[:16].replace("T", "<br/>")

        # Blood pressure
        sys = v.get("systolic")
        dia = v.get("diastolic")
        bp_str = f"<b>{sys}/{dia}</b>" if (sys and dia) else "-"

        # Heart rate
        hr = v.get("heart_rate") or v.get("pulse")
        hr_str = f"{hr} bpm" if hr else "-"

        # Glucose
        g_fast = v.get("glucose_fasting")
        g_post = v.get("glucose_post_meal")
        g_rand = v.get("glucose_random") or v.get("glucose")
        glu_parts = []
        if g_fast:
            glu_parts.append(f"Fast: {g_fast}")
        if g_post:
            glu_parts.append(f"Post: {g_post}")
        if g_rand:
            glu_parts.append(f"Rnd: {g_rand}")
        glu_str = "<br/>".join(glu_parts) if glu_parts else "-"

        # SpO2 / Temp
        spo2 = v.get("spo2")
        temp = v.get("temperature")
        st_parts = []
        if spo2:
            st_parts.append(f"SpO2: {spo2}%")
        if temp:
            st_parts.append(f"Temp: {temp}°C")
        st_str = "<br/>".join(st_parts) if st_parts else "-"

        # Weight / BMI
        wt = v.get("weight")
        bmi = v.get("bmi")
        cat = v.get("bmi_category") or v.get("category") or ""
        wb_parts = []
        if wt:
            wb_parts.append(f"{wt} kg")
        if bmi:
            wb_parts.append(f"BMI: {bmi} ({cat})")
        wb_str = "<br/>".join(wb_parts) if wb_parts else "-"

        notes = v.get("notes") or v.get("device_source") or "-"

        row = [
            Paragraph(date_str, styles["table_cell"]),
            Paragraph(bp_str, styles["table_cell"]),
            Paragraph(hr_str, styles["table_cell"]),
            Paragraph(glu_str, styles["table_cell"]),
            Paragraph(st_str, styles["table_cell"]),
            Paragraph(wb_str, styles["table_cell"]),
            Paragraph(str(notes)[:50], styles["table_cell"]),
        ]
        table_data.append(row)

    col_widths = [1.0 * inch, 1.0 * inch, 0.8 * inch, 1.1 * inch, 1.0 * inch, 1.2 * inch, 1.3 * inch]
    t = Table(table_data, colWidths=col_widths, repeatRows=1)

    t_style = [
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#F1F5F9")),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
        ("TOPPADDING", (0, 0), (-1, -1), 4),
        ("GRID", (0, 0), (-1, -1), 0.5, colors.HexColor("#E2E8F0")),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
    ]

    for i in range(1, len(table_data)):
        if i % 2 == 0:
            t_style.append(("BACKGROUND", (0, i), (-1, i), colors.HexColor("#F8FAFC")))

    t.setStyle(TableStyle(t_style))
    story.append(t)

    doc.build(story, canvasmaker=NumberedCanvas)
    return buffer.getvalue()


def generate_consultations_pdf(consultations_list: List[dict], patient_name: str, patient_id: str) -> bytes:
    """Generates the Clinical Consultations & Prescriptions PDF."""
    buffer = io.BytesIO()
    doc = SimpleDocTemplate(
        buffer,
        pagesize=letter,
        leftMargin=36,
        rightMargin=36,
        topMargin=36,
        bottomMargin=46,
    )
    styles = get_custom_styles()
    story = []

    story.extend(_create_header_banner("Clinical Consultations & Prescriptions", f"Total Consultation Records: {len(consultations_list)}", patient_name, patient_id, styles))

    if not consultations_list:
        story.append(Paragraph("<i>No consultation records found for this account.</i>", styles["body"]))
        doc.build(story, canvasmaker=NumberedCanvas)
        return buffer.getvalue()

    for idx, c in enumerate(consultations_list):
        c_title = c.get("title") or "Clinical Consultation"
        c_doctor = c.get("doctor_name") or c.get("doctorId") or "Attending Physician"
        c_type = "Audio AI Consultation" if c.get("type") == "audio_consultation" else "Doctor Encounter"
        
        c_at = c.get("created_at") or c.get("createdAt")
        date_str = ""
        if isinstance(c_at, datetime.datetime):
            date_str = c_at.strftime("%d %B %Y at %H:%M UTC")
        elif isinstance(c_at, str):
            date_str = c_at[:19].replace("T", " ")

        summary_text = c.get("summary") or c.get("summary_en") or c.get("chief_complaint") or "No detailed clinical summary recorded."
        diagnoses = c.get("key_diagnoses") or c.get("diagnoses") or []
        diag_str = ", ".join(diagnoses) if isinstance(diagnoses, list) else str(diagnoses)
        meds = c.get("medicines") or c.get("prescriptions") or []

        # Card Container
        card_content = []
        card_content.append(Paragraph(f"<b>{idx + 1}. {c_title}</b> — <font color='#0D9488'>{c_type}</font>", styles["h2"]))
        
        meta_table_data = [
            [Paragraph("<b>Doctor / Provider:</b>", styles["body_bold"]), Paragraph(str(c_doctor), styles["body"]),
             Paragraph("<b>Consultation Date:</b>", styles["body_bold"]), Paragraph(date_str, styles["body"])],
            [Paragraph("<b>Primary Diagnoses:</b>", styles["body_bold"]), Paragraph(f"<font color='#BE123C'><b>{diag_str or 'None documented'}</b></font>", styles["body"]),
             Paragraph("<b>Audio Attached:</b>", styles["body_bold"]), Paragraph("Yes (in Consultation_Audio/)" if (c.get("file_path") or c.get("audio_url")) else "No", styles["body"])],
        ]
        meta_t = Table(meta_table_data, colWidths=[1.5 * inch, 2.2 * inch, 1.5 * inch, 2.2 * inch])
        meta_t.setStyle(TableStyle([
            ("BACKGROUND", (0, 0), (-1, -1), colors.HexColor("#F8FAFC")),
            ("BOX", (0, 0), (-1, -1), 0.5, colors.HexColor("#E2E8F0")),
            ("INNERGRID", (0, 0), (-1, -1), 0.5, colors.HexColor("#E2E8F0")),
            ("TOPPADDING", (0, 0), (-1, -1), 3),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
        ]))
        card_content.append(meta_t)
        card_content.append(Spacer(1, 4))

        # Clinical Summary
        card_content.append(Paragraph("<b>Clinical Discussion & Findings:</b>", styles["body_bold"]))
        card_content.append(Paragraph(str(summary_text), styles["body"]))
        card_content.append(Spacer(1, 4))

        # Prescribed Medicines Table
        if meds:
            card_content.append(Paragraph("<b>Prescribed Medications:</b>", styles["body_bold"]))
            med_headers = [
                Paragraph("<b>Medicine Name</b>", styles["table_header"]),
                Paragraph("<b>Dosage</b>", styles["table_header"]),
                Paragraph("<b>Frequency / Timing</b>", styles["table_header"]),
                Paragraph("<b>Instructions</b>", styles["table_header"]),
            ]
            med_rows = [med_headers]
            for m in meds:
                if isinstance(m, dict):
                    m_name = m.get("name") or m.get("medicine_name") or "Medicine"
                    m_dose = m.get("dosage") or m.get("strength") or "-"
                    m_freq = m.get("frequency") or m.get("timing") or "-"
                    m_inst = m.get("instructions") or m.get("notes") or "-"
                else:
                    m_name = str(m)
                    m_dose, m_freq, m_inst = "-", "-", "-"
                med_rows.append([
                    Paragraph(f"<b>{m_name}</b>", styles["table_cell"]),
                    Paragraph(m_dose, styles["table_cell"]),
                    Paragraph(m_freq, styles["table_cell"]),
                    Paragraph(m_inst, styles["table_cell"]),
                ])
            med_t = Table(med_rows, colWidths=[2.2 * inch, 1.2 * inch, 1.8 * inch, 2.2 * inch])
            med_t.setStyle(TableStyle([
                ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#F1F5F9")),
                ("GRID", (0, 0), (-1, -1), 0.5, colors.HexColor("#E2E8F0")),
                ("TOPPADDING", (0, 0), (-1, -1), 3),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
            ]))
            card_content.append(med_t)

        card_content.append(HRFlowable(width="100%", thickness=0.5, color=colors.HexColor("#CBD5E1"), spaceBefore=8, spaceAfter=8))
        story.append(KeepTogether(card_content))

    doc.build(story, canvasmaker=NumberedCanvas)
    return buffer.getvalue()


def generate_single_consultation_pdf(consultation_data: dict, patient_name: str, patient_id: str) -> bytes:
    """
    Generates a dedicated standalone clinical consultation encounter PDF report.
    Includes Doctor metadata, diagnosis, clinical findings, prescription cards, and audio references.
    """
    buffer = io.BytesIO()
    doc = SimpleDocTemplate(
        buffer,
        pagesize=letter,
        leftMargin=36,
        rightMargin=36,
        topMargin=36,
        bottomMargin=46,
    )
    styles = get_custom_styles()
    story = []

    c_title = consultation_data.get("title") or "Clinical Consultation"
    c_doctor = consultation_data.get("doctor_name") or consultation_data.get("doctorId") or "Attending Physician"
    c_type = "Audio AI Consultation Encounter" if consultation_data.get("type") == "audio_consultation" else "Doctor Encounter Report"
    
    c_at = consultation_data.get("created_at") or consultation_data.get("createdAt")
    date_str = ""
    if isinstance(c_at, datetime.datetime):
        date_str = c_at.strftime("%d %B %Y at %H:%M UTC")
    elif isinstance(c_at, str):
        date_str = c_at[:19].replace("T", " ")

    story.extend(_create_header_banner(
        c_title,
        f"{c_type} • Date: {date_str}",
        patient_name,
        patient_id,
        styles,
    ))

    # SECTION 1: Encounter Metadata
    story.append(Paragraph("1. Encounter Details", styles["h1"]))
    diagnoses = consultation_data.get("key_diagnoses") or consultation_data.get("diagnoses") or []
    diag_str = ", ".join(diagnoses) if isinstance(diagnoses, list) else str(diagnoses)
    audio_path = consultation_data.get("file_path") or consultation_data.get("audio_url") or consultation_data.get("gcs_uri")
    cid = consultation_data.get("id", "audio")
    audio_ref = f"Yes (Archived in Consultation_Audio/{cid}.mp3)" if audio_path else "None"

    meta_rows = [
        [
            Paragraph("<b>Consulting Doctor / Provider:</b>", styles["body_bold"]), Paragraph(str(c_doctor), styles["body"]),
            Paragraph("<b>Encounter Date:</b>", styles["body_bold"]), Paragraph(date_str, styles["body"]),
        ],
        [
            Paragraph("<b>Primary Diagnoses:</b>", styles["body_bold"]), Paragraph(f"<font color='#BE123C'><b>{diag_str or 'None documented'}</b></font>", styles["body"]),
            Paragraph("<b>Audio Recording:</b>", styles["body_bold"]), Paragraph(f"<font color='#0D9488'>{audio_ref}</font>", styles["body"]),
        ],
    ]
    t_meta = Table(meta_rows, colWidths=[2.0 * inch, 2.0 * inch, 1.6 * inch, 1.8 * inch])
    t_meta.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, -1), colors.HexColor("#F8FAFC")),
        ("BOX", (0, 0), (-1, -1), 0.5, colors.HexColor("#E2E8F0")),
        ("INNERGRID", (0, 0), (-1, -1), 0.5, colors.HexColor("#E2E8F0")),
        ("TOPPADDING", (0, 0), (-1, -1), 4),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
    ]))
    story.append(t_meta)
    story.append(Spacer(1, 10))

    # SECTION 2: Clinical Discussion & Summary
    story.append(Paragraph("2. Clinical Discussion & Key Findings", styles["h1"]))
    summary_text = (
        consultation_data.get("summary")
        or consultation_data.get("summary_en")
        or consultation_data.get("chief_complaint")
        or "No clinical discussion recorded."
    )
    story.append(Paragraph(str(summary_text), styles["body"]))
    story.append(Spacer(1, 10))

    # SECTION 3: Prescribed Medications
    meds = consultation_data.get("medicines") or consultation_data.get("prescriptions") or []
    story.append(Paragraph("3. Prescribed Medications & Dosage Instructions", styles["h1"]))
    if meds:
        med_headers = [
            Paragraph("<b>Medicine Name</b>", styles["table_header"]),
            Paragraph("<b>Strength / Dosage</b>", styles["table_header"]),
            Paragraph("<b>Frequency / Timing</b>", styles["table_header"]),
            Paragraph("<b>Doctor Instructions & Meal Relation</b>", styles["table_header"]),
        ]
        med_rows = [med_headers]
        for m in meds:
            if isinstance(m, dict):
                m_name = m.get("name") or m.get("medicine_name") or "Medicine"
                m_dose = m.get("dosage") or m.get("strength") or "-"
                m_freq = m.get("frequency") or m.get("timing") or "-"
                m_inst = m.get("instructions") or m.get("notes") or "-"
            else:
                m_name = str(m)
                m_dose, m_freq, m_inst = "-", "-", "-"
            med_rows.append([
                Paragraph(f"<b>{m_name}</b>", styles["table_cell"]),
                Paragraph(m_dose, styles["table_cell"]),
                Paragraph(m_freq, styles["table_cell"]),
                Paragraph(m_inst, styles["table_cell"]),
            ])
        med_t = Table(med_rows, colWidths=[2.2 * inch, 1.4 * inch, 1.6 * inch, 2.2 * inch])
        med_t.setStyle(TableStyle([
            ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#F1F5F9")),
            ("GRID", (0, 0), (-1, -1), 0.5, colors.HexColor("#E2E8F0")),
            ("TOPPADDING", (0, 0), (-1, -1), 4),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
        ]))
        story.append(med_t)
    else:
        story.append(Paragraph("<i>No medications prescribed during this consultation encounter.</i>", styles["body"]))
    story.append(Spacer(1, 10))

    # SECTION 4: Follow-up & Recommendations
    reminders = consultation_data.get("reminder_suggestions") or []
    if reminders:
        story.append(Paragraph("4. Suggested Follow-Ups & Reminders", styles["h1"]))
        for r in reminders:
            if isinstance(r, dict):
                r_title = r.get("title") or r.get("medicine_name") or "Follow-up"
                r_time = r.get("time_of_day") or r.get("schedule") or ""
                story.append(Paragraph(f"• <b>{r_title}</b>: {r_time}", styles["body"]))
            else:
                story.append(Paragraph(f"• {r}", styles["body"]))

    doc.build(story, canvasmaker=NumberedCanvas)
    return buffer.getvalue()


def generate_reminders_pdf(reminders_list: List[dict], patient_name: str, patient_id: str) -> bytes:
    """Generates the Medication & Reminders Schedule PDF."""
    buffer = io.BytesIO()
    doc = SimpleDocTemplate(
        buffer,
        pagesize=letter,
        leftMargin=36,
        rightMargin=36,
        topMargin=36,
        bottomMargin=46,
    )
    styles = get_custom_styles()
    story = []

    story.extend(_create_header_banner("Medication & Healthcare Reminders Schedule", f"Total Configured Schedules: {len(reminders_list)}", patient_name, patient_id, styles))

    if not reminders_list:
        story.append(Paragraph("<i>No medication reminders scheduled for this account.</i>", styles["body"]))
        doc.build(story, canvasmaker=NumberedCanvas)
        return buffer.getvalue()

    headers = [
        Paragraph("<b>Reminder Title</b>", styles["table_header"]),
        Paragraph("<b>Type / Status</b>", styles["table_header"]),
        Paragraph("<b>Schedule / Recurrence</b>", styles["table_header"]),
        Paragraph("<b>Meal Timing</b>", styles["table_header"]),
        Paragraph("<b>Medicine Details</b>", styles["table_header"]),
        Paragraph("<b>Next Trigger Time</b>", styles["table_header"]),
    ]

    table_data = [headers]

    for r in reminders_list:
        title = r.get("title") or "Reminder"
        r_type = r.get("type", "medication").capitalize()
        status = r.get("status", "active").upper()
        sched = r.get("schedule") or {}
        recurrence = sched.get("recurrence") or r.get("recurrence") or "Daily"
        time_of_day = sched.get("time_of_day") or r.get("schedule") or "-"
        meal_timing = r.get("mealRelativeTiming") or r.get("meal_timing") or "-"
        med = r.get("medicineDetails") or r.get("medicine_details") or {}
        med_name = med.get("medicine_name") or r.get("medicineName") or "-"
        dosage = med.get("dosage") or "-"
        med_str = f"<b>{med_name}</b> ({dosage})" if med_name != "-" else "-"
        next_fire = r.get("next_trigger_at") or r.get("nextTriggerTime") or "-"
        if isinstance(next_fire, datetime.datetime):
            next_fire = next_fire.strftime("%d %b %Y, %H:%M")

        row = [
            Paragraph(f"<b>{title}</b>", styles["table_cell"]),
            Paragraph(f"{r_type}<br/><font color='#0D9488'><b>{status}</b></font>", styles["table_cell"]),
            Paragraph(f"{recurrence}<br/>At {time_of_day}", styles["table_cell"]),
            Paragraph(str(meal_timing), styles["table_cell"]),
            Paragraph(med_str, styles["table_cell"]),
            Paragraph(str(next_fire), styles["table_cell"]),
        ]
        table_data.append(row)

    col_widths = [1.5 * inch, 1.0 * inch, 1.3 * inch, 1.1 * inch, 1.3 * inch, 1.2 * inch]
    t = Table(table_data, colWidths=col_widths, repeatRows=1)

    t_style = [
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#F1F5F9")),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
        ("TOPPADDING", (0, 0), (-1, -1), 4),
        ("GRID", (0, 0), (-1, -1), 0.5, colors.HexColor("#E2E8F0")),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
    ]
    for i in range(1, len(table_data)):
        if i % 2 == 0:
            t_style.append(("BACKGROUND", (0, i), (-1, i), colors.HexColor("#F8FAFC")))

    t.setStyle(TableStyle(t_style))
    story.append(t)

    doc.build(story, canvasmaker=NumberedCanvas)
    return buffer.getvalue()


def generate_documents_index_pdf(documents_list: List[dict], patient_name: str, patient_id: str) -> bytes:
    """Generates the Uploaded Medical Documents & Lab Reports Index PDF."""
    buffer = io.BytesIO()
    doc = SimpleDocTemplate(
        buffer,
        pagesize=letter,
        leftMargin=36,
        rightMargin=36,
        topMargin=36,
        bottomMargin=46,
    )
    styles = get_custom_styles()
    story = []

    story.extend(_create_header_banner("Medical Documents & Lab Reports Index", f"Total Archived Records: {len(documents_list)}", patient_name, patient_id, styles))

    if not documents_list:
        story.append(Paragraph("<i>No uploaded medical documents or lab reports archived for this account.</i>", styles["body"]))
        doc.build(story, canvasmaker=NumberedCanvas)
        return buffer.getvalue()

    story.append(Paragraph("<i>Note: All original user-uploaded PDF files, prescriptions, and lab test image scans are preserved in original format inside the <b>Documents/</b> folder of this archive.</i>", styles["body"]))
    story.append(Spacer(1, 8))

    headers = [
        Paragraph("<b>Document Title</b>", styles["table_header"]),
        Paragraph("<b>Category / Type</b>", styles["table_header"]),
        Paragraph("<b>Upload Date</b>", styles["table_header"]),
        Paragraph("<b>AI OCR Summary / Key Findings</b>", styles["table_header"]),
        Paragraph("<b>Archived File Name</b>", styles["table_header"]),
    ]

    table_data = [headers]

    for d in documents_list:
        title = d.get("title") or d.get("name") or "Medical Document"
        cat = d.get("category") or d.get("type") or "Lab Report"
        d_at = d.get("createdAt") or d.get("created_at")
        date_str = ""
        if isinstance(d_at, datetime.datetime):
            date_str = d_at.strftime("%d %b %Y")
        elif isinstance(d_at, str):
            date_str = d_at[:10]

        summary = d.get("summary") or d.get("ocr_summary") or d.get("notes") or "Document verified"
        file_ref = d.get("fileRef") or d.get("file_path") or ""
        fname = file_ref.split("/")[-1] if "/" in file_ref else file_ref
        doc_id = d.get("id", "doc")
        archived_fname = f"{doc_id}_{fname}" if fname else "Attached in Documents/"

        row = [
            Paragraph(f"<b>{title}</b>", styles["table_cell"]),
            Paragraph(str(cat).replace("_", " ").title(), styles["table_cell"]),
            Paragraph(date_str, styles["table_cell"]),
            Paragraph(str(summary)[:100], styles["table_cell"]),
            Paragraph(f"<font color='#0D9488'>{archived_fname}</font>", styles["table_cell"]),
        ]
        table_data.append(row)

    col_widths = [1.5 * inch, 1.1 * inch, 0.9 * inch, 2.3 * inch, 1.6 * inch]
    t = Table(table_data, colWidths=col_widths, repeatRows=1)

    t_style = [
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#F1F5F9")),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
        ("TOPPADDING", (0, 0), (-1, -1), 4),
        ("GRID", (0, 0), (-1, -1), 0.5, colors.HexColor("#E2E8F0")),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
    ]
    for i in range(1, len(table_data)):
        if i % 2 == 0:
            t_style.append(("BACKGROUND", (0, i), (-1, i), colors.HexColor("#F8FAFC")))

    t.setStyle(TableStyle(t_style))
    story.append(t)

    doc.build(story, canvasmaker=NumberedCanvas)
    return buffer.getvalue()


def generate_comprehensive_medical_dossier_pdf(
    user_data: dict,
    patient_data: dict,
    vitals_list: List[dict],
    consultations_list: List[dict],
    reminders_list: List[dict],
    documents_list: List[dict],
) -> bytes:
    """
    Generates the Master All-in-One Comprehensive Medical Dossier PDF.
    Contains Executive Summary, Patient Profile, Vitals Trends, Consultations,
    Active Prescriptions & Reminders, and Document Inventories in one seamless document.
    """
    buffer = io.BytesIO()
    doc = SimpleDocTemplate(
        buffer,
        pagesize=letter,
        leftMargin=36,
        rightMargin=36,
        topMargin=36,
        bottomMargin=46,
    )
    styles = get_custom_styles()
    story = []

    patient_name = user_data.get("name") or patient_data.get("name") or "Patient"
    patient_id = user_data.get("uid") or patient_data.get("id") or "N/A"

    story.extend(_create_header_banner(
        "Comprehensive Medical Summary & Health Dossier",
        f"Complete Health Record • Vitals ({len(vitals_list)}) • Consultations ({len(consultations_list)}) • Documents ({len(documents_list)})",
        patient_name,
        patient_id,
        styles,
    ))

    # SECTION 1: Patient Profile & Emergency Card
    story.append(Paragraph("1. Patient Profile & Emergency Health Passport", styles["h1"]))
    blood_group = patient_data.get("blood_group") or "Not recorded"
    age = patient_data.get("age") or "N/A"
    dob = patient_data.get("date_of_birth") or user_data.get("date_of_birth") or "N/A"
    allergies = ", ".join(patient_data.get("allergies", [])) or "No known allergies"
    conditions = ", ".join(patient_data.get("chronic_conditions", [])) or "None documented"
    ec = patient_data.get("emergency_contact") or {}
    ec_name = ec.get("name") if isinstance(ec, dict) else "None"
    ec_phone = ec.get("phone") if isinstance(ec, dict) else "N/A"

    passport_data = [
        [Paragraph("<b>Patient Name:</b>", styles["body_bold"]), Paragraph(str(patient_name), styles["body"]),
         Paragraph("<b>Blood Group:</b>", styles["body_bold"]), Paragraph(f"<font color='#BE123C'><b>{blood_group}</b></font>", styles["body"])],
        [Paragraph("<b>DOB / Age:</b>", styles["body_bold"]), Paragraph(f"{dob} ({age} yrs)", styles["body"]),
         Paragraph("<b>Emergency Contact:</b>", styles["body_bold"]), Paragraph(f"{ec_name} ({ec_phone})", styles["body"])],
        [Paragraph("<b>Known Allergies:</b>", styles["body_bold"]), Paragraph(f"<font color='#BE123C'><b>{allergies}</b></font>", styles["body"]),
         Paragraph("<b>Chronic Conditions:</b>", styles["body_bold"]), Paragraph(str(conditions), styles["body"])],
    ]
    t_pass = Table(passport_data, colWidths=[1.5 * inch, 2.2 * inch, 1.5 * inch, 2.2 * inch])
    t_pass.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, -1), colors.HexColor("#F8FAFC")),
        ("BOX", (0, 0), (-1, -1), 0.5, colors.HexColor("#E2E8F0")),
        ("INNERGRID", (0, 0), (-1, -1), 0.5, colors.HexColor("#E2E8F0")),
        ("TOPPADDING", (0, 0), (-1, -1), 4),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
    ]))
    story.append(t_pass)
    story.append(Spacer(1, 10))

    # SECTION 2: Recent Vitals
    story.append(Paragraph("2. Recent Vitals & Biometric Logs", styles["h1"]))
    if vitals_list:
        v_headers = [
            Paragraph("<b>Date</b>", styles["table_header"]),
            Paragraph("<b>Blood Pressure</b>", styles["table_header"]),
            Paragraph("<b>Heart Rate</b>", styles["table_header"]),
            Paragraph("<b>Blood Glucose</b>", styles["table_header"]),
            Paragraph("<b>SpO2 / Temp</b>", styles["table_header"]),
            Paragraph("<b>BMI / Category</b>", styles["table_header"]),
        ]
        v_rows = [v_headers]
        for v in vitals_list[:8]: # Display most recent 8 in master dossier
            m_at = v.get("measured_at") or v.get("recordedAt")
            d_str = m_at.strftime("%d %b %Y") if isinstance(m_at, datetime.datetime) else str(m_at)[:10]
            sys, dia = v.get("systolic"), v.get("diastolic")
            bp = f"{sys}/{dia} mmHg" if (sys and dia) else "-"
            hr = f"{v.get('heart_rate')} bpm" if v.get("heart_rate") else "-"
            glu = f"{v.get('glucose_fasting') or v.get('glucose_random') or '-'} mg/dL"
            sp = f"{v.get('spo2')}%" if v.get("spo2") else "-"
            bmi = f"{v.get('bmi')} ({v.get('bmi_category') or '-'})" if v.get("bmi") else "-"
            v_rows.append([
                Paragraph(d_str, styles["table_cell"]),
                Paragraph(bp, styles["table_cell"]),
                Paragraph(hr, styles["table_cell"]),
                Paragraph(glu, styles["table_cell"]),
                Paragraph(sp, styles["table_cell"]),
                Paragraph(bmi, styles["table_cell"]),
            ])
        t_v = Table(v_rows, colWidths=[1.1 * inch, 1.3 * inch, 1.1 * inch, 1.3 * inch, 1.1 * inch, 1.5 * inch])
        t_v.setStyle(TableStyle([
            ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#F1F5F9")),
            ("GRID", (0, 0), (-1, -1), 0.5, colors.HexColor("#E2E8F0")),
            ("TOPPADDING", (0, 0), (-1, -1), 3),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
        ]))
        story.append(t_v)
    else:
        story.append(Paragraph("<i>No vital logs recorded.</i>", styles["body"]))
    story.append(Spacer(1, 10))

    # SECTION 3: Clinical Consultations & Diagnoses
    story.append(Paragraph("3. Clinical Consultations & Doctor Diagnoses", styles["h1"]))
    if consultations_list:
        for c in consultations_list[:5]: # Most recent 5
            c_title = c.get("title") or "Consultation"
            c_doc = c.get("doctor_name") or c.get("doctorId") or "Doctor"
            c_at = c.get("created_at") or c.get("createdAt")
            d_str = c_at.strftime("%d %b %Y") if isinstance(c_at, datetime.datetime) else str(c_at)[:10]
            summary = c.get("summary") or c.get("summary_en") or "Clinical encounter documented."
            diag = ", ".join(c.get("key_diagnoses", [])) or "None"
            
            c_box = [
                Paragraph(f"<b>{c_title}</b> ({d_str}) — <i>{c_doc}</i> | <font color='#BE123C'><b>Diagnoses:</b> {diag}</font>", styles["body_bold"]),
                Paragraph(f"{summary}", styles["body"]),
                Spacer(1, 4),
            ]
            story.append(KeepTogether(c_box))
    else:
        story.append(Paragraph("<i>No consultation records found.</i>", styles["body"]))
    story.append(Spacer(1, 10))

    # SECTION 4: Active Medication Schedule
    story.append(Paragraph("4. Prescriptions & Medication Schedule", styles["h1"]))
    if reminders_list:
        r_headers = [
            Paragraph("<b>Medication</b>", styles["table_header"]),
            Paragraph("<b>Dosage</b>", styles["table_header"]),
            Paragraph("<b>Timing / Schedule</b>", styles["table_header"]),
            Paragraph("<b>Meal Relation</b>", styles["table_header"]),
            Paragraph("<b>Status</b>", styles["table_header"]),
        ]
        r_rows = [r_headers]
        for r in reminders_list:
            med = r.get("medicineDetails") or r.get("medicine_details") or {}
            m_name = med.get("medicine_name") or r.get("medicineName") or r.get("title") or "Medicine"
            dosage = med.get("dosage") or "-"
            sched = r.get("schedule") or {}
            timing = sched.get("time_of_day") or r.get("schedule") or "-"
            meal = r.get("mealRelativeTiming") or "-"
            stat = r.get("status", "active").upper()
            r_rows.append([
                Paragraph(f"<b>{m_name}</b>", styles["table_cell"]),
                Paragraph(dosage, styles["table_cell"]),
                Paragraph(str(timing), styles["table_cell"]),
                Paragraph(str(meal), styles["table_cell"]),
                Paragraph(f"<font color='#0D9488'>{stat}</font>", styles["table_cell"]),
            ])
        t_r = Table(r_rows, colWidths=[2.2 * inch, 1.2 * inch, 1.5 * inch, 1.4 * inch, 1.1 * inch])
        t_r.setStyle(TableStyle([
            ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#F1F5F9")),
            ("GRID", (0, 0), (-1, -1), 0.5, colors.HexColor("#E2E8F0")),
            ("TOPPADDING", (0, 0), (-1, -1), 3),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
        ]))
        story.append(t_r)
    else:
        story.append(Paragraph("<i>No active medication schedules found.</i>", styles["body"]))
    story.append(Spacer(1, 10))

    # SECTION 5: Document Archives
    story.append(Paragraph("5. Uploaded Lab Reports & Records Inventory", styles["h1"]))
    if documents_list:
        d_headers = [
            Paragraph("<b>Document Title</b>", styles["table_header"]),
            Paragraph("<b>Type</b>", styles["table_header"]),
            Paragraph("<b>Date</b>", styles["table_header"]),
            Paragraph("<b>Archived File in Documents/</b>", styles["table_header"]),
        ]
        d_rows = [d_headers]
        for d in documents_list:
            title = d.get("title") or d.get("name") or "Medical Document"
            cat = d.get("category") or d.get("type") or "Report"
            d_at = d.get("createdAt") or d.get("created_at")
            d_str = d_at.strftime("%d %b %Y") if isinstance(d_at, datetime.datetime) else str(d_at)[:10]
            file_ref = d.get("fileRef") or d.get("file_path") or ""
            fname = file_ref.split("/")[-1] if "/" in file_ref else file_ref
            doc_id = d.get("id", "doc")
            archived_fname = f"{doc_id}_{fname}" if fname else "Attached in Documents/"
            d_rows.append([
                Paragraph(f"<b>{title}</b>", styles["table_cell"]),
                Paragraph(str(cat).replace("_", " ").title(), styles["table_cell"]),
                Paragraph(d_str, styles["table_cell"]),
                Paragraph(f"<font color='#0D9488'>{archived_fname}</font>", styles["table_cell"]),
            ])
        t_d = Table(d_rows, colWidths=[2.5 * inch, 1.4 * inch, 1.1 * inch, 2.4 * inch])
        t_d.setStyle(TableStyle([
            ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#F1F5F9")),
            ("GRID", (0, 0), (-1, -1), 0.5, colors.HexColor("#E2E8F0")),
            ("TOPPADDING", (0, 0), (-1, -1), 3),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
        ]))
        story.append(t_d)
    else:
        story.append(Paragraph("<i>No uploaded documents attached.</i>", styles["body"]))

    doc.build(story, canvasmaker=NumberedCanvas)
    return buffer.getvalue()
