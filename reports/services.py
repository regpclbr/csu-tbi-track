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
from datetime import datetime

from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import QuerySet
from django.utils import timezone

from accounts.models import TBIMember
from accounts.permissions import Permission, require_permission, require_staff_member
from activities.services import PageResult, _paginate
from config.files import write_file_atomically

from . import excel, metrics, narrative, pdf
from .content import REPORT_TYPES, SECTIONS, ReportContent, build_content, ordered_sections, range_in_words
from .models import GeneratedReport

logger = logging.getLogger("reports")

DEFAULT_PAGE_SIZE = 20
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


def activity_evaluation(member: TBIMember, activity) -> dict:
    """One activity's evaluation results (counts and rating averages, never text answers), for any
    active staff member, like the dashboard."""
    require_staff_member(member)
    return metrics.activity_evaluation_results(activity)


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


def request_report(member: TBIMember, data: dict, now: datetime | None = None, queue: bool = True) -> GeneratedReport:
    """Save a report request and queue its generation (after the commit). `queue=False` is only for the
    `generate_report` command, which builds the file itself right away.

    data: report_type, included_sections (list; default: the type's sections), date_range_start and
    date_range_end (both required, YYYY-MM-DD, end on or after start; a future range is allowed),
    activity_type (ID, optional), format.
    """
    require_permission(member, Permission.CREATE_ACTIVITY)
    report_type, sections, filters, report_format = _clean_request(data, now)
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
        )
        # robust=True: a problem queueing never undoes the request (`generate_report --report` builds it by hand).
        if queue:
            transaction.on_commit(lambda: queue_build(report.pk), robust=True)
    return report


# --- Building the file ------------------------------------------------------------------------------


def report_content(report: GeneratedReport, now: datetime | None = None) -> ReportContent:
    filters = metrics.ReportFilters(report.date_range_start, report.date_range_end, report.activity_type_id)
    return build_content(
        report.report_type, sections_of(report), filters,
        generated_by=report.generated_by.name, generated_at=report.generated_at, now=now,
    )


def file_name_for(report: GeneratedReport) -> str:
    year = timezone.localtime(report.generated_at).year
    return f"reports/{year}/report-{report.pk}-{report.report_type}.{EXTENSIONS[report.format]}"


def build_report(report_id: int, now: datetime | None = None) -> str:
    """Compute, render and save the report file. Returns "generated" or "already generated".

    The AI narrative never stops the report: if Gemini fails, the section says why.
    Raises if computing, rendering or saving fails (the task records it as failed; no file is left).
    """
    report = GeneratedReport.objects.select_related("generated_by").get(pk=report_id)
    if report.file_path:
        return "already generated"
    content = report_content(report, now)
    written = narrative.add_narrative(content, report.pk)
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
    except BaseException:
        if saved:
            storage.delete(name)
        raise
    logger.info("Report %s generated (%s, %d bytes).", report_id, report.format, len(data))
    return "generated"


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
    return (
        f"{report.report_type}-report-{report.date_range_start:%Y%m%d}-{report.date_range_end:%Y%m%d}"
        f"-{report.pk}.{EXTENSIONS[report.format]}"
    )
