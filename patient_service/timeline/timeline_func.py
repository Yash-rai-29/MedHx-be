import asyncio
import calendar
import logging
from datetime import datetime, date, time as dt_time, timezone, timedelta
from typing import List, Optional, Tuple, Union
from zoneinfo import ZoneInfo
from google.cloud import firestore

from common_code.config import settings
from patient_service.timeline.timeline_model import (
    TimelineFilterMetadata,
    TimelineFilterType,
    TimelineItem,
    TimelineItemType,
    TimelineMonthGroup,
    TimelineResponse,
)

logger = logging.getLogger(__name__)

IST = ZoneInfo("Asia/Kolkata")
_PAGE_SIZE_MAX = 100
_PAGE_SIZE_DEFAULT = 20


def _format_datetime_fields(dt: datetime) -> Tuple[str, str, str, str]:
    """
    Returns (date_formatted, time_formatted, month_group, month_key) in IST.
    Example: ('15 Aug 2026', '09:30 AM IST', 'August 2026', '2026-08')
    """
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    ist_dt = dt.astimezone(IST)
    date_formatted = ist_dt.strftime("%d %b %Y")
    time_formatted = ist_dt.strftime("%I:%M %p IST")
    month_group = ist_dt.strftime("%B %Y")
    month_key = ist_dt.strftime("%Y-%m")
    return date_formatted, time_formatted, month_group, month_key


def _truncate_summary(summary: Optional[str], max_len: int = 160) -> Optional[str]:
    if not summary:
        return None
    cleaned = " ".join(summary.split()).strip()
    if len(cleaned) <= max_len:
        return cleaned
    return cleaned[:max_len].rstrip() + "..."


def _convert_document_to_timeline(doc_id: str, d: dict) -> TimelineItem:
    created_at = d.get("createdAt") or d.get("created_at")
    if not isinstance(created_at, datetime):
        created_at = datetime.now(timezone.utc)
    elif created_at.tzinfo is None:
        created_at = created_at.replace(tzinfo=timezone.utc)

    date_formatted, time_formatted, month_group, month_key = _format_datetime_fields(created_at)

    category = d.get("type", "other")
    meds = d.get("medications") or []
    abnormal_labs = d.get("abnormal_labs") or []
    red_flags = d.get("red_flags") or []

    highlights = []
    if len(meds) > 0:
        highlights.append(f"{len(meds)} Medicine{'s' if len(meds) > 1 else ''}")
    if len(abnormal_labs) > 0:
        highlights.append(f"{len(abnormal_labs)} Abnormal Lab{'s' if len(abnormal_labs) > 1 else ''}")
    if len(red_flags) > 0:
        highlights.append("⚠️ Red Flag")
    if category and category != "other":
        highlights.append(category.replace("_", " ").title())

    title = d.get("title") or (d.get("fileRef", "").split("/")[-1] if d.get("fileRef") else "Medical Document")
    file_ref = d.get("fileRef") or ""

    return TimelineItem(
        id=doc_id,
        type=TimelineItemType.document,
        category=category,
        title=title,
        timestamp=created_at,
        date_formatted=date_formatted,
        time_formatted=time_formatted,
        month_group=month_group,
        month_key=month_key,
        doctor_name=d.get("doctor_name"),
        summary_preview=_truncate_summary(d.get("summary")),
        status=d.get("status", "completed"),
        language=d.get("language", "en"),
        highlights=highlights,
        has_audio=False,
        has_file=bool(file_ref),
        file_path=file_ref or None,
        attached_documents_count=0,
        consultation_id=d.get("consultation_id"),
        metadata={
            "document_date": d.get("document_date"),
            "medications_count": len(meds),
            "abnormal_labs_count": len(abnormal_labs),
            "red_flags_count": len(red_flags),
        }
    )


