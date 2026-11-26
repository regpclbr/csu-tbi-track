"""Display helpers for the Reports page (phase F5): the "New report" form and the history table. Display only: what a
report contains, who may request one and how its state is decided are in reports.services (request_report,
report_state); this module turns their inputs and results into what the templates show.
"""

from dataclasses import dataclass, field
from datetime import date

from django.conf import settings
from django.http import QueryDict
from django.urls import reverse

from activities.models import ActivityType
from assistant import gemini
from documents.services import today_in_manila

from . import services
from .content import AI_SUMMARY, REPORT_TYPES, SECTIONS, period_name, range_in_words
from .dashboard_page import quick_range
from .models import GeneratedReport

# The form's period choices (ASSUMPTIONS 399): "This month", the presets of reports.services (quarters, halves, the
# whole year; each with the Year choice) and Custom dates.
THIS_MONTH, CUSTOM = "this_month", "custom"
REPORT_RANGES = [(THIS_MONTH, "This month"), *services.PERIOD_PRESETS.items(), (CUSTOM, "Custom dates")]
# The Year choice offers this year - YEARS_BACK ... this year + YEARS_AHEAD (plus the form's own year if outside).
YEARS_BACK, YEARS_AHEAD = 5, 1

# A pending row asks for itself again every POLL_SECONDS, at most MAX_POLLS times (15 minutes), so a forgotten tab
# does not poll forever (then: "refresh to check again").
POLL_SECONDS = 4
MAX_POLLS = 15 * 60 // POLL_SECONDS

STATE_BADGES = {
    services.PENDING: ("Pending", "info"),
    services.READY: ("Ready", "success"),
    services.FAILED: ("Failed", "danger"),
}


def report_range(key: str, today: date, year: int | None = None) -> tuple[date, date] | None:
    """Start and end of a period choice (Manila calendar): "this_month", or a preset of `year` (default: this year);
    None for "custom" / unknown keys. The preset dates come from reports.services.preset_range."""
    if key == THIS_MONTH:
        return quick_range(key, today)
    return services.preset_range(key, year if year is not None else today.year)


def year_choices(today: date, also: int | None = None) -> list[int]:
    """The Year choice: newest first; `also` (a filled or posted year outside the usual list) is added."""
    years = set(range(today.year - YEARS_BACK, today.year + YEARS_AHEAD + 1))
    if also is not None and services.PRESET_MIN_YEAR <= also <= services.PRESET_MAX_YEAR:
        years.add(also)
    return sorted(years, reverse=True)


def date_presets(today: date, years: list[int]) -> dict:
    """The period choices and, for the page's JavaScript, every choice's dates per year (computed here, so Alpine only
    looks them up): {"choices": [{key, label, yearly}], "dates": {"2026": {"q3": {start, end, text}}, ...}}."""
    choices = [{"key": key, "label": label, "yearly": key in services.PERIOD_PRESETS} for key, label in REPORT_RANGES]
    dates = {}
    for year in years:
        per_key = {}
        for key, _label in REPORT_RANGES:
            chosen = report_range(key, today, year)
            if chosen:
                per_key[key] = {"start": chosen[0].isoformat(), "end": chosen[1].isoformat(), "text": range_in_words(*chosen)}
        dates[str(year)] = per_key
    return {"choices": choices, "dates": dates}


def current_quarter(today: date) -> str:
    return f"q{(today.month - 1) // 3 + 1}"


# --- The form -----------------------------------------------------------------------------------------------


@dataclass
class ReportForm:
    """The form's values and the service's errors (one message per field). Sections never include the AI summary:
    the "Include AI summary" switch adds it."""

    report_type: str = "accomplishment"
    sections: list[str] = field(default_factory=list)
    ai_summary: bool = False
    range_key: str = CUSTOM
    period_year: int | None = None  # the Year choice of a preset (ASSUMPTIONS 399)
    start: str = ""
    end: str = ""
    activity_type: str = ""
    format: str = GeneratedReport.Format.PDF
    errors: dict[str, str] = field(default_factory=dict)
    notes: str = ""  # ASSUMPTIONS 461: notes to guide the AI summary (files come with the request)

    @property
    def range_text(self) -> str:
        """The dates in words ("October 1 – December 31, 2026"), "" while they are not valid."""
        try:
            return range_in_words(date.fromisoformat(self.start), date.fromisoformat(self.end))
        except ValueError:
            return ""

    def service_data(self) -> dict:
        """What reports.services.request_report() reads."""
        sections = self.sections + ([AI_SUMMARY] if self.ai_summary else [])
        return {
            "report_type": self.report_type,
            "included_sections": sections,
            "date_range_start": self.start,
            "date_range_end": self.end,
            "activity_type": self.activity_type,
            "format": self.format,
            "notes": self.notes,
        }


def empty_form(today: date, report_type: str = "accomplishment") -> ReportForm:
    """The defaults (ASSUMPTIONS 467): this quarter of this year, the type's sections, PDF, and the AI summary when the
    AI is available (so the notes and files of step 3 are used; a failed summary never stops the report)."""
    report_type = report_type if report_type in REPORT_TYPES else "accomplishment"
    key = current_quarter(today)
    start, end = report_range(key, today, today.year)
    return ReportForm(report_type=report_type, sections=list(REPORT_TYPES[report_type].default_sections), range_key=key,
                      period_year=today.year, start=start.isoformat(), end=end.isoformat(),
                      ai_summary=gemini.is_configured())


