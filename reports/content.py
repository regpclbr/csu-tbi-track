"""What a report contains: its sections as plain tables (and simple charts), built ONLY from
reports.metrics. The PDF (reports/pdf.py) and the Excel file (reports/excel.py) render the same
content, so both always show the same numbers as the dashboard.
"""

from dataclasses import dataclass, field
from datetime import date, datetime

from activities.models import ActivityType

from . import metrics
from .metrics import ReportFilters

# Column kinds: how a value is formatted (PDF) and which number format it gets (Excel).
TEXT, INT, PERCENT, DECIMAL, DATE = "text", "int", "percent", "decimal", "date"


@dataclass
class Column:
    label: str
    kind: str = TEXT
    for_ai: bool = True  # False: never sent to Gemini (e.g. startup names)


@dataclass
class Cell:
    """A value whose kind differs from its column (e.g. a percentage in a "Value" column)."""

    value: object
    kind: str


@dataclass
class Table:
    title: str
    columns: list[Column]
    rows: list[list]
    note: str = ""


@dataclass
class Chart:
    """A single-series horizontal bar chart (the values are also in a table next to it)."""

    title: str
    items: list[tuple[str, float]]
    kind: str = INT
    max_value: float | None = None  # e.g. 5 for ratings; default: the largest value


@dataclass
class Section:
    key: str
    label: str
    tables: list[Table] = field(default_factory=list)
    chart: Chart | None = None
    text: str = ""  # the AI narrative
    note: str = ""


@dataclass
class ReportContent:
    title: str
    report_type: str
    report_type_label: str
    filters: ReportFilters
    activity_type_name: str  # "All activity types" when not filtered
    generated_by: str
    generated_at: datetime
    today: date
    summary: dict  # metrics.period_summary(), for the dashboard comparison test
    period_in_words: str  # e.g. "January 1 – June 30, 2026"
    has_activities: bool  # False: the period sections say NO_ACTIVITIES instead of showing empty tables
    sections: list[Section]

    def section(self, key: str) -> Section | None:
        return next((s for s in self.sections if s.key == key), None)


# --- Catalogue: sections and report types ------------------------------------------------------

AI_SUMMARY = "ai_summary"
AI_LABEL = "AI-generated summary based on the figures in this report; please review."
NO_ACTIVITIES = "No conducted activities in this period."

# In the order they appear in a report.
SECTIONS: dict[str, str] = {
    "summary": "Summary",
    AI_SUMMARY: "AI-generated summary",
    "activities": "Activities conducted",
    "by_type": "By activity type",
    "by_category": "By participant category",
    "monthly": "Per month",
    "evaluation": "Evaluation results",
    "documents": "Document compliance",
    "certificates": "Certificates",
    "startups": "Startups engaged",
}


@dataclass(frozen=True)
class ReportType:
    label: str
    default_sections: tuple[str, ...]
    main: bool = False  # the reports the TBI most likely needs


REPORT_TYPES: dict[str, ReportType] = {
    "accomplishment": ReportType(
        "Accomplishment Report", ("summary", "activities", "by_type", "by_category", "monthly", "evaluation"), main=True
    ),
    "participation": ReportType("Participation Report", ("summary", "by_category", "monthly", "startups"), main=True),
    "evaluation": ReportType("Evaluation Report", ("summary", "evaluation", "activities")),
    "document_compliance": ReportType("Document Compliance Report", ("documents",)),
    "certificates": ReportType("Certificates Report", ("certificates",)),
}


# Sections built from the period's Conducted activities (all but the certificates and the AI summary).
PERIOD_SECTIONS = frozenset(SECTIONS) - {"certificates", AI_SUMMARY}


def ordered_sections(keys) -> list[str]:
    """Known section keys, without duplicates, in report order."""
    wanted = set(keys)
    return [key for key in SECTIONS if key in wanted]


def range_in_words(start: date, end: date) -> str:
    """The exact range, in words: "January 1 – June 30, 2026", "March 1 – 31, 2026",
    "December 1, 2025 – January 31, 2026", or "March 5, 2026" for one day."""
    if start == end:
        return f"{start:%B} {start.day}, {start.year}"
    if start.year != end.year:
        return f"{start:%B} {start.day}, {start.year} – {end:%B} {end.day}, {end.year}"
    if start.month != end.month:
        return f"{start:%B} {start.day} – {end:%B} {end.day}, {end.year}"
    return f"{start:%B} {start.day} – {end.day}, {end.year}"


def long_date(value: date) -> str:
    return f"{value:%b} {value.day}, {value.year}"


