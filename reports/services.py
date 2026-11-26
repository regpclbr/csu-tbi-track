"""Business logic for generated reports (GENERATED_REPORT) and the dashboard.

- Every number comes from reports.metrics (the dashboard, the PDF and the Excel file use the same functions).
- Requesting a report (can_create_activity, pending TBI) saves the row at once with an empty file_path
  and queues a Django-Q2 task named "report-<id>" after the commit.
- The task builds the content, adds the optional AI narrative, renders the PDF or Excel file and saves
  it under reports/<year>/. Reports are history: never deleted; generating again creates a new row.
- State is calculated, never stored: "ready" (file saved), "failed" (the task failed), else "pending".
- Files are downloaded only through reports.views.download_report (any active staff member, attachment).
"""

import json
import logging
from dataclasses import dataclass
from datetime import date, datetime

from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import QuerySet
from django.utils import timezone

from accounts.models import TBIMember
from accounts.permissions import Permission, require_permission_for, require_staff_member
from activities.models import Activity
from activities.services import PageResult, _paginate, happening_now
from assistant.periods import half_period, quarter_period, year_period
from config.files import write_file_atomically

from . import content as content_module
from . import excel, metrics, narrative, pdf
from .content import (
    AI_SUMMARY, FREQUENCY_WORDS, REPORT_TYPES, SECTIONS, ReportContent, build_content, ordered_sections, period_name,
    range_in_words,
)
from .models import GeneratedReport

logger = logging.getLogger("reports")

DEFAULT_PAGE_SIZE = 20
# Who may generate a report (and use "Fill in the form", ASSUMPTIONS 384): one constant, so both always agree.
GENERATE_REPORT_PERMISSION = Permission.CREATE_ACTIVITY
READY, PENDING, FAILED = "ready", "pending", "failed"
EXTENSIONS = {GeneratedReport.Format.PDF: "pdf", GeneratedReport.Format.EXCEL: "xlsx"}
CONTENT_TYPES = {
    "pdf": "application/pdf",
    "xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
}


# --- Dashboard -----------------------------------------------------------------------------------


def dashboard(member: TBIMember, params: dict, now: datetime | None = None) -> dict:
    """Dashboard numbers for any active staff member. params: start, end (YYYY-MM-DD), activity_type."""
    require_staff_member(member)
    filters = metrics.parse_filters(params, now)
    return {"filters": filters_json(filters), **metrics.dashboard(filters, now)}


@dataclass
class Spotlight:
    """The dashboard's spotlight card: an activity happening now ("ongoing"), else the next one ("upcoming")."""

    kind: str  # ONGOING_NOW or NEXT_UP
    activity: Activity
    more_ongoing: int = 0  # other activities happening now (the "+N more ongoing" link)


ONGOING_NOW, NEXT_UP = "ongoing", "upcoming"


def dashboard_spotlight(filters: metrics.ReportFilters, now: datetime | None = None,
                        upcoming_next: list[dict] | None = None) -> Spotlight | None:
    """The one choice of the spotlight (ASSUMPTIONS 351). An Ongoing activity running today
    (activities.services.happening_now: the one that started first, the others counted) wins over the next Scheduled
    one (S2, metrics.upcoming_activities; pass its "next" rows when already loaded). Both respect the activity type
    filter; Draft never appears. Callers run activities.services.apply_automatic_statuses() first."""
    ongoing = list(happening_now(filters.activity_type_id, now).select_related("status", "activity_type")[:1])
    if ongoing:
        more = happening_now(filters.activity_type_id, now).count() - 1
        return Spotlight(ONGOING_NOW, ongoing[0], more)
    rows = metrics.upcoming_activities(filters, now)["next"] if upcoming_next is None else upcoming_next
    if not rows:
        return None
    activity = Activity.objects.select_related("status", "activity_type").get(pk=rows[0]["activity_id"])
    return Spotlight(NEXT_UP, activity)


def activity_evaluation(member: TBIMember, activity) -> dict:
    """One activity's evaluation results (counts and rating averages, never text answers), for any
    active staff member, like the dashboard."""
    require_staff_member(member)
    return metrics.activity_evaluation_results(activity)