def _convert_audio_consultation_to_timeline(doc_id: str, d: dict) -> TimelineItem:
    created_at = d.get("created_at") or d.get("createdAt")
    if not isinstance(created_at, datetime):
        created_at = datetime.now(timezone.utc)
    elif created_at.tzinfo is None:
        created_at = created_at.replace(tzinfo=timezone.utc)

    date_formatted, time_formatted, month_group, month_key = _format_datetime_fields(created_at)

    meds = d.get("medicines") or []
    diagnoses = d.get("key_diagnoses") or []
    attached_ids = d.get("attached_document_ids") or []
    suggestions = d.get("reminder_suggestions") or []

    highlights = ["Audio Consultation"]
    if len(diagnoses) > 0:
        highlights.append(diagnoses[0])
    if len(meds) > 0:
        highlights.append(f"{len(meds)} Rx Medicine{'s' if len(meds) > 1 else ''}")
    if len(suggestions) > 0:
        highlights.append(f"{len(suggestions)} Reminder{'s' if len(suggestions) > 1 else ''}")
    if len(attached_ids) > 0:
        highlights.append(f"{len(attached_ids)} Linked Report{'s' if len(attached_ids) > 1 else ''}")

    title = d.get("title") or (f"Consultation with Dr. {d['doctor_name']}" if d.get("doctor_name") else "Audio Consultation")
    file_path = d.get("file_path") or ""

    return TimelineItem(
        id=doc_id,
        type=TimelineItemType.consult_record,
        category="audio_consultation",
        title=title,
        timestamp=created_at,
        date_formatted=date_formatted,
        time_formatted=time_formatted,
        month_group=month_group,
        month_key=month_key,
        doctor_name=d.get("doctor_name"),
        summary_preview=_truncate_summary(d.get("summary")),
        status=d.get("status", "completed"),
        language=d.get("language", "en"),
        highlights=highlights,
        has_audio=True,
        has_file=bool(file_path),
        file_path=file_path or None,
        attached_documents_count=len(attached_ids),
        consultation_id=None,
        metadata={
            "key_diagnoses": diagnoses,
            "medicines_count": len(meds),
            "reminders_count": len(suggestions),
        }
    )


def _convert_published_consultation_to_timeline(doc_id: str, d: dict) -> TimelineItem:
    created_at = d.get("createdAt") or d.get("created_at")
    if not isinstance(created_at, datetime):
        created_at = datetime.now(timezone.utc)
    elif created_at.tzinfo is None:
        created_at = created_at.replace(tzinfo=timezone.utc)

    date_formatted, time_formatted, month_group, month_key = _format_datetime_fields(created_at)

    meds = d.get("medicines") or []
    diagnoses = d.get("diagnoses") or []
    highlights = ["Doctor Consultation"]
    if len(diagnoses) > 0:
        diag_item = diagnoses[0]
        diag_name = diag_item.get("condition") if isinstance(diag_item, dict) else str(diag_item)
        highlights.append(diag_name)
    if len(meds) > 0:
        highlights.append(f"{len(meds)} Prescribed Medicine{'s' if len(meds) > 1 else ''}")

    pdf_ref = d.get("pdfRef") or ""
    title = f"Doctor Consultation Summary"

    return TimelineItem(
        id=doc_id,
        type=TimelineItemType.consult_record,
        category="doctor_consultation",
        title=title,
        timestamp=created_at,
        date_formatted=date_formatted,
        time_formatted=time_formatted,
        month_group=month_group,
        month_key=month_key,
        doctor_name=d.get("doctorId"),
        summary_preview=_truncate_summary(d.get("summary_en")),
        status=d.get("status", "published"),
        language="en",
        highlights=highlights,
        has_audio=False,
        has_file=bool(pdf_ref),
        file_path=pdf_ref or None,
        attached_documents_count=0,
        consultation_id=None,
        metadata={
            "follow_up_days": d.get("follow_up_days", 0),
            "medicines_count": len(meds),
        }
    )


def _group_by_month(items: List[TimelineItem]) -> List[TimelineMonthGroup]:
    """
    Groups ordered timeline items into monthly buckets (newest month first).
    """
    groups_dict: dict[str, list[TimelineItem]] = {}
    for item in items:
        key = item.month_key
        if key not in groups_dict:
            groups_dict[key] = []
        groups_dict[key].append(item)

    month_groups: List[TimelineMonthGroup] = []
    for month_key, group_items in groups_dict.items():
        first_item = group_items[0]
        y, m = map(int, month_key.split("-")[:2])
        month_groups.append(
            TimelineMonthGroup(
                month_group=first_item.month_group,
                month_key=month_key,
                year=y,
                month=m,
                count=len(group_items),
                items=group_items,
            )
        )
    return month_groups