# --- Section builders ----------------------------------------------------------------------------


def _summary(filters: ReportFilters, summary: dict) -> Section:
    s = summary
    rows = [
        ["Activities conducted", s["activities_conducted"]],
        ["Registered participants", s["registered"]],
        ["Attended", s["attended"]],
        ["Attendance rate (attended / registered)", Cell(s["attendance_rate"], PERCENT)],
        ["Unique attendees (by email)", s["unique_attendees"]],
        ["Evaluations submitted", s["evaluations"]],
        ["Evaluation response rate (evaluations / attended)", Cell(s["evaluation_response_rate"], PERCENT)],
        ["Average rating (1-5, all rating questions)", Cell(s["average_rating"], DECIMAL)],
        ["Activities with all required documents approved", s["fully_compliant_activities"]],
        ["Certificates issued", s["certificates_issued"]],
        ["Certificates emailed", s["certificates_emailed"]],
        ["Startups engaged", s["startups_engaged"]],
    ]
    return Section(
        "summary", SECTIONS["summary"],
        tables=[Table("Key figures", [Column("Measure"), Column("Value", INT)], rows)],
        note=f"Conducted activities with an end date from {long_date(filters.start)} to {long_date(filters.end)}. "
             "Certificates count by the date they were issued or emailed.",
    )


def _activities(filters: ReportFilters) -> Section:
    rows = [
        [r["title"], r["activity_type__name"], r["categories"], r["start_date"], r["end_date"], r["venue"],
         r["registered"], r["attended"], r["attendance_rate"], r["evaluations"], r["average_rating"]]
        for r in metrics.activity_rows(filters)
    ]
    columns = [
        Column("Title"), Column("Type"), Column("Categories"), Column("Start", DATE), Column("End", DATE), Column("Venue"),
        Column("Registered", INT), Column("Attended", INT), Column("Attendance rate", PERCENT),
        Column("Evaluations", INT), Column("Average rating", DECIMAL),
    ]
    return Section("activities", SECTIONS["activities"], tables=[Table("Activities", columns, rows)])


def _by_type(filters: ReportFilters) -> Section:
    data = metrics.by_type(filters)
    rows = [[r["name"], r["activities"], r["registered"], r["attended"], r["attendance_rate"]] for r in data]
    columns = [Column("Activity type"), Column("Activities", INT), Column("Registered", INT), Column("Attended", INT),
               Column("Attendance rate", PERCENT)]
    return Section(
        "by_type", SECTIONS["by_type"], tables=[Table("By activity type", columns, rows)],
        chart=Chart("Activities conducted by type", [(r["name"], r["activities"]) for r in data]),
    )


def _by_category(filters: ReportFilters) -> Section:
    data = metrics.by_category(filters)
    rows = [[r["name"], r["activities"], r["registered"], r["attended"], r["attendance_rate"]] for r in data]
    columns = [Column("Participant category"), Column("Activities for this category", INT), Column("Registered", INT),
               Column("Attended", INT), Column("Attendance rate", PERCENT)]
    return Section(
        "by_category", SECTIONS["by_category"], tables=[Table("By participant category", columns, rows, note=metrics.CATEGORY_NOTE)],
        chart=Chart("Attendees by participant category", [(r["name"], r["attended"]) for r in data]),
    )


def _monthly(filters: ReportFilters) -> Section:
    data = metrics.per_month(filters)
    rows = [[r["label"], r["activities"], r["registered"], r["attended"]] for r in data]
    columns = [Column("Month"), Column("Activities", INT), Column("Registered", INT), Column("Attended", INT)]
    return Section(
        "monthly", SECTIONS["monthly"], tables=[Table("Per month (by end date)", columns, rows)],
        chart=Chart("Activities conducted per month", [(r["label"], r["activities"]) for r in data]),
    )


def _evaluation(filters: ReportFilters) -> Section:
    data = metrics.evaluation_results(filters)
    rows = [[q["question"], q["average"], q["answers"]] for q in data["questions"]]
    rows.append(["All rating questions", data["average_rating"], data["rating_answers"]])
    columns = [Column("Rating question"), Column("Average (1-5)", DECIMAL), Column("Answers", INT)]
    return Section(
        "evaluation", SECTIONS["evaluation"],
        tables=[Table("Average rating per question", columns, rows,
                      note="Questions with the same label (ignoring upper/lower case) are combined across activities.")],
        chart=Chart("Average rating per question", [(q["question"], q["average"] or 0) for q in data["questions"]],
                    kind=DECIMAL, max_value=5),
    )


