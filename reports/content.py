"""What a report contains: its sections as plain tables (and simple charts), built ONLY from
reports.metrics. The PDF (reports/pdf.py) and the Excel file (reports/excel.py) render the same
content, so both always show the same numbers as the dashboard.
"""

from dataclasses import dataclass, field
from datetime import date, datetime

from activities.formatting import format_time_range
from activities.models import ActivityType
from assistant.periods import half_period, quarter_period, year_period

from . import metrics
from .metrics import ReportFilters

# Column kinds: how a value is formatted (PDF) and which number format it gets (Excel).
# MONEY: "₱"#,##0.00 in Excel, ₱1,234.50 in the PDF (the budget, ASSUMPTIONS 433).
TEXT, INT, PERCENT, DECIMAL, DATE, MONEY = "text", "int", "percent", "decimal", "date", "money"


@dataclass
class Column:
    label: str
    kind: str = TEXT
    for_ai: bool = True  # False: never sent to Gemini (e.g. names)
    # What the column holds, for the PDF layout only (reports/pdf.py): "start" + "end" are printed as one Date column,
    # "time" in its compact form; "title" / "venue" get the widest text columns. Excel keeps every column as it is.
    key: str = ""


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
    # The totals row (Excel only, below the table). Its values come from metrics.period_summary(), never from adding
    # up the rows: one activity can count under several participant categories (distinct activities).
    total: list | None = None


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
    period_name: str = ""  # "Q3 2026", "January to June 2026", "2026" for a quarter / half / whole year; "" otherwise
    # Header lines that replace the period lines (PDF header, Excel title block and Report info): documents about ONE
    # activity (evaluation summary, one evaluation; ASSUMPTIONS 401-402) use them. None = a normal period report.
    meta_lines: list[tuple[str, str]] | None = None

    def section(self, key: str) -> Section | None:
        return next((s for s in self.sections if s.key == key), None)

    @property
    def period_text(self) -> str:
        """"Q3 2026 (July 1 – September 30, 2026)" for a named period, else the range in words."""
        return f"{self.period_name} ({self.period_in_words})" if self.period_name else self.period_in_words


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
    "category_by_activity": "Participants by category per activity",
    "monthly": "Per month",
    "budget": "Budget by fund source",  # ASSUMPTIONS 433
    "evaluation": "Evaluation results",
    "documents": "Document compliance",
    "certificates": "Certificates",
    # "startups" (Startups engaged) was removed with the startup portal (ASSUMPTIONS 458); ordered_sections skips it in
    # older GENERATED_REPORT rows, whose files stay as they are.
}


@dataclass(frozen=True)
class ReportType:
    label: str
    default_sections: tuple[str, ...]
    main: bool = False  # the reports the TBI most likely needs