def activity_participants_by_category(member: TBIMember, activity) -> dict:
    """One activity's registered / attended per participant category with percentages (ASSUMPTIONS 400), for any
    active staff member (counts only, no names), like the activity page itself."""
    require_staff_member(member)
    return metrics.participants_by_category([activity.pk])[activity.pk]


def activity_participant_mix(member: TBIMember, activity) -> dict:
    """One activity's participant mix with NAVIGATU's guideline check (ASSUMPTIONS 424), for any active staff member
    (counts only), like activity_participants_by_category."""
    require_staff_member(member)
    return metrics.participant_mix([activity.pk])[activity.pk]


@dataclass
class ActivityFile:
    """A PDF or Excel file made on request for one activity (never stored, never a GENERATED_REPORT row)."""

    data: bytes
    name: str
    content_type: str


def evaluation_summary_file(member: TBIMember, activity, file_format: str, now: datetime | None = None) -> ActivityFile:
    """One activity's evaluation summary as PDF or Excel (ASSUMPTIONS 401), for any active staff member like the
    Evaluation tab: counts, rating frequencies and means, and text answers WITHOUT names. Made on request (small),
    through the report renderers (the PDF via certificates.pdf.get_renderer)."""
    require_staff_member(member)
    if file_format not in GeneratedReport.Format.values:
        raise ValidationError({"format": ["Choose pdf or excel."]})
    content = content_module.build_evaluation_summary(
        activity, metrics.activity_evaluation_summary(activity), generated_by=member.name,
        generated_at=now or timezone.now())
    extension = EXTENSIONS[file_format]
    data = pdf.render_report_pdf(content) if file_format == GeneratedReport.Format.PDF else excel.build_workbook(content)
    return ActivityFile(data, f"evaluation-summary-activity-{activity.pk}.{extension}", CONTENT_TYPES[extension])


def individual_evaluation_file(member: TBIMember, registration, now: datetime | None = None) -> ActivityFile:
    """One participant's submitted evaluation as a portrait PDF (ASSUMPTIONS 402, 404). The permission (can_create_activity) and the
    answers come from registrations.services.evaluation_answers (PermissionDenied / EvaluationSubmission.DoesNotExist).
    The file name holds IDs only, never the participant's name."""
    from registrations.services import evaluation_answers

    answers = evaluation_answers(member, registration)
    activity = registration.activity
    data = pdf.render_individual_evaluation_pdf(activity, answers, generated_by=member.name,
                                                generated_at=now or timezone.now())
    return ActivityFile(data,
                        f"evaluation-activity-{activity.pk}-registration-{registration.pk}.pdf", CONTENT_TYPES["pdf"])


def filters_json(filters: metrics.ReportFilters) -> dict:
    return {
        "start": filters.start,
        "end": filters.end,
        "range_text": range_in_words(filters.start, filters.end),
        "activity_type": filters.activity_type_id,
    }


# --- Catalogue -------------------------------------------------------------------------------------


def report_options() -> dict:
    """Report types (with their default sections), sections and formats, for the frontend."""
    return {
        "report_types": [
            {"key": key, "label": t.label, "default_sections": list(t.default_sections), "main": t.main}
            for key, t in REPORT_TYPES.items()
        ],
        "sections": [{"key": key, "label": label} for key, label in SECTIONS.items()],
        "formats": [{"key": value, "label": label} for value, label in GeneratedReport.Format.choices],
    }


def sections_of(report: GeneratedReport) -> list[str]:
    """included_sections is stored as a JSON list in the ERD's string column."""
    try:
        sections = json.loads(report.included_sections or "[]")
    except json.JSONDecodeError:
        return []
    return ordered_sections(sections) if isinstance(sections, list) else []


# --- Period presets (ASSUMPTIONS 399) ------------------------------------------------------------------------------
# Quarters, halves and the whole year of a chosen year. The dates come only from here (assistant.periods' calendar
# quarters and halves), never from Gemini or JavaScript: the form gets them as a table made by reports.pages.

