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
import re
from dataclasses import dataclass, field
from datetime import date, datetime

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import QuerySet
from django.utils import timezone

from accounts.models import TBIMember
from accounts.permissions import Permission, require_permission, require_staff_member
from activities.models import Activity, ActivityType
from activities.services import PageResult, _paginate, happening_now
from assistant import gemini
from assistant.periods import ASK, half_period, parse_range, quarter_period, year_period
from config import ratelimit
from config.files import write_file_atomically

from . import content as content_module
from . import excel, metrics, narrative, pdf, request_reader
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


def request_report(member: TBIMember, data: dict, now: datetime | None = None, queue: bool = True) -> GeneratedReport:
    """Save a report request and queue its generation (after the commit). `queue=False` is only for the
    `generate_report` command, which builds the file itself right away.

    data: report_type, included_sections (list; default: the type's sections), date_range_start and
    date_range_end (both required, YYYY-MM-DD, end on or after start; a future range is allowed),
    activity_type (ID, optional), format.
    """
    require_permission(member, GENERATE_REPORT_PERMISSION)
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


# --- "Describe the report you want" (ASSUMPTIONS 384) -------------------------------------------------------------
# Gemini only reads the request (reports.request_reader); every value it returns is checked here against the allowed
# choices, dates are worked out by assistant.periods.parse_range() in Manila time, and every message shown is our own
# sentence. Nothing is saved: the member reviews the filled form and clicks Generate (request_report, unchanged).

FILL_EMPTY = "Describe the report you want first."
FILL_TOO_LONG = "Keep the description under {limit} characters."
FILL_AI_OFF = "Describing the report is not available: the AI is turned off. Fill in the form by hand."
FILL_RATE_LIMITED = "You've used this {limit} times in the last hour. Try again later, or fill in the form by hand."
FILL_NOT_UNDERSTOOD = "The request could not be read. Nothing was changed; fill in the form by hand."
FILL_RATE_SCOPE = "report_form_fill"
MAX_UNSUPPORTED_SHOWN, MAX_QUOTE_CHARS = 3, 80
FILL_CONDUCTED_ONLY = "Reports include only Conducted activities. For upcoming activities, ask the AI Assistant."
# "quarterly" / "semi-annual" without saying which one (ASSUMPTIONS 399): the dates are never guessed.
FILL_WHICH = {
    "quarterly": "Which quarter? Choose the quarter and the year under Period; the dates were not changed.",
    "semi-annual": "Which half? Choose First half or Second half and the year under Period; the dates were not changed.",
}
# Our own topic words (never Gemini's): the activity list, certificates, and activities that have not happened yet.
ACTIVITY_WORDS = re.compile(r"\b(?:activit(?:y|ies)(?!\s+types?\b)|events?|programs?|programmes?|trainings?|seminars?|"
                            r"workshops?|sessions?)\b", re.IGNORECASE)
CERTIFICATE_WORDS = re.compile(r"\bcertificates?\b", re.IGNORECASE)
UPCOMING_WORDS = re.compile(r"\b(?:upcoming|planned|scheduled|future|forthcoming)\b", re.IGNORECASE)


@dataclass
class FormFill:
    """What "Fill in the form" changes. None = keep the form's current value. `filled` and `notes` are our sentences."""

    report_type: str | None = None
    sections: list[str] | None = None  # without ai_summary (the switch)
    ai_summary: bool | None = None
    start: date | None = None
    end: date | None = None
    activity_type: str | None = None  # an ActivityType ID as text, or "" = all activity types
    format: str | None = None
    filled: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)


def _squash(text: str) -> str:
    return " ".join(str(text).lower().replace("’", "'").split())


def _quoted_from_request(value, request: str) -> str | None:
    """`value` when it is a string that appears word for word in the request (so we only ever echo the user's own
    words), else None."""
    if not isinstance(value, str) or not value.strip():
        return None
    return value.strip() if _squash(value) in _squash(request) else None


def _short(text: str) -> str:
    text = " ".join(text.split())
    return text if len(text) <= MAX_QUOTE_CHARS else text[:MAX_QUOTE_CHARS - 1] + "…"