REPORT_TYPES: dict[str, ReportType] = {
    "accomplishment": ReportType(
        "Accomplishment Report",
        ("summary", "activities", "by_type", "by_category", "category_by_activity", "monthly", "budget", "evaluation"),
        main=True,
    ),
    "participation": ReportType(
        "Participation Report", ("summary", "by_category", "category_by_activity", "monthly"), main=True
    ),
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


# --- Period names (ASSUMPTIONS 399) -----------------------------------------------------------------------------
# A report whose dates are exactly a calendar quarter, half or year is named after it, in the title and the file name.
# The name is worked out from the stored dates (no column), so it is the same for old reports.
QUARTER, HALF, YEAR = "quarter", "half", "year"


@dataclass(frozen=True)
class PeriodName:
    kind: str  # QUARTER, HALF or YEAR
    label: str  # "Q3 2026", "January to June 2026", "2026"
    slug: str  # for the file name: "q3-2026", "jan-jun-2026", "2026"


def period_name(start: date, end: date) -> PeriodName | None:
    """The quarter, half or whole year these dates are exactly, or None (any other range)."""
    year = start.year
    if (start, end) == (year_period(year).start, year_period(year).end):
        return PeriodName(YEAR, str(year), str(year))
    for half in (1, 2):
        period = half_period(year, half)
        if (start, end) == (period.start, period.end):
            first, last = (("January", "June"), ("July", "December"))[half - 1]
            return PeriodName(HALF, f"{first} to {last} {year}", f"{first[:3].lower()}-{last[:3].lower()}-{year}")
    for quarter in (1, 2, 3, 4):
        period = quarter_period(year, quarter)
        if (start, end) == (period.start, period.end):
            return PeriodName(QUARTER, f"Q{quarter} {year}", f"q{quarter}-{year}")
    return None


# The word before the report type's label: "Semi-annual Accomplishment Report", "Annual Participation Report".
FREQUENCY_WORDS = {QUARTER: "", HALF: "Semi-annual ", YEAR: "Annual "}


def report_title(label: str, start: date, end: date) -> str:
    """"Accomplishment Report - Q3 2026", "Semi-annual Accomplishment Report - January to June 2026",
    "Annual Accomplishment Report - 2026", else "Accomplishment Report - March 5 – April 2, 2026"."""
    name = period_name(start, end)
    if name is None:
        return f"{label} - {range_in_words(start, end)}"
    return f"{FREQUENCY_WORDS[name.kind]}{label} - {name.label}"


def long_date(value: date) -> str:
    return f"{value:%b} {value.day}, {value.year}"


# --- Section builders ----------------------------------------------------------------------------


def _summary(filters: ReportFilters, summary: dict) -> Section:
    s = summary
    rows = [
        ["Activities conducted", s["activities_conducted"]],
        ["Registered participants", s["registered"]],
        ["Cancelled (\u201cWill not attend\u201d, not counted as registered)", s["cancelled"]],
        ["Attended", s["attended"]],
        ["Attendance rate (attended / registered)", Cell(s["attendance_rate"], PERCENT)],
        ["Unique attendees (by email)", s["unique_attendees"]],
        ["Evaluations submitted", s["evaluations"]],
        ["Evaluation response rate (evaluations / attended)", Cell(s["evaluation_response_rate"], PERCENT)],
        ["Average rating (1-5, all rating questions)", Cell(s["average_rating"], DECIMAL)],
        ["Activities with all required documents approved", s["fully_compliant_activities"]],
        ["Certificates issued", s["certificates_issued"]],
        ["Certificates emailed", s["certificates_emailed"]],
    ]
    return Section(
        "summary", SECTIONS["summary"],
        tables=[Table("Key figures", [Column("Measure"), Column("Value", INT)], rows)],
        note=f"Conducted activities with an end date from {long_date(filters.start)} to {long_date(filters.end)}. "
             "Certificates count by the date they were issued or emailed.",
    )


def _activities(filters: ReportFilters) -> Section:
    rows = [
        [r["title"], r["activity_type__name"], r["categories"], r["start_date"], r["end_date"],
         format_time_range(r["start_time"], r["end_time"]), r["venue"],
         r["registered"], r["attended"], r["attendance_rate"], r["evaluations"], r["average_rating"]]
        for r in metrics.activity_rows(filters)
    ]
    columns = [
        Column("Title", key="title"), Column("Type"), Column("Categories"), Column("Start", DATE, key="start"),
        Column("End", DATE, key="end"), Column("Time", key="time"), Column("Venue", key="venue"),
        Column("Registered", INT), Column("Attended", INT), Column("Attendance rate", PERCENT),
        Column("Evaluations", INT), Column("Average rating", DECIMAL),
    ]
    return Section("activities", SECTIONS["activities"], tables=[Table("Activities", columns, rows)])


def _total_row(label: str, summary: dict, *, with_rate: bool = True) -> list:
    """The totals row of the by-type, by-category and per-month tables: the period's own numbers."""
    row = [label, summary["activities_conducted"], summary["registered"], summary["attended"]]
    return row + [summary["attendance_rate"]] if with_rate else row


def _by_type(filters: ReportFilters, summary: dict) -> Section:
    data = metrics.by_type(filters)
    rows = [[r["name"], r["activities"], r["registered"], r["attended"], r["attendance_rate"]] for r in data]
    columns = [Column("Activity type"), Column("Activities", INT), Column("Registered", INT), Column("Attended", INT),
               Column("Attendance rate", PERCENT)]
    return Section(
        "by_type", SECTIONS["by_type"], tables=[Table("By activity type", columns, rows, total=_total_row("Total", summary))],
        chart=Chart("Activities conducted by type", [(r["name"], r["activities"]) for r in data]),
    )


def _by_category(filters: ReportFilters, summary: dict) -> Section:
    data = metrics.by_category(filters)
    rows = [[r["name"], r["activities"], r["registered"], r["attended"], r["attendance_rate"]] for r in data]
    columns = [Column("Participant category"), Column("Activities for this category", INT), Column("Registered", INT),
               Column("Attended", INT), Column("Attendance rate", PERCENT)]
    return Section(
        "by_category", SECTIONS["by_category"], tables=[Table("By participant category", columns, rows, note=metrics.CATEGORY_NOTE,
                      total=_total_row("Total (distinct activities)", summary))],
        chart=Chart("Attendees by participant category", [(r["name"], r["attended"]) for r in data]),
    )


SHARE_NOTE = (
    "Percentages are of the activity's registered or attended participants, rounded to one decimal place so that "
    "each activity's percentages add up to 100%. A dash (or an empty cell in Excel): nobody registered or attended yet."
)


def _category_by_activity(filters: ReportFilters) -> Section:
    """For each period activity: registered and attended per participant category, with percentages
    (metrics.participants_by_category, ASSUMPTIONS 400). One row per activity and category, so the Excel table can be
    filtered; the activity's own totals are in the Activities section."""
    activities = metrics.activity_rows(filters)
    breakdown = metrics.participants_by_category([row["activity_id"] for row in activities])
    rows = [
        [activity["title"], activity["start_date"], activity["end_date"], item["category"],
         item["registered"], item["registered_share"], item["attended"], item["attended_share"]]
        for activity in activities
        for item in breakdown[activity["activity_id"]]["rows"]
    ]
    columns = [
        Column("Activity", key="title"), Column("Start", DATE, key="start"), Column("End", DATE, key="end"),
        Column("Participant category"), Column("Registered", INT), Column("% of registered", PERCENT),
        Column("Attended", INT), Column("% of attended", PERCENT),
    ]
    return Section(
        "category_by_activity", SECTIONS["category_by_activity"],
        tables=[Table("Participants by category per activity", columns, rows, note=SHARE_NOTE)],
    )


def _monthly(filters: ReportFilters, summary: dict) -> Section:
    data = metrics.per_month(filters)
    rows = [[r["label"], r["activities"], r["registered"], r["attended"]] for r in data]
    columns = [Column("Month"), Column("Activities", INT), Column("Registered", INT), Column("Attended", INT)]
    return Section(
        "monthly", SECTIONS["monthly"],
        tables=[Table("Per month (by end date)", columns, rows, total=_total_row("Total", summary, with_rate=False))],
        chart=Chart("Activities conducted per month", [(r["label"], r["activities"]) for r in data]),
    )


BUDGET_NOTE = ("The budget is copied from each activity's Activity Design (approved on paper): the sum of its budget "
               "lines. \"Not set\": an older activity whose fund source was never chosen.")


def _budget(filters: ReportFilters, summary: dict) -> Section:
    """Budget by fund source (ASSUMPTIONS 433): metrics.budget_by_fund_source (N20); the total row is N1 and N19, never
    the rows added up here."""
    rows = [[r["fund_source"], r["activities"], r["total"]] for r in metrics.budget_by_fund_source(filters)]
    columns = [Column("Fund source"), Column("Activities", INT), Column("Total budget", MONEY)]
    total = ["Total", summary["activities_conducted"], metrics.period_budget_total(filters)]
    return Section("budget", SECTIONS["budget"],
                   tables=[Table("Budget by fund source", columns, rows, total=total)], note=BUDGET_NOTE)


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


NOT_REQUIRED = "Not required"  # compliance cell of a type that does not apply to the activity (ASSUMPTIONS 425)


def _documents(filters: ReportFilters, now: datetime | None) -> Section:
    data = metrics.document_compliance(metrics.period_activities(filters), now)
    columns = [Column("Activity", key="title"), Column("End", DATE)] + [Column(name) for name in data["document_types"]] + [Column("All approved")]
    rows, overdue = [], 0
    for activity in data["activities"]:
        by_name = {item["document_type"]: item for item in activity["items"]}
        states = []
        for name in data["document_types"]:
            item = by_name.get(name)
            if item is None:  # a payment type that does not apply to this older activity (ASSUMPTIONS 425)
                states.append(NOT_REQUIRED)
                continue
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
             "its documents is Approved (uploads are approved copies); overdue means after the due date and not "
             "Approved.",
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
                ["Certificates waiting for their activity's approval", now_counts["waiting_certificates"]],
                ["Activities that need certificate approval", now_counts["waiting_activities"]],
                ["Approved, not issued yet", now_counts["approved_not_issued"]],
            ]),
        ],
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
        "by_type": lambda: _by_type(filters, summary),
        "by_category": lambda: _by_category(filters, summary),
        "category_by_activity": lambda: _category_by_activity(filters),
        "monthly": lambda: _monthly(filters, summary),
        "budget": lambda: _budget(filters, summary),
        "evaluation": lambda: _evaluation(filters),
        "documents": lambda: _documents(filters, now),
        "certificates": lambda: _certificates(filters, summary, today),
    }
    activity_type = (
        ActivityType.objects.get(pk=filters.activity_type_id).name if filters.activity_type_id else "All activity types"
    )
    name = period_name(filters.start, filters.end)
    return ReportContent(
        title=report_title(report.label, filters.start, filters.end),
        report_type=report_type,
        report_type_label=report.label,
        filters=filters,
        activity_type_name=activity_type,
        generated_by=generated_by,
        generated_at=generated_at,
        today=today,
        summary=summary,
        period_in_words=period,
        period_name=name.label if name else "",
        has_activities=has_activities,
        sections=[
            builders[key]() if has_activities or key not in PERIOD_SECTIONS else Section(key, SECTIONS[key], note=NO_ACTIVITIES)
            for key in ordered_sections(sections)
        ],
    )


