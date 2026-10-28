"""Display helpers for the Reports page (phase F5): the "New report" form and the history table. Display only: what a
report contains, who may request one and how its state is decided are in reports.services (request_report,
report_state); this module turns their inputs and results into what the templates show.
"""

from dataclasses import dataclass, field
from datetime import date

from django.http import QueryDict
from django.urls import reverse

from activities.models import ActivityType
from documents.services import today_in_manila

from . import services
from .content import AI_SUMMARY, REPORT_TYPES, SECTIONS, range_in_words
from .dashboard_page import quick_range
from .models import GeneratedReport

# The form's quick date choices. "first_half" is the reports' own (January–June); the others are the dashboard's.
REPORT_RANGES = [
    ("this_month", "This month"),
    ("this_quarter", "This quarter"),
    ("first_half", "January – June"),
    ("this_year", "This year"),
    ("last_year", "Last year"),
    ("custom", "Custom"),
]
DEFAULT_RANGE = "this_quarter"

# A pending row asks for itself again every POLL_SECONDS, at most MAX_POLLS times (15 minutes), so a forgotten tab
# does not poll forever (then: "refresh to check again").
POLL_SECONDS = 4
MAX_POLLS = 15 * 60 // POLL_SECONDS

STATE_BADGES = {
    services.PENDING: ("Pending", "info"),
    services.READY: ("Ready", "success"),
    services.FAILED: ("Failed", "danger"),
}


def report_range(key: str, today: date) -> tuple[date, date] | None:
    """Start and end of a quick choice (Manila calendar), or None for "custom" / unknown keys."""
    if key == "first_half":
        return date(today.year, 1, 1), date(today.year, 6, 30)
    return quick_range(key, today)


def date_presets(today: date) -> list[dict]:
    """The quick choices with their dates (ISO, for the date inputs) and the range in words."""
    presets = []
    for key, label in REPORT_RANGES:
        chosen = report_range(key, today)
        presets.append({
            "key": key,
            "label": label,
            "start": chosen[0].isoformat() if chosen else "",
            "end": chosen[1].isoformat() if chosen else "",
            "text": range_in_words(*chosen) if chosen else "",
        })
    return presets


# --- The form -----------------------------------------------------------------------------------------------


@dataclass
class ReportForm:
    """The form's values and the service's errors (one message per field). Sections never include the AI summary:
    the "Include AI summary" switch adds it."""

    report_type: str = "accomplishment"
    sections: list[str] = field(default_factory=list)
    ai_summary: bool = False
    range_key: str = DEFAULT_RANGE
    start: str = ""
    end: str = ""
    activity_type: str = ""
    format: str = GeneratedReport.Format.PDF
    errors: dict[str, str] = field(default_factory=dict)

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
        }


def empty_form(today: date) -> ReportForm:
    start, end = report_range(DEFAULT_RANGE, today)
    return ReportForm(sections=list(REPORT_TYPES["accomplishment"].default_sections), start=start.isoformat(), end=end.isoformat())


def form_from_post(post: QueryDict, today: date) -> ReportForm:
    """The posted form. A quick choice wins over typed dates (so the form works without JavaScript); "custom" keeps
    the typed dates. Every value is checked by the service, not here."""
    range_key = post.get("range", "custom")
    chosen = report_range(range_key, today)
    start, end = (chosen[0].isoformat(), chosen[1].isoformat()) if chosen else (post.get("date_range_start", "").strip(),
                                                                               post.get("date_range_end", "").strip())
    return ReportForm(
        report_type=post.get("report_type", ""),
        sections=[key for key in post.getlist("included_sections") if key != AI_SUMMARY],
        ai_summary=post.get("ai_summary") == "on",
        range_key=range_key if chosen else "custom",
        start=start,
        end=end,
        activity_type=post.get("activity_type", "").strip(),
        format=post.get("format", ""),
    )


def form_options(today: date) -> dict:
    """Choices for the form: the five types (the two main ones first, as REPORT_TYPES lists them) with their default
    sections, the sections without the AI summary, the formats, the quick dates and the activity types."""
    return {
        "report_types": [{"key": key, "label": t.label, "main": t.main} for key, t in REPORT_TYPES.items()],
        "type_defaults": {key: [s for s in t.default_sections if s != AI_SUMMARY] for key, t in REPORT_TYPES.items()},
        "sections": [(key, label) for key, label in SECTIONS.items() if key != AI_SUMMARY],
        "formats": GeneratedReport.Format.choices,
        "presets": date_presets(today),
        "activity_types": ActivityType.objects.order_by("name"),
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