def _singular(name: str) -> str:
    name = _squash(name)
    return name[:-1] if name.endswith("s") and not name.endswith("ss") else name


def match_activity_type(name: str) -> ActivityType | None:
    """The ACTIVITY TYPE row with this name, ignoring case and a plural "s" ("trainings" = "Training"); active rows
    only (the table decides, never a fixed list)."""
    wanted = _singular(name)
    for activity_type in ActivityType.objects.filter(is_active=True).order_by("name"):
        if _singular(activity_type.name) == wanted:
            return activity_type
    return None


def mentions_activities(text: str) -> bool:
    """Activities, events, programs, trainings, seminars, workshops, sessions, unless the word is the name of an
    activity type in the table ("trainings only" = the Training type, not the activity list)."""
    return any(not match_activity_type(match.group(0)) for match in ACTIVITY_WORDS.finditer(text))


def _fill_period(fill: FormFill, args: dict, request: str, now: datetime, asks_upcoming: bool) -> bool:
    """Dates from OUR parser: Gemini's period words only when they are in the request, else the request itself.
    Returns False when the request says nothing about a period."""
    words = _quoted_from_request(args.get("period_words"), request)
    # Gemini's words with the whole request as context, so "Q3" still finds the "2025" said elsewhere in it.
    period = parse_range(words, now, context=request) if words else None
    if period is None or period.kind == ASK:
        period = parse_range(request, now) or period
    if period is None:
        if words:
            fill.notes.append(f"Couldn't work out the period “{_short(words)}”; the dates were not changed.")
        return bool(words)
    if period.kind == ASK:
        fill.notes.append(FILL_WHICH[period.label])
        return True
    if period.start is None or period.end is None:
        if not asks_upcoming:  # else FILL_CONDUCTED_ONLY explains it
            fill.notes.append(f"“{period.label}” is not a date range; the dates were not changed. Choose the dates below.")
        return True
    fill.start, fill.end = period.start, period.end
    fill.filled.append(f"Period: {range_in_words(period.start, period.end)}")
    return True


def _fill_sections(fill: FormFill, args: dict, request: str, current_sections: list[str] | None, asks_upcoming: bool) -> bool:
    """Gemini's sections (checked), else the report type's usual sections; then OUR rule: a request that mentions
    activities / certificates gets that section when it is missing ("Added: ..."). False = sections left as they were."""
    raw = args.get("sections")
    chosen = []
    if isinstance(raw, (list, tuple)):
        chosen = [key for key in ordered_sections([k for k in raw if isinstance(k, str)]) if key != AI_SUMMARY]
    from_type = not chosen and bool(fill.report_type)
    if from_type:
        chosen = [key for key in REPORT_TYPES[fill.report_type].default_sections if key != AI_SUMMARY]
    base = chosen or [key for key in (current_sections or []) if key in SECTIONS and key != AI_SUMMARY]
    added = []
    if mentions_activities(request) and not asks_upcoming and "activities" not in base:
        added.append("activities")
    if CERTIFICATE_WORDS.search(request) and "certificates" not in base:
        added.append("certificates")
    if not chosen and not added:
        return False
    fill.sections = ordered_sections(base + added)
    if from_type and not added:
        fill.filled.append("Sections: the report type's usual sections")
    else:
        fill.filled.append("Sections: " + ", ".join(SECTIONS[key] for key in fill.sections))
    if added:
        fill.notes.append("Added: " + ", ".join(SECTIONS[key] for key in added) + ".")
    return True