# --- One activity's evaluation summary (ASSUMPTIONS 401) -----------------------------------------------------------
# (One participant's evaluation has its own portrait template: reports.pdf.individual_evaluation_html, ASSUMPTIONS 404.)
# Built as a ReportContent, so the PDF (the landscape report template) and the Excel file (titles, real tables,
# formula-safe text) are made by the same code as the reports. `meta_lines` replace the period lines in the header.

EVALUATION_SUMMARY = "evaluation_summary"
NO_EVALUATIONS = "No evaluations submitted yet."
RATING_NOTE = ("Percentages are of the answers to this question, rounded to one decimal place so they add up to 100%. "
               "Mean = the average rating (1-5), two decimal places.")
TEXT_NOTE = "Listed without names, sorted A-Z (not in the order they were submitted). Empty answers are left out."


def _activity_meta(activity) -> list[tuple[str, str]]:
    from activities.formatting import format_date_span

    return [("Activity", activity.title), ("Dates", format_date_span(activity.start_date, activity.end_date)),
            ("Activity type", activity.activity_type.name)]


def _activity_content(activity, *, key: str, label: str, sections: list[Section], generated_by: str,
                      generated_at: datetime, meta: list[tuple[str, str]]) -> ReportContent:
    return ReportContent(
        title=f"{label} - {activity.title}",
        report_type=key,
        report_type_label=label,
        filters=ReportFilters(activity.start_date, activity.end_date, activity.activity_type_id),
        activity_type_name=activity.activity_type.name,
        generated_by=generated_by,
        generated_at=generated_at,
        today=metrics.today_in_manila(generated_at),
        summary={},
        period_in_words=range_in_words(activity.start_date, activity.end_date),
        has_activities=True,
        sections=sections,
        meta_lines=meta,
    )