PERIOD_PRESETS: dict[str, str] = {
    "q1": "Q1 (Jan–Mar)",
    "q2": "Q2 (Apr–Jun)",
    "q3": "Q3 (Jul–Sep)",
    "q4": "Q4 (Oct–Dec)",
    "h1": "First half (Jan–Jun)",
    "h2": "Second half (Jul–Dec)",
    "year": "Whole year (Jan–Dec)",
}
PRESET_MIN_YEAR, PRESET_MAX_YEAR = 2000, 2100


def preset_range(key: str, year: int) -> tuple[date, date] | None:
    """The dates of a preset in `year` (Q3 2026 = Jul 1 – Sep 30, 2026), or None for an unknown key or year."""
    if key not in PERIOD_PRESETS or not PRESET_MIN_YEAR <= year <= PRESET_MAX_YEAR:
        return None
    if key == "year":
        period = year_period(year)
    elif key.startswith("h"):
        period = half_period(year, int(key[1]))
    else:
        period = quarter_period(year, int(key[1]))
    return period.start, period.end


def matching_preset(start: date, end: date) -> tuple[str, int] | None:
    """(key, year) of the preset that gives exactly these dates, or None."""
    for key in PERIOD_PRESETS:
        if preset_range(key, start.year) == (start, end):
            return key, start.year
    return None


# --- Requesting a report ----------------------------------------------------------------------------


def _clean_request(data: dict, now: datetime | None) -> tuple[str, list[str], metrics.ReportFilters, str]:
    errors: dict[str, list[str]] = {}
    report_type = data.get("report_type")
    if report_type not in REPORT_TYPES:
        errors["report_type"] = [f"Choose one of: {', '.join(REPORT_TYPES)}."]

    raw_sections = data.get("included_sections")
    if raw_sections in (None, "", []):
        sections = list(REPORT_TYPES[report_type].default_sections) if report_type in REPORT_TYPES else []
    elif not isinstance(raw_sections, list) or not all(isinstance(key, str) for key in raw_sections):
        errors["included_sections"] = ["Send a list of section keys."]
        sections = []
    else:
        unknown = sorted({key for key in raw_sections if key not in SECTIONS})
        if unknown:
            errors["included_sections"] = [f"Unknown sections: {', '.join(unknown)}. Choose from: {', '.join(SECTIONS)}."]
        sections = ordered_sections(raw_sections)
    if not errors.get("included_sections") and report_type in REPORT_TYPES and not sections:
        errors["included_sections"] = ["Choose at least one section."]

    report_format = data.get("format")
    if report_format not in GeneratedReport.Format.values:
        errors["format"] = ["Choose pdf or excel."]

    try:
        filters = metrics.parse_filters(
            {"start": data.get("date_range_start"), "end": data.get("date_range_end"), "activity_type": data.get("activity_type")},
            now,
            dates_required=True,
        )
    except ValidationError as error:
        names = {"start": "date_range_start", "end": "date_range_end"}
        for field, messages in error.message_dict.items():
            errors[names.get(field, field)] = messages
        filters = None
    if errors:
        raise ValidationError(errors)
    return report_type, sections, filters, report_format


def task_name(report_id: int) -> str:
    return f"report-{report_id}"


def queue_build(report_id: int) -> None:
    from django_q.tasks import async_task

    async_task("reports.tasks.run_build_report", report_id, task_name=task_name(report_id))


def can_generate(member) -> bool:
    """GENERATE_REPORT_PERMISSION, or an open "Prepare a report" assignment (ASSUMPTIONS 437)."""
    from accounts.permissions import has_permission_for

    return has_permission_for(member, GENERATE_REPORT_PERMISSION)


REPORT_NOTES_MAX = 2000  # ASSUMPTIONS 461: the member's notes for the AI summary