def _check_fill(args: dict, request: str, now: datetime, current_sections: list[str] | None = None) -> FormFill:
    fill = FormFill()
    kept: list[str] = []
    asks_upcoming = bool(UPCOMING_WORDS.search(request))

    report_type = args.get("report_type")
    if report_type in REPORT_TYPES:
        fill.report_type = report_type
        fill.filled.append(f"Report type: {REPORT_TYPES[report_type].label}")
    else:
        kept.append("report type")

    if not _fill_sections(fill, args, request, current_sections, asks_upcoming):
        kept.append("sections")

    if isinstance(args.get("ai_summary"), bool):
        fill.ai_summary = args["ai_summary"]
        fill.filled.append("AI summary: " + ("included" if fill.ai_summary else "not included"))

    if not _fill_period(fill, args, request, now, asks_upcoming):
        kept.append("period")

    name = args.get("activity_type")
    if isinstance(name, str) and name.strip():
        if _squash(name) in ("all", "any", "all activity types", "all types"):
            fill.activity_type = ""
            fill.filled.append("Activity type: All activity types")
        elif activity_type := match_activity_type(name):
            fill.activity_type = str(activity_type.pk)
            fill.filled.append(f"Activity type: {activity_type.name}")
        else:
            fill.notes.append(f"There is no activity type named “{_short(name.strip())}”; the activity type was not changed.")
    else:
        kept.append("activity type")

    report_format = args.get("format")
    if report_format in GeneratedReport.Format.values:
        fill.format = report_format
        fill.filled.append(f"Format: {GeneratedReport.Format(report_format).label}")
    else:
        kept.append("format")

    if asks_upcoming:
        fill.notes.append(FILL_CONDUCTED_ONLY)

    # Quotes the form CAN do are not "unsupported": activities / certificates once their section is in, upcoming
    # wording once FILL_CONDUCTED_ONLY explains it.
    sections = fill.sections or []
    raw_unsupported = args.get("unsupported")
    quotes = []
    for value in raw_unsupported if isinstance(raw_unsupported, (list, tuple)) else []:
        quote = _quoted_from_request(value, request)
        if not quote or (asks_upcoming and UPCOMING_WORDS.search(quote)):
            continue
        if ("activities" in sections and mentions_activities(quote)) or ("certificates" in sections and CERTIFICATE_WORDS.search(quote)):
            continue
        if _short(quote) not in quotes:
            quotes.append(_short(quote))
    if quotes:
        fill.notes.append("These can't be set in this form: " + "; ".join(f"“{q}”" for q in quotes[:MAX_UNSUPPORTED_SHOWN]) + ".")
    if kept:
        fill.notes.append("Not in your request, so kept as it was: " + ", ".join(kept) + ".")
    return fill


def fill_report_form(member: TBIMember, text: str, now: datetime | None = None,
                     current_sections: list[str] | None = None) -> FormFill:
    """Read a typed report request and return the checked form settings (nothing is saved). `current_sections`: the
    form's sections now, the base when the request names no type or sections but our rule adds one.

    Same permission as generating a report. Refusals (empty or too long text, the AI turned off, the hourly limit,
    Gemini failing) raise ValidationError({"request_text": [message]}) with a message safe to show. The request text
    is never logged or stored.
    """
    require_permission(member, GENERATE_REPORT_PERMISSION)
    text = (text or "").strip()
    if not text:
        raise ValidationError({"request_text": [FILL_EMPTY]})
    if len(text) > settings.REPORT_FORM_FILL_MAX_CHARS:
        raise ValidationError({"request_text": [FILL_TOO_LONG.format(limit=settings.REPORT_FORM_FILL_MAX_CHARS)]})
    if not gemini.is_configured():
        raise ValidationError({"request_text": [FILL_AI_OFF]})
    limit = settings.REPORT_FORM_FILL_MAX_PER_HOUR
    if not ratelimit.count_attempt(FILL_RATE_SCOPE, "member", str(member.pk), limit, 60):
        logger.info("Report form fill: member %s, refused (hourly limit).", member.pk)
        raise ValidationError({"request_text": [FILL_RATE_LIMITED.format(limit=limit)]})

    try:
        with gemini.time_limit(settings.REPORT_FORM_FILL_TIMEOUT_SECONDS):
            args = request_reader.read_request(text)
    except gemini.AssistantUnavailable as error:
        logger.info("Report form fill: member %s, Gemini unavailable (%s).", member.pk, error.kind)
        raise ValidationError({"request_text": [str(error)]}) from None
    if args is None:
        logger.info("Report form fill: member %s, no settings returned.", member.pk)
        raise ValidationError({"request_text": [FILL_NOT_UNDERSTOOD]})

    fill = _check_fill(args, text, now or timezone.now(), current_sections)
    logger.info("Report form fill: member %s, %d setting(s) filled, %d note(s).", member.pk, len(fill.filled), len(fill.notes))
    return fill


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