def build_evaluation_summary(activity, data: dict, *, generated_by: str, generated_at: datetime) -> ReportContent:
    """The evaluation summary of ONE activity from metrics.activity_evaluation_summary (no names anywhere)."""
    responses = Section("responses", "Responses", tables=[Table("Responses vs attendees", [Column("Measure"), Column("Value", INT)], [
        ["Registered", data["registered"]],
        ["Cancelled (\u201cWill not attend\u201d, not counted)", data.get("cancelled", 0)],
        ["Attended", data["attended"]],
        ["Evaluations submitted", data["evaluations"]],
        ["Response rate (evaluations / attended)", Cell(data["evaluation_response_rate"], PERCENT)],
    ])], note=(f"{data['evaluations']} of {data['attended']} attendee{'' if data['attended'] == 1 else 's'} "
               "submitted the evaluation."))
    if not data["evaluations"]:
        return _activity_content(
            activity, key=EVALUATION_SUMMARY, label="Evaluation Summary", generated_by=generated_by,
            generated_at=generated_at, meta=_activity_meta(activity),
            sections=[responses, Section("ratings", "Rating questions", note=NO_EVALUATIONS)],
        )

    rating_tables = [Table("Overview", [Column("Rating question", key="title"), Column("Answers", INT),
                                        Column("Mean (1-5)", DECIMAL)],
                           [[q["question"], q["answers"], q["mean"]] for q in data["ratings"]])]
    for question in data["ratings"]:
        rating_tables.append(Table(
            question["question"], [Column("Rating"), Column("Frequency", INT), Column("Percentage", PERCENT)],
            [[str(item["rating"]), item["count"], item["share"]] for item in question["frequency"]],
            total=["Total", question["answers"], 1.0 if question["answers"] else None],
        ))
    ratings = Section("ratings", "Rating questions", tables=rating_tables if data["ratings"] else [],
                      note=RATING_NOTE if data["ratings"] else "The evaluation form has no rating questions.")

    text_tables = [Table(item["question"], [Column("Answer", key="title")], [[answer] for answer in item["answers"]])
                   for item in data["text_answers"]]
    if data["comments"]:
        text_tables.append(Table("Comments", [Column("Comment", key="title")], [[text] for text in data["comments"]]))
    texts = Section("text_answers", "Text answers", tables=text_tables,
                    note=TEXT_NOTE if text_tables else "No text answers were given.")
    return _activity_content(activity, key=EVALUATION_SUMMARY, label="Evaluation Summary", generated_by=generated_by,
                             generated_at=generated_at, meta=_activity_meta(activity), sections=[responses, ratings, texts])