def request_report(member: TBIMember, data: dict, now: datetime | None = None, queue: bool = True,
                   files=None) -> GeneratedReport:
    """Save a report request and queue its generation (after the commit). `queue=False` is only for the
    `generate_report` command, which builds the file itself right away.

    data: report_type, included_sections (list; default: the type's sections), date_range_start and
    date_range_end (both required, YYYY-MM-DD, end on or after start; a future range is allowed),
    activity_type (ID, optional), format, notes (optional, ASSUMPTIONS 461: guide the AI summary; never numbers).
    files: optional files "for the AI to read" (reports.generator.NewFile list), checked like a draft's.
    """
    require_permission_for(member, GENERATE_REPORT_PERMISSION)  # or a report assignment (ASSUMPTIONS 437)
    report_type, sections, filters, report_format = _clean_request(data, now)
    notes = str(data.get("notes") or "").replace("\r\n", "\n").strip()
    if len(notes) > REPORT_NOTES_MAX:
        raise ValidationError({"notes": [f"Keep the notes under {REPORT_NOTES_MAX} characters."]})
    from . import generator

    stored: list[str] = []
    if files:
        generator.check_files(None, [generator.NewFile(f.file, "document") for f in files])
    try:
        return _save_request(member, report_type, sections, filters, report_format, notes, files or [], queue, now,
                             stored)
    except Exception:
        generator._delete_files(stored)  # nothing was saved: never leave files behind
        raise


def _save_request(member, report_type, sections, filters, report_format, notes, files, queue, now, stored) -> GeneratedReport:
    from . import generator

    with transaction.atomic():
        report = GeneratedReport.objects.create(
            generated_by=member,
            report_type=report_type,
            included_sections=json.dumps(sections),
            date_range_start=filters.start,
            date_range_end=filters.end,
            activity_type_id=filters.activity_type_id,
            format=report_format,
            file_path="",
            notes=notes,
        )
        if files:
            stored.extend(generator.save_report_attachments(report, member, files, now or timezone.now()))
        # robust=True: a problem queueing never undoes the request (`generate_report --report` builds it by hand).
        if queue:
            transaction.on_commit(lambda: queue_build(report.pk), robust=True)
    return report


def report_content(report: GeneratedReport, now: datetime | None = None) -> ReportContent:
    filters = metrics.ReportFilters(report.date_range_start, report.date_range_end, report.activity_type_id)
    return build_content(
        report.report_type, sections_of(report), filters,
        generated_by=report.generated_by.name, generated_at=report.generated_at, now=now,
    )


def file_name_for(report: GeneratedReport) -> str:
    year = timezone.localtime(report.generated_at).year
    return f"reports/{year}/report-{report.pk}-{report.report_type}.{EXTENSIONS[report.format]}"


def _queue_indexing(report_id: int) -> None:
    from assistant.indexing import REPORT, queue_source_indexing

    queue_source_indexing(REPORT, report_id)


def build_report(report_id: int, now: datetime | None = None) -> str:
    """Compute, render and save the report file. Returns "generated" or "already generated".

    The AI narrative never stops the report: if Gemini fails, the section says why.
    Raises if computing, rendering or saving fails (the task records it as failed; no file is left).
    """
    report = GeneratedReport.objects.select_related("generated_by").get(pk=report_id)
    if report.file_path:
        return "already generated"
    content = report_content(report, now)
    from .generator import read_report_attachments

    guidance = {"notes": report.notes, "files": read_report_attachments(report)} if report.notes or \
        report.attachments.exists() else None
    written = narrative.add_narrative(content, report.pk, guidance=guidance)
    data = pdf.render_report_pdf(content) if report.format == GeneratedReport.Format.PDF else excel.build_workbook(content)

    name = file_name_for(report)
    storage = GeneratedReport._meta.get_field("file_path").storage
    saved = False
    try:
        with transaction.atomic():
            locked = GeneratedReport.objects.select_for_update().get(pk=report_id)
            if locked.file_path:  # another task finished first
                return "already generated"
            write_file_atomically(storage, name, data)
            saved = True
            locked.file_path.name = name
            locked.ai_prompt = written.prompt_record if written else None
            locked.save(update_fields=["file_path", "ai_prompt"])
            transaction.on_commit(lambda: _queue_indexing(report_id), robust=True)  # the assistant reads it (438)
    except BaseException:
        if saved:
            storage.delete(name)
        raise
    logger.info("Report %s generated (%s, %d bytes).", report_id, report.format, len(data))
    return "generated"