def _resolve_query_bounds(
    year: Optional[int] = None,
    month: Optional[Union[int, str]] = None,
    from_date: Optional[date] = None,
    to_date: Optional[date] = None,
    cursor: Optional[str] = None,
) -> Tuple[Optional[datetime], Optional[datetime], Optional[int], Optional[int]]:
    """
    Computes (lower_bound_utc, upper_bound_utc, resolved_year, resolved_month).
    """
    lower_bound: Optional[datetime] = None
    upper_bound: Optional[datetime] = None

    resolved_year = year
    resolved_month: Optional[int] = None

    # Parse month parameter (could be int e.g. 8 or string e.g. "8", "08", "2026-08")
    if month is not None:
        month_str = str(month).strip()
        if "-" in month_str:
            try:
                p_y, p_m = map(int, month_str.split("-")[:2])
                resolved_year = p_y
                resolved_month = p_m
            except Exception:
                pass
        else:
            try:
                resolved_month = int(month_str)
            except ValueError:
                pass

    if resolved_month is not None and not (1 <= resolved_month <= 12):
        resolved_month = None

    # 1. Year and Month specified -> full month range
    if resolved_year is not None and resolved_month is not None:
        _, last_day = calendar.monthrange(resolved_year, resolved_month)
        lower_bound = datetime(resolved_year, resolved_month, 1, 0, 0, 0, tzinfo=timezone.utc)
        upper_bound = datetime(resolved_year, resolved_month, last_day, 23, 59, 59, 999999, tzinfo=timezone.utc)

    # 2. Only Year specified -> full year range
    elif resolved_year is not None and resolved_month is None:
        lower_bound = datetime(resolved_year, 1, 1, 0, 0, 0, tzinfo=timezone.utc)
        upper_bound = datetime(resolved_year, 12, 31, 23, 59, 59, 999999, tzinfo=timezone.utc)

    # 3. Only Month specified without Year -> use current year
    elif resolved_year is None and resolved_month is not None:
        current_year = datetime.now(timezone.utc).year
        resolved_year = current_year
        _, last_day = calendar.monthrange(current_year, resolved_month)
        lower_bound = datetime(current_year, resolved_month, 1, 0, 0, 0, tzinfo=timezone.utc)
        upper_bound = datetime(current_year, resolved_month, last_day, 23, 59, 59, 999999, tzinfo=timezone.utc)

    # 4. Custom from_date / to_date overrides/adjustments
    if from_date:
        from_dt = datetime.combine(from_date, dt_time.min, tzinfo=timezone.utc)
        if lower_bound is None or from_dt > lower_bound:
            lower_bound = from_dt

    if to_date:
        to_dt = datetime.combine(to_date, dt_time.max, tzinfo=timezone.utc)
        if upper_bound is None or to_dt < upper_bound:
            upper_bound = to_dt

    # 5. Cursor bound
    if cursor:
        try:
            cursor_ts = float(cursor)
            cursor_dt = datetime.fromtimestamp(cursor_ts, tz=timezone.utc) - timedelta(microseconds=1)
            if upper_bound is None or cursor_dt < upper_bound:
                upper_bound = cursor_dt
        except (ValueError, TypeError) as e:
            logger.warning(f"Invalid cursor '{cursor}': {e}")

    return lower_bound, upper_bound, resolved_year, resolved_month