def _posted_year(post: QueryDict, today: date) -> int:
    raw = post.get("period_year", "").strip()
    return int(raw) if raw.isdigit() and services.PRESET_MIN_YEAR <= int(raw) <= services.PRESET_MAX_YEAR else today.year


def form_from_post(post: QueryDict, today: date) -> ReportForm:
    """The posted form. A period choice (with its Year) wins over typed dates (so the form works without JavaScript);
    "custom" keeps the typed dates. Every value is checked by the service, not here."""
    range_key = post.get("range", CUSTOM)
    year = _posted_year(post, today)
    chosen = report_range(range_key, today, year)
    start, end = (chosen[0].isoformat(), chosen[1].isoformat()) if chosen else (post.get("date_range_start", "").strip(),
                                                                               post.get("date_range_end", "").strip())
    return ReportForm(
        report_type=post.get("report_type", ""),
        sections=[key for key in post.getlist("included_sections") if key != AI_SUMMARY],
        ai_summary=post.get("ai_summary") == "on",
        range_key=range_key if chosen else CUSTOM,
        period_year=year,
        start=start,
        end=end,
        activity_type=post.get("activity_type", "").strip(),
        format=post.get("format", ""),
        notes=post.get("notes", "")[:4000],
    )


def matching_range_key(start: date, end: date, today: date) -> tuple[str, int]:
    """(choice, year) that gives exactly these dates: a preset first (it names the period in the title), then
    "this_month", else ("custom", this year)."""
    if preset := services.matching_preset(start, end):
        return preset
    if report_range(THIS_MONTH, today) == (start, end):
        return THIS_MONTH, today.year
    return CUSTOM, today.year


def form_options(today: date, form: ReportForm | None = None) -> dict:
    """Choices for the form: the five types with their default sections, the sections without the AI summary, the formats, the period choices with their years and dates, and
    the activity types."""
    years = year_choices(today, form.period_year if form else None)
    presets = date_presets(today, years)
    return {
        "report_types": [{"key": key, "label": t.label, "main": t.main} for key, t in REPORT_TYPES.items()],
        "type_defaults": {key: [s for s in t.default_sections if s != AI_SUMMARY] for key, t in REPORT_TYPES.items()},
        "sections": [(key, label) for key, label in SECTIONS.items() if key != AI_SUMMARY],
        "formats": GeneratedReport.Format.choices,
        "presets": presets["choices"],
        "preset_dates": presets["dates"],
        "years": years,
        "activity_types": ActivityType.objects.order_by("name"),
        "ai_available": gemini.is_configured(),
        "notes_max": services.REPORT_NOTES_MAX, "max_files": settings.REPORT_GENERATOR_MAX_ATTACHMENTS,
    }


def flat_errors(message_dict: dict[str, list[str]]) -> dict[str, str]:
    """One message per field; errors not tied to a field are under "general" (templates cannot read "__all__")."""
    return {("general" if key == "__all__" else key): " ".join(messages) for key, messages in message_dict.items()}


# --- History ------------------------------------------------------------------------------------------------


def history_row(report: GeneratedReport, state: str, polls: int = 0) -> dict:
    """One report as the history table shows it. `polls`: how often a pending row has asked for itself already."""
    report_type = REPORT_TYPES.get(report.report_type)
    label, tone = STATE_BADGES[state]
    sections = services.sections_of(report)
    return {
        "report": report,
        "type_label": report_type.label if report_type else report.report_type,
        "sections": [SECTIONS[key] for key in sections],
        "has_ai_summary": AI_SUMMARY in sections,
        "range_text": range_in_words(report.date_range_start, report.date_range_end),
        "period_name": name.label if (name := period_name(report.date_range_start, report.date_range_end)) else "",
        "format_label": report.get_format_display(),
        "state": state,
        "badge": {"label": label, "tone": tone},
        "download_url": reverse("reports:download", args=[report.pk]) if state == services.READY else "",
        "poll": state == services.PENDING and polls < MAX_POLLS,
        "next_poll": polls + 1,
        "gave_up": state == services.PENDING and polls >= MAX_POLLS,
    }


def history(page: str | int) -> dict:
    """services.list_reports (newest first, 20 a page) with each report's state (one query for the failed tasks)."""
    result = services.list_reports({}, page=page)
    failed = services.failed_report_ids([report.pk for report in result.items])
    return {"result": result, "rows": [history_row(r, services.report_state(r, failed)) for r in result.items]}


def state_announcement(row: dict) -> str:
    """What the live region says when a polled report is no longer pending."""
    report = row["report"]
    if row["state"] == services.READY:
        return f"Report {report.pk} ({row['type_label']}, {row['range_text']}) is ready to download."
    if row["state"] == services.FAILED:
        return f"Report {report.pk} ({row['type_label']}, {row['range_text']}) failed."
    return ""


def today() -> date:
    return today_in_manila()