# --- The one Reports page: past reports (ASSUMPTIONS 461-462) ------------------------------------------------------

ACTIVITY_REPORT_DOCUMENT_TYPES = ("Activity Report", "Attendance Sheet", "Confirmation of Attendance")


def past_reports(member: TBIMember, activity=None) -> list[dict]:
    """Every output of the one Reports page, newest first: [{"type": "figures" | "draft" | "document", "object", "when"}].
    - Without an activity: every figures report (any active staff member may download them, item 301) and the member's
      OWN drafts (a draft is its maker's only, item 442).
    - For one activity (its Reports tab): the member's drafts about it and the report-like documents saved on it
      (Activity Report, Attendance Sheet, Confirmation of Attendance: generated forms and saved drafts are approved
      copies there). Figures reports are about periods, so they are not listed there."""
    from documents.models import Document

    from .models import ReportDraft

    require_staff_member(member)
    drafts = ReportDraft.objects.filter(created_by=member).select_related("activity")
    entries = []
    if activity is None:
        entries += [{"type": "figures", "object": report, "when": report.generated_at}
                    for report in GeneratedReport.objects.select_related("generated_by", "activity_type")]
    else:
        drafts = drafts.filter(activity=activity)
        entries += [{"type": "document", "object": document, "when": document.uploaded_at}
                    for document in Document.objects.activity_documents().filter(
                        activity=activity, document_type__name__in=ACTIVITY_REPORT_DOCUMENT_TYPES)
                    .select_related("document_type", "uploaded_by")]
    entries += [{"type": "draft", "object": draft, "when": draft.updated_at} for draft in drafts]
    return sorted(entries, key=lambda entry: entry["when"], reverse=True)


# --- State, listing and detail ---------------------------------------------------------------------


def failed_report_ids(report_ids: list[int]) -> set[int]:
    """Reports whose Django-Q2 task failed (one query for a whole page)."""
    from django_q.models import Task

    names = {task_name(report_id): report_id for report_id in report_ids}
    failed = Task.objects.filter(name__in=list(names), success=False).values_list("name", flat=True)
    return {names[name] for name in failed}


def report_state(report: GeneratedReport, failed_ids: set[int] | None = None) -> str:
    if report.file_path:
        return READY
    if failed_ids is None:
        failed_ids = failed_report_ids([report.pk])
    return FAILED if report.pk in failed_ids else PENDING


def list_reports(filters: dict | None = None, page: int | str | None = 1, per_page: int = DEFAULT_PAGE_SIZE) -> PageResult:
    """Any active staff member: every report, newest first; filters report_type and format."""
    filters = filters or {}
    queryset: QuerySet = GeneratedReport.objects.select_related("generated_by", "activity_type")
    if filters.get("report_type"):
        queryset = queryset.filter(report_type=filters["report_type"])
    if filters.get("format"):
        queryset = queryset.filter(format=filters["format"])
    return _paginate(queryset.order_by("-generated_at", "-report_id"), page, per_page)


def download_name(report: GeneratedReport) -> str:
    """"accomplishment-report-q3-2026-12.pdf", "semi-annual-participation-report-jan-jun-2026-12.pdf",
    "annual-accomplishment-report-2026-12.xlsx" for a named period (ASSUMPTIONS 399); any other range keeps
    "accomplishment-report-20260305-20260402-12.pdf"."""
    extension = EXTENSIONS[report.format]
    name = period_name(report.date_range_start, report.date_range_end)
    if name is None:
        return (f"{report.report_type}-report-{report.date_range_start:%Y%m%d}-{report.date_range_end:%Y%m%d}"
                f"-{report.pk}.{extension}")
    prefix = FREQUENCY_WORDS[name.kind].strip().lower()
    report_type = report.report_type.replace("_", "-")
    return f"{prefix + '-' if prefix else ''}{report_type}-report-{name.slug}-{report.pk}.{extension}"