async def get_patient_timeline(
    patient_id: str,
    db: firestore.AsyncClient,
    type_filter: TimelineFilterType = TimelineFilterType.all,
    year: Optional[int] = None,
    month: Optional[Union[int, str]] = None,
    from_date: Optional[date] = None,
    to_date: Optional[date] = None,
    cursor: Optional[str] = None,
    limit: int = _PAGE_SIZE_DEFAULT,
) -> TimelineResponse:
    """
    Retrieves a unified medical history timeline combining both consultations and documents.
    Optimized with parallel Firestore queries and timestamp cursor-based pagination.
    Returns both month-grouped structures and flat items.
    """
    limit = min(max(1, limit), _PAGE_SIZE_MAX)
    lower_bound, upper_bound, resolved_year, resolved_month = _resolve_query_bounds(
        year=year, month=month, from_date=from_date, to_date=to_date, cursor=cursor
    )

    tasks = []

    # 1. Documents Query
    if type_filter in (TimelineFilterType.all, TimelineFilterType.document):
        doc_q = db.collection(settings.DOCUMENTS_COLLECTION).where("patientId", "==", patient_id)
        if lower_bound:
            doc_q = doc_q.where("createdAt", ">=", lower_bound)
        if upper_bound:
            doc_q = doc_q.where("createdAt", "<=", upper_bound)
        doc_q = doc_q.order_by("createdAt", direction=firestore.Query.DESCENDING).limit(limit + 1)
        tasks.append(doc_q.get())
    else:
        tasks.append(asyncio.sleep(0, result=[]))

    # 2. Audio Consultations Query
    if type_filter in (TimelineFilterType.all, TimelineFilterType.consult_record):
        audio_q = db.collection(settings.AUDIO_CONSULTATIONS_COLLECTION).where("patientId", "==", patient_id)
        if lower_bound:
            audio_q = audio_q.where("created_at", ">=", lower_bound)
        if upper_bound:
            audio_q = audio_q.where("created_at", "<=", upper_bound)
        audio_q = audio_q.order_by("created_at", direction=firestore.Query.DESCENDING).limit(limit + 1)
        tasks.append(audio_q.get())

        # 3. Doctor Published Consultations Query (Optional fallback)
        pub_q = db.collection(settings.CONSULTATIONS_COLLECTION).where("patientId", "==", patient_id).where("status", "==", "published")
        if lower_bound:
            pub_q = pub_q.where("createdAt", ">=", lower_bound)
        if upper_bound:
            pub_q = pub_q.where("createdAt", "<=", upper_bound)
        pub_q = pub_q.order_by("createdAt", direction=firestore.Query.DESCENDING).limit(limit + 1)
        tasks.append(pub_q.get())
    else:
        tasks.append(asyncio.sleep(0, result=[]))
        tasks.append(asyncio.sleep(0, result=[]))

    results = await asyncio.gather(*tasks, return_exceptions=True)

    doc_snaps = results[0] if isinstance(results[0], list) else []
    audio_snaps = results[1] if isinstance(results[1], list) else []
    pub_snaps = results[2] if isinstance(results[2], list) else []

    merged_items: List[TimelineItem] = []

    for s in doc_snaps:
        try:
            merged_items.append(_convert_document_to_timeline(s.id, s.to_dict()))
        except Exception as e:
            logger.warning(f"Error parsing document {s.id} for timeline: {e}")

    for s in audio_snaps:
        try:
            merged_items.append(_convert_audio_consultation_to_timeline(s.id, s.to_dict()))
        except Exception as e:
            logger.warning(f"Error parsing audio consultation {s.id} for timeline: {e}")

    for s in pub_snaps:
        try:
            merged_items.append(_convert_published_consultation_to_timeline(s.id, s.to_dict()))
        except Exception as e:
            logger.warning(f"Error parsing consultation {s.id} for timeline: {e}")

    # In-memory boundary filtering (guarantees strict boundaries across multi-collection merges)
    if lower_bound:
        merged_items = [i for i in merged_items if i.timestamp >= lower_bound]
    if upper_bound:
        merged_items = [i for i in merged_items if i.timestamp <= upper_bound]

    # Sort descending by timestamp
    merged_items.sort(key=lambda x: x.timestamp, reverse=True)

    has_more = len(merged_items) > limit
    page_items = merged_items[:limit]

    next_cursor: Optional[str] = None
    if has_more and page_items:
        last_item = page_items[-1]
        next_cursor = str(last_item.timestamp.timestamp())

    # Build monthly grouped structure
    groups = _group_by_month(page_items)

    return TimelineResponse(
        groups=groups,
        # items=page_items,
        next_cursor=next_cursor,
        has_more=has_more,
        total_count=len(page_items),
        filters_applied=TimelineFilterMetadata(
            type=type_filter,
            year=resolved_year,
            month=resolved_month,
            from_date=from_date,
            to_date=to_date,
            limit=limit,
        )
    )