def _documents(filters: ReportFilters, now: datetime | None) -> Section:
    data = metrics.document_compliance(metrics.period_activities(filters), now)
    columns = [Column("Activity"), Column("End", DATE)] + [Column(name) for name in data["document_types"]] + [Column("All approved")]
    rows, overdue = [], 0
    for activity in data["activities"]:
        states = []
        for item in activity["items"]:
            overdue += item["is_overdue"]
            states.append(f"{item['state']} (overdue)" if item["is_overdue"] else item["state"])
        rows.append([activity["title"], activity["end_date"], *states, "Yes" if activity["complete"] else "No"])
    complete = sum(activity["complete"] for activity in data["activities"])
    overview = Table("Overview", [Column("Measure"), Column("Value", INT)], [
        ["Conducted activities", len(rows)],
        ["With all required documents approved", complete],
        ["Required documents overdue", overdue],
    ])
    return Section(
        "documents", SECTIONS["documents"],
        tables=[overview, Table("Required documents per activity", columns, rows)],
        note=f"As of {long_date(data['today'])}. Same rules as the activity checklist: a type is complete when one of "
             "its documents is Approved; overdue means after the due date and not Approved.",
    )


def _certificates(filters: ReportFilters, summary: dict, today: date) -> Section:
    now_counts = metrics.certificates_now(filters)
    columns = [Column("Measure"), Column("Value", INT)]
    return Section(
        "certificates", SECTIONS["certificates"],
        tables=[
            Table("Issued and emailed in the period", columns, [
                ["Certificates of Participation issued", summary["certificates_issued_participation"]],
                ["Certificates of Appreciation issued (hard copy)", summary["certificates_issued_appreciation"]],
                ["Total issued", summary["certificates_issued"]],
                ["Certificates emailed", summary["certificates_emailed"]],
            ]),
            Table(f"Right now ({long_date(today)})", columns, [
                ["Generated (not reviewed yet)", now_counts["generated"]],
                ["Pending sign-off", now_counts["pending_signoff"]],
                ["Approved, not issued yet", now_counts["approved_not_issued"]],
            ]),
        ],
    )


def _startups(filters: ReportFilters) -> Section:
    rows = [
        [s.name, s.maturity_stage or "", s.activities_attended or 0]
        for s in metrics.engaged_startups(filters).order_by("name", "pk")
    ]
    columns = [Column("Startup", for_ai=False), Column("Maturity stage", for_ai=False), Column("Activities attended", INT)]
    return Section(
        "startups", SECTIONS["startups"], tables=[Table("Startups engaged", columns, rows)],
        note="Startups with at least one attended registration in a conducted activity of the period "
             "(registered while logged in, or with the startup's email).",
    )


def build_content(
    report_type: str, sections: list[str], filters: ReportFilters, *, generated_by: str,
    generated_at: datetime, now: datetime | None = None,
) -> ReportContent:
    """Compute every included section. The AI summary section is left empty here (reports.narrative fills it)."""
    report = REPORT_TYPES[report_type]
    today = metrics.today_in_manila(now)
    summary = metrics.period_summary(filters, now)
    period = range_in_words(filters.start, filters.end)
    # Nothing was conducted (for example a range in the future): the sections built from the period's
    # activities say so instead of showing tables of zeros. Certificates count by their own dates.
    has_activities = summary["activities_conducted"] > 0
    builders = {
        "summary": lambda: _summary(filters, summary),
        AI_SUMMARY: lambda: Section(AI_SUMMARY, SECTIONS[AI_SUMMARY]),
        "activities": lambda: _activities(filters),
        "by_type": lambda: _by_type(filters),
        "by_category": lambda: _by_category(filters),
        "monthly": lambda: _monthly(filters),
        "evaluation": lambda: _evaluation(filters),
        "documents": lambda: _documents(filters, now),
        "certificates": lambda: _certificates(filters, summary, today),
        "startups": lambda: _startups(filters),
    }
    activity_type = (
        ActivityType.objects.get(pk=filters.activity_type_id).name if filters.activity_type_id else "All activity types"
    )
    return ReportContent(
        title=f"{report.label}: {period}",
        report_type=report_type,
        report_type_label=report.label,
        filters=filters,
        activity_type_name=activity_type,
        generated_by=generated_by,
        generated_at=generated_at,
        today=today,
        summary=summary,
        period_in_words=period,
        has_activities=has_activities,
        sections=[
            builders[key]() if has_activities or key not in PERIOD_SECTIONS else Section(key, SECTIONS[key], note=NO_ACTIVITIES)
            for key in ordered_sections(sections)
        ],
    )
