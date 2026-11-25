"""Every number on the dashboard and in the reports. ONE source of truth: the dashboard API, the
PDF and the Excel file all call these functions. The definitions (N1-N18, S1-S6) are in
docs/ASSUMPTIONS.md, Step 10.

Two scopes:
- "period" numbers count only Conducted activities whose end_date (a Manila date) is in the
  date range, optionally of one activity type. Exception: S1 (activities by status) counts every
  status, Draft included, by start_date in the range.
- "now" numbers (S2-S6) are a snapshot of today: they ignore the date range but respect the
  activity type filter.

Everything is counted by the database (aggregates, EXISTS and subqueries), never by looping over
registrations, answers or documents in Python. Python only reshapes the (small) results.
"""

import re
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from decimal import Decimal

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db.models import (
    Avg,
    BooleanField,
    Count,
    Exists,
    ExpressionWrapper,
    FloatField,
    IntegerField,
    Min,
    OuterRef,
    Q,
    QuerySet,
    StringAgg,
    Subquery,
    Sum,
    Value,
)
from django.db.models.functions import Cast, Coalesce, Lower, Trim, TruncMonth

from activities.models import (
    Activity, ActivityParticipantCategory, ActivityStatus, ActivityType, FundSource, ParticipantCategory,
)
from certificates.models import Certificate
from documents.models import Document, DocumentStatus, DocumentType
from documents.services import due_date, today_in_manila, type_applies
from registrations.models import EvaluationSubmission, FieldType, FormAnswer, FormField, FormType, Registration
from startups.models import Startup

PERIOD = "period"  # depends on the date range
NOW = "now"  # snapshot of today; ignores the date range

STATUS_ORDER = [ActivityStatus.DRAFT, ActivityStatus.SCHEDULED, ActivityStatus.ONGOING, ActivityStatus.CONDUCTED]
UPCOMING_LIMIT = 5
UPCOMING_WINDOW_DAYS = 30  # S2 detail: starting within the next 30 days (today included)
MAX_RANGE_DAYS = 3660  # about 10 years: keeps the per-month list small

APPROVED, FOR_REVIEW, MISSING = "Approved", "For Review", "Missing"  # same words as the Step 6 checklist

CATEGORY_NOTE = (
    "An activity can be for several participant categories, so the activities per category can add up "
    "to more than the total. Registrations and attendees add up to the totals: each registration has one category."
)


@dataclass(frozen=True)
class ReportFilters:
    """The date range (inclusive, Manila dates) and the optional activity type."""

    start: date
    end: date
    activity_type_id: int | None = None


# --- Filters -----------------------------------------------------------------------------------


def default_filters(now: datetime | None = None) -> ReportFilters:
    """The current year in Manila: January 1 to December 31."""
    year = today_in_manila(now).year
    return ReportFilters(date(year, 1, 1), date(year, 12, 31))


ISO_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
FORMAT_MESSAGE = "Use the format YYYY-MM-DD, for example 2026-06-30."


def _parse_date(value, field: str, errors: dict) -> date | None:
    """A date written exactly as YYYY-MM-DD. Python's own parser also accepts forms like 20260630 or
    2026-W26-2 (a week date), so the shape is checked first."""
    if value in (None, ""):
        return None
    if isinstance(value, date):  # from Python code (tests, commands), not from a request
        return value
    if not isinstance(value, str) or not ISO_DATE.match(value.strip()):
        errors[field] = [FORMAT_MESSAGE]
        return None
    try:
        return date.fromisoformat(value.strip())
    except ValueError:
        errors[field] = [f"{value.strip()} is not a real date. {FORMAT_MESSAGE}"]
        return None


def parse_filters(params: dict, now: datetime | None = None, *, dates_required: bool = False) -> ReportFilters:
    """Read start, end (YYYY-MM-DD) and activity_type (an ID) from request data.

    Reports need both dates (`dates_required=True`). The dashboard may leave them out: a missing
    start or end takes the current year's January 1 / December 31 (Manila).
    Raises ValidationError with one message per field.
    """
    errors: dict[str, list[str]] = {}
    defaults = default_filters(now)
    start = _parse_date(params.get("start"), "start", errors)
    end = _parse_date(params.get("end"), "end", errors)
    for field, value in (("start", start), ("end", end)):
        if value is None and field not in errors and dates_required:
            errors[field] = [f"Required. {FORMAT_MESSAGE}"]
    start, end = start or defaults.start, end or defaults.end
    if not errors:
        if start > end:
            errors["end"] = [
                f"The end date ({end.isoformat()}) is before the start date ({start.isoformat()}). "
                "The end date must be on or after the start date."
            ]
        elif (end - start).days > MAX_RANGE_DAYS:
            errors["end"] = ["The date range can be at most 10 years."]

    activity_type_id = None
    raw_type = params.get("activity_type")
    if raw_type not in (None, ""):
        if not str(raw_type).isdigit():
            errors["activity_type"] = ["activity_type must be an activity type ID."]
        elif not ActivityType.objects.filter(pk=int(raw_type)).exists():
            errors["activity_type"] = ["Unknown activity type."]
        else:
            activity_type_id = int(raw_type)
    if errors:
        raise ValidationError(errors)
    return ReportFilters(start, end, activity_type_id)


# --- Building blocks -----------------------------------------------------------------------------


def _of_type(queryset: QuerySet, filters: ReportFilters, path: str = "activity_type_id") -> QuerySet:
    return queryset.filter(**{path: filters.activity_type_id}) if filters.activity_type_id else queryset


def in_period(filters: ReportFilters, activity: str = "activity") -> Q:
    """Rows whose activity (reached through `activity`; "" for Activity itself) is a period activity:
    Conducted, end_date in the range, of the chosen type. Written as plain joins, not
    "activity IN (subquery)": PostgreSQL plans joins well even when its table statistics are stale."""
    prefix = f"{activity}__" if activity else ""
    condition = Q(**{
        f"{prefix}status__name": ActivityStatus.CONDUCTED,
        f"{prefix}end_date__gte": filters.start,
        f"{prefix}end_date__lte": filters.end,
    })
    if filters.activity_type_id:
        condition &= Q(**{f"{prefix}activity_type_id": filters.activity_type_id})
    return condition


def period_activities(filters: ReportFilters) -> QuerySet:
    """N1: Conducted activities with end_date in the range (and of the chosen type)."""
    return Activity.objects.filter(in_period(filters, ""))


def _count_of(queryset: QuerySet, link: str) -> Coalesce:
    """How many rows of `queryset` point (through `link`) to the outer row. A subquery, so several
    counts on one row never multiply each other the way joins would."""
    counted = queryset.filter(**{link: OuterRef("pk")}).order_by().values(link).annotate(n=Count("pk")).values("n")
    return Coalesce(Subquery(counted, output_field=IntegerField()), 0)


# Who counts as registered (ASSUMPTIONS 440, owner decision 2026-11-25): a cancelled registration ("Will not attend",
# item 434) is NOT counted as registered in the reports, the attendance rate, the participant mix or Needs attention,
# unless the person came anyway (then registered AND attended). Walk-ins count. The slot rule
# (activities.services.registered_count) is separate and unchanged.
COUNTED = Q(cancelled_at__isnull=True) | Q(attendance_confirmed=True)
NOT_COUNTED = Q(cancelled_at__isnull=False, attendance_confirmed=False)  # shown as "Cancelled: N"


def counted_registrations() -> QuerySet:
    """REGISTRATION rows that count as registered (COUNTED)."""
    return Registration.objects.filter(COUNTED)


def rate(part: int, whole: int) -> float | None:
    """part / whole as a fraction (0.85 = 85%), or None when there is nothing to divide by."""
    return round(part / whole, 4) if whole else None


def _average(total: int | None, count: int) -> float | None:
    return round(total / count, 2) if count and total is not None else None


def participant_average(ratings: list[int]) -> float | None:
    """One participant's average rating (ASSUMPTIONS 404): the same 2-decimal rounding as the evaluation summary's
    mean; None when they answered no rating question."""
    return _average(sum(ratings), len(ratings))


RATING_ACTIVITY = "evaluation_submission__registration__activity"  # from FORM_ANSWER to its activity


def _rating_answers() -> QuerySet:
    """Evaluation answers to rating questions (stored as "1".."5", checked by validate_answers)."""
    return FormAnswer.objects.filter(
        answer_source=FormAnswer.AnswerSource.EVALUATION, field__field_type=FieldType.RATING
    )


def _rating_value():
    return Cast("answer_value", IntegerField())


def _months(start: date, end: date) -> list[date]:
    """The first day of every month from start to end (inclusive)."""
    months, current = [], date(start.year, start.month, 1)
    while current <= end:
        months.append(current)
        current = date(current.year + (current.month == 12), current.month % 12 + 1, 1)
    return months


# --- Period numbers ------------------------------------------------------------------------------


def period_summary(filters: ReportFilters, now: datetime | None = None, types: list[DocumentType] | None = None) -> dict:
    """The headline period numbers (N1, N5-N8, N10, N11, N13, N15-N18), used by the dashboard cards
    and the report's Summary section."""
    activities = period_activities(filters)
    activities_conducted = activities.count()
    registrations = Registration.objects.filter(in_period(filters)).aggregate(
        registered=Count("pk", filter=COUNTED),
        cancelled=Count("pk", filter=NOT_COUNTED),
        attended=Count("pk", filter=Q(attendance_confirmed=True)),
        unique_attendees=Count(Lower("email"), filter=Q(attendance_confirmed=True), distinct=True),
        evaluations=Count("evaluation_submission"),
    )
    ratings = _rating_answers().filter(in_period(filters, RATING_ACTIVITY)).aggregate(
        answers=Count("pk"), total=Sum(_rating_value())
    )
    certificates = certificates_in_period(filters)
    return {
        "activities_conducted": activities_conducted,
        "registered": registrations["registered"],
        "cancelled": registrations["cancelled"],
        "attended": registrations["attended"],
        "attendance_rate": rate(registrations["attended"], registrations["registered"]),
        "unique_attendees": registrations["unique_attendees"],
        "evaluations": registrations["evaluations"],
        "evaluation_response_rate": rate(registrations["evaluations"], registrations["attended"]),
        "average_rating": _average(ratings["total"], ratings["answers"]),
        "rating_answers": ratings["answers"],
        "fully_compliant_activities": fully_compliant_count(filters, types),
        "certificates_issued": certificates["issued_participation"] + certificates["issued_appreciation"],
        "certificates_issued_participation": certificates["issued_participation"],
        "certificates_issued_appreciation": certificates["issued_appreciation"],
        "certificates_emailed": certificates["emailed"],
        "startups_engaged": engaged_startups(filters).count(),
    }


# --- Budget (ASSUMPTIONS 433) -----------------------------------------------------------------------------------------

BUDGET_NOT_SET = "Not set"  # activities from before the budget lines without a fund source (activities.budget.NOT_SET)


def period_budget_total(filters: ReportFilters) -> Decimal:
    """N19: the total budget (ACTIVITY.budget_amount = the sum of its budget lines) of the period's Conducted
    activities. Decimal, 0.00 when there are none."""
    total = period_activities(filters).aggregate(total=Sum("budget_amount"))["total"]
    return (total or Decimal("0.00")).quantize(Decimal("0.01"))


def budget_by_fund_source(filters: ReportFilters) -> list[dict]:
    """N20: the period's Conducted activities per fund source: {"fund_source" (code - name or "Not set"), "code",
    "activities", "total"}. Every ACTIVE fund source is listed (0 when unused), in its own order; an inactive one only
    when used; "Not set" last and only when some activity has none. One query; the rows add up to N1 and N19."""
    grouped = {
        row["fund_source_id"]: row for row in
        period_activities(filters).values("fund_source_id").annotate(activities=Count("pk"), total=Sum("budget_amount"))
    }
    rows = []
    for source in FundSource.objects.order_by("sort_order", "code"):
        found = grouped.get(source.pk)
        if found is None and not source.is_active:
            continue
        rows.append({"fund_source": str(source), "code": source.code, "activities": found["activities"] if found else 0,
                     "total": (found["total"] if found else Decimal("0")).quantize(Decimal("0.01"))})
    if None in grouped:
        rows.append({"fund_source": BUDGET_NOT_SET, "code": "", "activities": grouped[None]["activities"],
                     "total": grouped[None]["total"].quantize(Decimal("0.01"))})
    return rows


def activity_rows(filters: ReportFilters) -> list[dict]:
    """One row per period activity with its own registered / attended / evaluation numbers."""
    category_names = (
        ActivityParticipantCategory.objects.filter(activity=OuterRef("pk"))
        .order_by()
        .values("activity")
        .annotate(names=StringAgg("participant_category__name", Value(", "), order_by="participant_category__name"))
        .values("names")
    )
    average_rating = (
        _rating_answers()
        .filter(evaluation_submission__registration__activity=OuterRef("pk"))
        .order_by()
        .values("evaluation_submission__registration__activity")
        .annotate(average=Avg(Cast("answer_value", FloatField())))
        .values("average")
    )
    rows = (
        period_activities(filters)
        .annotate(
            registered=_count_of(counted_registrations(), "activity"),
            cancelled=_count_of(Registration.objects.filter(NOT_COUNTED), "activity"),
            attended=_count_of(Registration.objects.filter(attendance_confirmed=True), "activity"),
            evaluations=_count_of(EvaluationSubmission.objects.all(), "registration__activity"),
            average_rating=Subquery(average_rating, output_field=FloatField()),
            categories=Subquery(category_names),
        )
        .order_by("end_date", "start_date", "title", "pk")
        .values(
            "activity_id", "title", "activity_type__name", "categories", "start_date", "end_date", "start_time",
            "end_time", "venue", "registered", "cancelled", "attended", "evaluations", "average_rating",
        )
    )
    return [
        {
            **row,
            "attendance_rate": rate(row["attended"], row["registered"]),
            "average_rating": round(row["average_rating"], 2) if row["average_rating"] is not None else None,
            "categories": row["categories"] or "",
        }
        for row in rows
    ]


def _with_rate(row: dict) -> dict:
    """Rename activity_count (the model already has an "activities" relation) and add the attendance rate."""
    row = dict(row)
    row["activities"] = row.pop("activity_count")
    return {**row, "attendance_rate": rate(row["attended"], row["registered"])}


def by_type(filters: ReportFilters) -> list[dict]:
    """N2: period activities, registered and attended per activity type (adds up to the totals)."""
    activities = period_activities(filters)
    registrations = counted_registrations().filter(in_period(filters))
    rows = (
        _of_type(ActivityType.objects.all(), filters, "pk")
        .annotate(
            activity_count=_count_of(activities, "activity_type"),
            registered=_count_of(registrations, "activity__activity_type"),
            attended=_count_of(registrations.filter(attendance_confirmed=True), "activity__activity_type"),
        )
        .filter(activity_count__gt=0)
        .order_by("name")
        .values("activity_type_id", "name", "activity_count", "registered", "attended")
    )
    return [_with_rate(row) for row in rows]


def by_category(filters: ReportFilters) -> list[dict]:
    """N3 (activities per category, can add up to more than N1) and N9 (registered / attended per
    the registration's category, adds up to N5 / N6). Every category is listed, also with zero."""
    registrations = counted_registrations().filter(in_period(filters))
    rows = (
        ParticipantCategory.objects.annotate(
            activity_count=_count_of(ActivityParticipantCategory.objects.filter(in_period(filters)), "participant_category"),
            registered=_count_of(registrations, "participant_category"),
            attended=_count_of(registrations.filter(attendance_confirmed=True), "participant_category"),
        )
        .order_by("name")
        .values("participant_category_id", "name", "activity_count", "registered", "attended")
    )
    return [_with_rate(row) for row in rows]


def shares(counts: list[int], units: int = 1000) -> list[float | None]:
    """Each count's share of their sum as a fraction rounded to 0.1 % (0.333 = 33.3 %), so that the shares add up to
    exactly 100.0 % (ASSUMPTIONS 400): every share is rounded down to 0.1 %, then the 0.1 %s still missing go to the
    largest remainders (ties: the earlier row). Integers only, so no float rounding surprises. Blank-safe: when the sum
    is 0 every share is None (shown blank, never 0 % and never a division by zero).

    `units` is the number of steps in 100 %: 1000 = 0.1 % (reports), 100 = whole percentages (the activity page's
    donut, ASSUMPTIONS 403), with the same largest-remainder rule."""
    total = sum(counts)
    if not total:
        return [None] * len(counts)
    tenths = [count * units // total for count in counts]
    remainders = [count * units % total for count in counts]
    for index in sorted(range(len(counts)), key=lambda i: (-remainders[i], i))[: units - sum(tenths)]:
        tenths[index] += 1
    return [value / units for value in tenths]


def participants_by_category(activity_ids: list[int]) -> dict[int, dict]:
    """Registered and attended per participant category for each of these activities, with each category's share of
    the activity's registered / attended (`shares`, adds up to 100.0 %; None when nobody registered / attended)
    (ASSUMPTIONS 400). An activity lists its own categories (ACTIVITY_PARTICIPANT_CATEGORY) and any other category it
    has registrations in, also with zero, by name. Two queries, whatever the number of activities.

    Returns {activity_id: {"registered": n, "attended": n, "rows": [{"participant_category_id", "category",
    "registered", "registered_share", "attended", "attended_share"}]}}."""
    counts = (
        counted_registrations().filter(activity_id__in=activity_ids)
        .order_by()
        .values("activity_id", "participant_category_id")
        .annotate(registered=Count("pk"), attended=Count("pk", filter=Q(attendance_confirmed=True)))
    )
    found: dict[int, dict[int, dict]] = {activity_id: {} for activity_id in activity_ids}
    for row in counts:
        found[row["activity_id"]][row["participant_category_id"]] = row
    links = ActivityParticipantCategory.objects.filter(activity_id__in=activity_ids).values_list(
        "activity_id", "participant_category_id")
    for activity_id, category_id in links:
        found[activity_id].setdefault(category_id, {"registered": 0, "attended": 0})
    category_ids = {category_id for per_activity in found.values() for category_id in per_activity}
    names = dict(ParticipantCategory.objects.filter(pk__in=category_ids).values_list("pk", "name"))

    result = {}
    for activity_id, per_activity in found.items():
        ordered = sorted(per_activity.items(), key=lambda item: (names[item[0]].lower(), item[0]))
        registered = [row["registered"] for _id, row in ordered]
        attended = [row["attended"] for _id, row in ordered]
        result[activity_id] = {
            "registered": sum(registered),
            "attended": sum(attended),
            "rows": [
                {"participant_category_id": category_id, "category": names[category_id],
                 "registered": row["registered"], "registered_share": registered_share,
                 "attended": row["attended"], "attended_share": attended_share}
                for (category_id, row), registered_share, attended_share in zip(ordered, shares(registered), shares(attended))
            ],
        }
    return result


# --- Participant mix (ASSUMPTIONS 424, NAVIGATU guideline) ----------------------------------------------------------

MIX_OTHER = "Other categories"


def _mix_groups() -> list[str]:
    """The rows of the mix, in order: Faculty, Incubatees, MSMEs (settings), then MIX_OTHER."""
    return [settings.PARTICIPANT_MIX_FACULTY, *settings.PARTICIPANT_MIX_INCUBATEES, MIX_OTHER]


def _mix_group(category_name: str) -> str:
    """The mix row of a category: matched by name ignoring case and outer spaces (seeded names cannot be renamed);
    any other category is MIX_OTHER."""
    key = (category_name or "").strip().lower()
    return next((group for group in _mix_groups()[:-1] if group.lower() == key), MIX_OTHER)


def incubatee_group_name() -> str:
    """"Incubatees and MSMEs": the categories counted together for the minimum."""
    names = settings.PARTICIPANT_MIX_INCUBATEES
    return names[0] if len(names) == 1 else f"{', '.join(names[:-1])} and {names[-1]}"


def guideline_text() -> str:
    """NAVIGATU's guideline in words (shown next to every mix chart)."""
    return (f"NAVIGATU's guideline: {settings.PARTICIPANT_MIX_FACULTY} at most "
            f"{settings.PARTICIPANT_MIX_FACULTY_MAX_PERCENT}%, {incubatee_group_name()} at least "
            f"{settings.PARTICIPANT_MIX_INCUBATEES_MIN_PERCENT}% (MSMEs count with the Incubatees until NAVIGATU "
            "confirms).")


def mix_check(faculty: int, incubatees: int, total: int) -> dict | None:
    """The one guideline check (integers only, never rounded first): Faculty at most PARTICIPANT_MIX_FACULTY_MAX_PERCENT %
    and Incubatees + MSMEs at least PARTICIPANT_MIX_INCUBATEES_MIN_PERCENT % of `total`. None when total is 0.
    Returns {"within": bool, "problems": [short texts, e.g. "Faculty 45% (at most 30%)"]}."""
    if not total:
        return None
    faculty_max = settings.PARTICIPANT_MIX_FACULTY_MAX_PERCENT
    incubatees_min = settings.PARTICIPANT_MIX_INCUBATEES_MIN_PERCENT
    problems = []
    if faculty * 100 > faculty_max * total:
        problems.append(f"{settings.PARTICIPANT_MIX_FACULTY} {round(100 * faculty / total)}% (at most {faculty_max}%)")
    if incubatees * 100 < incubatees_min * total:
        problems.append(f"{incubatee_group_name()} {round(100 * incubatees / total)}% (at least {incubatees_min}%)")
    return {"within": not problems, "problems": problems}


def _mix(counts: dict[str, dict[str, int]], checked: bool) -> dict:
    """The mix from {group: {"registered": n, "attended": n}}: rows with whole percentages (`shares`, units=100: they add
    up to 100 %; None when the total is 0), the basis ("attended" once anybody attended, else "registered"; None when
    nobody registered) and the guideline check on that basis (None when not `checked` or nothing to count)."""
    groups = _mix_groups()
    registered = [counts.get(group, {}).get("registered", 0) for group in groups]
    attended = [counts.get(group, {}).get("attended", 0) for group in groups]
    rows = [
        {"group": group, "registered": reg, "registered_share": reg_share, "attended": att, "attended_share": att_share}
        for group, reg, reg_share, att, att_share in zip(
            groups, registered, shares(registered, 100), attended, shares(attended, 100))
        if group != MIX_OTHER or reg or att  # "Other categories" only when someone is in it
    ]
    basis = "attended" if sum(attended) else "registered" if sum(registered) else None
    check = None
    if checked and basis:
        values = attended if basis == "attended" else registered
        check = mix_check(values[0], sum(values[1:-1]), sum(values))
    return {"registered": sum(registered), "attended": sum(attended), "rows": rows, "basis": basis,
            "checked": checked, "check": check}


def _guideline_applies(category_names: set[str]) -> bool:
    """The guideline is checked only for activities open to Faculty AND to Incubatees or MSMEs (owner decision
    2026-11-22): a Faculty-only training is never "outside"."""
    groups = {_mix_group(name) for name in category_names}
    return settings.PARTICIPANT_MIX_FACULTY in groups and bool(groups & set(settings.PARTICIPANT_MIX_INCUBATEES))


def participant_mix(activity_ids: list[int]) -> dict[int, dict]:
    """Each activity's participant mix (Faculty, Incubatees, MSMEs, other categories) among registrations and among
    attendees, with NAVIGATU's guideline check where it applies (`_guideline_applies`) (ASSUMPTIONS 424). Two queries,
    whatever the number of activities. Used by the Overview, Needs attention and period_participant_mix."""
    return _mixes(Q(activity_id__in=activity_ids), activity_ids)


def _mixes(activities: Q, activity_ids: list[int] | None = None) -> dict[int, dict]:
    """participant_mix for the activities that `activities` (a Q on REGISTRATION / ACTIVITY_PARTICIPANT_CATEGORY rows,
    through "activity") selects: two queries. `activity_ids`: also list these when they have no rows."""
    rows = (
        counted_registrations().filter(activities).order_by()
        .values("activity_id", "participant_category__name")
        .annotate(registered=Count("pk"), attended=Count("pk", filter=Q(attendance_confirmed=True)))
    )
    links = ActivityParticipantCategory.objects.filter(activities).values_list("activity_id", "participant_category__name")
    counts: dict[int, dict[str, dict[str, int]]] = {activity_id: {} for activity_id in activity_ids or []}
    categories: dict[int, set[str]] = {activity_id: set() for activity_id in activity_ids or []}
    for row in rows:
        group = counts.setdefault(row["activity_id"], {}).setdefault(
            _mix_group(row["participant_category__name"]), {"registered": 0, "attended": 0})
        group["registered"] += row["registered"]
        group["attended"] += row["attended"]
    for activity_id, name in links:
        categories.setdefault(activity_id, set()).add(name)
    ids = set(counts) | set(categories)
    return {activity_id: _mix(counts.get(activity_id, {}), _guideline_applies(categories.get(activity_id, set())))
            for activity_id in ids}


def period_participant_mix(filters: ReportFilters) -> dict:
    """The dashboard's mix for the period (the period activities: Conducted, end date in the range, of the type): all
    their registrations together (the guideline is shown next to it, not checked on the total), plus how many of those
    activities are checked and how many are outside the guideline (each activity's own check, `participant_mix`)."""
    per_activity = _mixes(in_period(filters))  # two queries (the dashboard's query count is fixed)
    totals: dict[str, dict[str, int]] = {}
    for mix in per_activity.values():
        for row in mix["rows"]:
            group = totals.setdefault(row["group"], {"registered": 0, "attended": 0})
            group["registered"] += row["registered"]
            group["attended"] += row["attended"]
    result = _mix(totals, checked=False)
    checked = [mix for mix in per_activity.values() if mix["check"] is not None]
    result["activities_checked"] = len(checked)
    result["activities_outside"] = sum(1 for mix in checked if not mix["check"]["within"])
    return result


def per_month(filters: ReportFilters) -> list[dict]:
    """N4: period activities per month of their end_date, with registered and attended.
    Every month of the range is listed, also with zero."""
    activities = period_activities(filters)
    activity_counts = dict(
        activities.annotate(month=TruncMonth("end_date")).order_by().values("month").annotate(n=Count("pk")).values_list("month", "n")
    )
    registration_counts = {
        row["month"]: row
        for row in counted_registrations().filter(in_period(filters))
        .annotate(month=TruncMonth("activity__end_date"))
        .order_by()
        .values("month")
        .annotate(registered=Count("pk"), attended=Count("pk", filter=Q(attendance_confirmed=True)))
    }
    months = []
    for month in _months(filters.start, filters.end):
        counts = registration_counts.get(month, {})
        months.append({
            "month": month,
            "label": f"{month:%b %Y}",
            "activities": activity_counts.get(month, 0),
            "registered": counts.get("registered", 0),
            "attended": counts.get("attended", 0),
        })
    return months


def evaluation_results(filters: ReportFilters) -> dict:
    """N12 and N13. Rating questions are grouped by their label, ignoring upper/lower case and outer
    spaces (forms are edited per activity). Each group shows the label used most often."""
    return _rating_questions(_rating_answers().filter(in_period(filters, RATING_ACTIVITY)))


def activity_evaluation_results(activity: Activity) -> dict:
    """One activity's evaluation results: the N5, N6, N10, N11 counts and the N12/N13 rating averages
    with the same rules as the reports, but for this activity only and in any status (evaluations
    open on the end date, possibly before the activity is set to Conducted). Rating questions only:
    never comments or text answers."""
    counts = Registration.objects.filter(activity=activity).aggregate(
        registered=Count("pk", filter=COUNTED),
        cancelled=Count("pk", filter=NOT_COUNTED),
        attended=Count("pk", filter=Q(attendance_confirmed=True)),
        evaluations=Count("evaluation_submission"),
    )
    ratings = _rating_questions(_rating_answers().filter(**{RATING_ACTIVITY: activity}))
    return {
        "activity_id": activity.pk,
        "registered": counts["registered"],
        "cancelled": counts["cancelled"],
        "attended": counts["attended"],
        "evaluations": counts["evaluations"],
        "evaluation_response_rate": rate(counts["evaluations"], counts["attended"]),
        **ratings,
    }


def activity_registration_counts(activity: Activity) -> dict:
    """One activity's registration numbers with the reports' rule (COUNTED): registered (walk-ins included), cancelled
    ("Will not attend", not counted), walk-ins, attended (at least one day) and the attendance rate. One query."""
    counts = Registration.objects.filter(activity=activity).aggregate(
        registered=Count("pk", filter=COUNTED),
        cancelled=Count("pk", filter=NOT_COUNTED),
        walk_ins=Count("pk", filter=Q(registration_source=Registration.Source.WALK_IN)),
        attended=Count("pk", filter=Q(attendance_confirmed=True)),
    )
    return {**counts, "attendance_rate": rate(counts["attended"], counts["registered"])}


RATING_VALUES = (5, 4, 3, 2, 1)  # the rows of a frequency table, best first (ratings are 1-5, validate_answers)
TEXT_FIELD_TYPES = (FieldType.SHORT_TEXT, FieldType.LONG_TEXT)


def activity_evaluation_summary(activity: Activity) -> dict:
    """One activity's evaluation summary (ASSUMPTIONS 401): responses vs attendees; per rating question of the
    activity's evaluation form (form order) the frequency of each rating 5..1, its share of that question's answers
    (`shares`, adds up to 100.0 %) and the mean (2 decimals, None without answers); the text answers per text question
    and the comments, WITHOUT names (never the registration, the certificate name or the email), empty ones left out,
    sorted A-Z (ignoring case) so they cannot be matched to the order of submission. No interpretation scale (owner
    decision 2026-11-17: mean only until the TBI gives its scale). Five queries."""
    counts = Registration.objects.filter(activity=activity).aggregate(
        registered=Count("pk", filter=COUNTED),
        cancelled=Count("pk", filter=NOT_COUNTED),
        attended=Count("pk", filter=Q(attendance_confirmed=True)),
        evaluations=Count("evaluation_submission"),
    )
    fields = list(
        FormField.objects.filter(template__activity=activity, template__form_type=FormType.EVALUATION)
        .filter(field_type__in=(FieldType.RATING, *TEXT_FIELD_TYPES))
        .order_by("sort_order", "pk")
        .values("pk", "field_label", "field_type")
    )
    answers = FormAnswer.objects.filter(answer_source=FormAnswer.AnswerSource.EVALUATION,
                                        evaluation_submission__registration__activity=activity)
    frequency: dict[int, dict[str, int]] = {}
    for row in (answers.filter(field__field_type=FieldType.RATING).order_by()
                .values("field_id", "answer_value").annotate(n=Count("pk"))):
        frequency.setdefault(row["field_id"], {})[row["answer_value"].strip()] = row["n"]
    texts: dict[int, list[str]] = {}
    for field_id, value in answers.filter(field__field_type__in=TEXT_FIELD_TYPES).values_list("field_id", "answer_value"):
        if value and value.strip():
            texts.setdefault(field_id, []).append(value.strip())
    comments = [text.strip() for text in EvaluationSubmission.objects.filter(registration__activity=activity)
                .values_list("comments", flat=True) if text and text.strip()]

    def a_to_z(values: list[str]) -> list[str]:
        return sorted(values, key=lambda text: (text.casefold(), text))

    ratings = []
    for field in (f for f in fields if f["field_type"] == FieldType.RATING):
        counted = [frequency.get(field["pk"], {}).get(str(value), 0) for value in RATING_VALUES]
        total_answers = sum(counted)
        total = sum(value * count for value, count in zip(RATING_VALUES, counted))
        ratings.append({
            "question": field["field_label"].strip(),
            "answers": total_answers,
            "mean": _average(total, total_answers),
            "frequency": [{"rating": value, "count": count, "share": share}
                          for value, count, share in zip(RATING_VALUES, counted, shares(counted))],
        })
    return {
        "activity_id": activity.pk,
        "registered": counts["registered"],
        "cancelled": counts["cancelled"],
        "attended": counts["attended"],
        "evaluations": counts["evaluations"],
        "evaluation_response_rate": rate(counts["evaluations"], counts["attended"]),
        "ratings": ratings,
        "text_answers": [{"question": f["field_label"].strip(), "answers": a_to_z(texts.get(f["pk"], []))}
                         for f in fields if f["field_type"] in TEXT_FIELD_TYPES],
        "comments": a_to_z(comments),
    }


def period_rating_frequency(filters: ReportFilters) -> list[dict]:
    """The period's rating questions (grouped by label ignoring case and outer spaces, like evaluation_results) with how
    many gave each rating 5..1, each rating's share of that question's answers (`shares`) and the mean (ASSUMPTIONS 445,
    the period Evaluation Summary). One query."""
    rows = (_rating_answers().filter(in_period(filters, RATING_ACTIVITY))
            .annotate(key=Lower(Trim("field__field_label"))).order_by()
            .values("key", "field__field_label", "answer_value").annotate(n=Count("pk"), order=Min("field__sort_order")))
    groups: dict[str, dict] = {}
    for row in rows:
        group = groups.setdefault(row["key"], {"labels": {}, "counts": {}, "order": row["order"]})
        label = row["field__field_label"].strip()
        group["labels"][label] = group["labels"].get(label, 0) + row["n"]
        value = row["answer_value"].strip()
        group["counts"][value] = group["counts"].get(value, 0) + row["n"]
        group["order"] = min(group["order"], row["order"])
    result = []
    for group in sorted(groups.values(), key=lambda g: (g["order"], min(g["labels"]).lower())):
        counted = [group["counts"].get(str(value), 0) for value in RATING_VALUES]
        answers = sum(counted)
        result.append({
            "question": min(group["labels"], key=lambda name: (-group["labels"][name], name)),
            "answers": answers,
            "mean": _average(sum(v * c for v, c in zip(RATING_VALUES, counted)), answers),
            "frequency": [{"rating": value, "count": count, "share": share}
                          for value, count, share in zip(RATING_VALUES, counted, shares(counted))],
        })
    return result


def period_text_answers(filters: ReportFilters) -> list[str]:
    """Every written answer and comment of the period's evaluations, WITHOUT names, sorted A-Z (ignoring case) so they
    cannot be matched to a person or an order (like activity_evaluation_summary). For the AI summary of the open
    answers only (ASSUMPTIONS 445)."""
    texts = [value.strip() for value in FormAnswer.objects.filter(
        in_period(filters, RATING_ACTIVITY), answer_source=FormAnswer.AnswerSource.EVALUATION,
        field__field_type__in=TEXT_FIELD_TYPES).values_list("answer_value", flat=True) if value and value.strip()]
    texts += [value.strip() for value in EvaluationSubmission.objects.filter(in_period(filters, "registration__activity"))
              .values_list("comments", flat=True) if value and value.strip()]
    return sorted(texts, key=lambda text: (text.casefold(), text))


def paper_sheet_mix(category_names: list[str | None], checked: bool = True) -> dict:
    """The participant mix of the rows of a paper sheet (ASSUMPTIONS 443): one name per included row (None = no category
    yet). Same groups, whole percentages and guideline check as participant_mix, counted as attendees; rows without a
    category are counted separately ("no_category") and never in the percentages."""
    counts: dict[str, dict[str, int]] = {}
    missing = 0
    for name in category_names:
        if not name:
            missing += 1
            continue
        group = counts.setdefault(_mix_group(name), {"registered": 0, "attended": 0})
        group["registered"] += 1
        group["attended"] += 1
    return {**_mix(counts, checked), "no_category": missing}


def _rating_questions(answers: QuerySet) -> dict:
    """Group rating answers by question label (ignoring case and outer spaces) with their averages."""
    rows = (
        answers
        .annotate(key=Lower(Trim("field__field_label")))
        .order_by()
        .values("key", "field__field_label")
        .annotate(answers=Count("pk"), total=Sum(_rating_value()), order=Min("field__sort_order"))
    )
    groups: dict[str, dict] = {}
    for row in rows:  # one row per distinct label spelling, not per answer
        label = row["field__field_label"].strip()
        group = groups.setdefault(row["key"], {"answers": 0, "total": 0, "order": row["order"], "labels": {}})
        group["answers"] += row["answers"]
        group["total"] += row["total"]
        group["order"] = min(group["order"], row["order"])
        group["labels"][label] = group["labels"].get(label, 0) + row["answers"]
    questions = sorted(
        (
            {
                "question": min(group["labels"], key=lambda name: (-group["labels"][name], name)),
                "average": _average(group["total"], group["answers"]),
                "answers": group["answers"],
                "order": group["order"],
            }
            for group in groups.values()
        ),
        key=lambda item: (item["order"], item["question"].lower()),
    )
    answers = sum(group["answers"] for group in groups.values())
    total = sum(group["total"] for group in groups.values())
    for question in questions:
        del question["order"]
    return {"questions": questions, "average_rating": _average(total, answers), "rating_answers": answers}


def certificates_in_period(filters: ReportFilters) -> dict:
    """N16 (Issued, by the Manila date of issued_at) and N17 (emailed, by the Manila date of sent_at)."""
    in_range = {"__date__gte": filters.start, "__date__lte": filters.end}
    issued = Q(status=Certificate.Status.ISSUED, **{f"issued_at{k}": v for k, v in in_range.items()})
    return _of_type(Certificate.objects.all(), filters, "activity__activity_type_id").aggregate(
        issued_participation=Count("pk", filter=issued & Q(recipient_type=Certificate.RecipientType.REGISTRATION)),
        issued_appreciation=Count("pk", filter=issued & Q(recipient_type=Certificate.RecipientType.RESOURCE_PERSON)),
        emailed=Count("pk", filter=Q(**{f"sent_at{k}": v for k, v in in_range.items()})),
    )


def engaged_startups(filters: ReportFilters) -> QuerySet:
    """N18: startups with an attended registration in a period activity: linked to the startup, or an
    older public registration with the startup's email (ignoring case), the same rule as
    startups.services.registrations_for. Inactive startups count too (this is history)."""
    matching = Registration.objects.filter(in_period(filters), attendance_confirmed=True).filter(
        Q(startup=OuterRef("pk")) | Q(startup__isnull=True, email__iexact=OuterRef("email"))
    )
    activities_attended = matching.order_by().annotate(one=Value(1)).values("one").annotate(n=Count("activity", distinct=True)).values("n")
    return Startup.objects.filter(Exists(matching)).annotate(
        activities_attended=Subquery(activities_attended, output_field=IntegerField())
    )


# --- Document compliance (same rules as documents.services.document_checklist) -------------------


def required_document_types() -> list[DocumentType]:
    """Active required types, as in the checklist."""
    return list(DocumentType.objects.filter(is_active=True, is_required=True).order_by("name"))


def _documents_of(document_type: DocumentType) -> QuerySet:
    """The outer activity's documents of this type (certificate files never count)."""
    return Document.objects.activity_documents().filter(activity=OuterRef("pk"), document_type=document_type)


def _approved(document_type: DocumentType) -> Exists:
    """An Approved document of this type exists (the type is complete)."""
    return Exists(_documents_of(document_type).filter(status__status_name=DocumentStatus.APPROVED))


def _uploaded(document_type: DocumentType) -> Exists:
    return Exists(_documents_of(document_type))


def _applies(document_type: DocumentType) -> Q:
    """documents.services.type_applies in SQL (ASSUMPTIONS 425, 434): a type in DOCUMENT_TYPES_REQUIRED_FROM only on
    activities ending on or after its date."""
    since = settings.DOCUMENT_TYPES_REQUIRED_FROM.get(document_type.name)
    if since is not None:
        return Q(end_date__gte=since)
    return Q()


def _counts_now(document_type: DocumentType) -> Q:
    """documents.services.counts_now in SQL (ASSUMPTIONS 425): a payment document counts only once Conducted."""
    if document_type.name in settings.DOCUMENT_TYPES_FOR_PAYMENT:
        return Q(status__name=ActivityStatus.CONDUCTED)
    return Q()


def _overdue(document_type: DocumentType, today: date) -> Q:
    """Today is after the due date and nothing is Approved. "today > reference + offset" is written as
    "reference < today - offset", so it compares plain dates. Only where the type applies and counts now
    (ASSUMPTIONS 425)."""
    due_before = {f"{document_type.due_reference}__lt": today - timedelta(days=document_type.due_offset_days)}
    late = Q(**due_before) & ~Q(_approved(document_type)) & _applies(document_type) & _counts_now(document_type)
    if document_type.name in settings.DOCUMENT_TYPES_REQUIRED_AT_CREATION:
        # ASSUMPTIONS 405 (documents.services._required_item): on activities created with their creation documents,
        # an uploaded file (waiting or returned) is never overdue.
        late &= ~(Q(creation_documents_required=True) & Q(_uploaded(document_type)))
    return late


def _all_approved(types: list[DocumentType]) -> Q:
    condition = Q()
    for document_type in types:
        applies = _applies(document_type)
        # A type that does not apply to the activity (ASSUMPTIONS 425) never makes it incomplete.
        condition &= (Q(_approved(document_type)) | ~applies) if applies else Q(_approved(document_type))
    return condition


def fully_compliant_count(filters: ReportFilters, types: list[DocumentType] | None = None) -> int:
    """N15: period activities where every active required document type has an Approved document."""
    types = required_document_types() if types is None else types
    activities = period_activities(filters)
    if not types:
        return activities.count()
    return activities.aggregate(n=Count("pk", filter=_all_approved(types)))["n"]


def document_compliance(activities: QuerySet, now: datetime | None = None, types: list[DocumentType] | None = None) -> dict:
    """N14: for each activity and each active required type that applies to it (documents.services.type_applies,
    ASSUMPTIONS 425): due date, state and overdue flag."""
    today = today_in_manila(now)
    types = required_document_types() if types is None else types
    rows = []
    annotations = {}
    for t in types:
        annotations[f"approved_{t.pk}"] = _approved(t)
        annotations[f"uploaded_{t.pk}"] = _uploaded(t)
        annotations[f"overdue_{t.pk}"] = ExpressionWrapper(_overdue(t, today), output_field=BooleanField())
    for activity in activities.annotate(**annotations).order_by("end_date", "start_date", "title", "pk"):
        items = []
        for document_type in types:
            if not type_applies(document_type, activity):
                continue
            pk = document_type.pk
            approved, uploaded = getattr(activity, f"approved_{pk}"), getattr(activity, f"uploaded_{pk}")
            items.append({
                "document_type": document_type.name,
                "document_type_id": pk,
                "due_date": due_date(document_type, activity),
                "state": APPROVED if approved else FOR_REVIEW if uploaded else MISSING,
                "is_overdue": getattr(activity, f"overdue_{pk}"),
            })
        rows.append({
            "activity_id": activity.pk,
            "title": activity.title,
            "end_date": activity.end_date,
            "items": items,
            "complete": all(item["state"] == APPROVED for item in items),
        })
    return {"today": today, "document_types": [t.name for t in types], "activities": rows}


# --- "Now" numbers (snapshot; ignore the date range, respect the activity type) -----------------


def activities_by_status(filters: ReportFilters) -> list[dict]:
    """S1: every activity (Draft included) with start_date in the range, by current status."""
    queryset = _of_type(Activity.objects.filter(start_date__gte=filters.start, start_date__lte=filters.end), filters)
    counts = dict(queryset.order_by().values("status__name").annotate(n=Count("pk")).values_list("status__name", "n"))
    return [{"status": name, "count": counts.get(name, 0)} for name in STATUS_ORDER]


def upcoming_activities(filters: ReportFilters, now: datetime | None = None) -> dict:
    """S2: Scheduled activities starting today or later (Manila), with the next few (title and dates).
    `within_30_days`: those of them starting no later than today + UPCOMING_WINDOW_DAYS."""
    today = today_in_manila(now)
    queryset = _of_type(
        Activity.objects.filter(status__name=ActivityStatus.SCHEDULED, start_date__gte=today), filters
    ).order_by("start_date", "title", "pk")
    counts = queryset.aggregate(
        count=Count("pk"),
        within_30_days=Count("pk", filter=Q(start_date__lte=today + timedelta(days=UPCOMING_WINDOW_DAYS))),
    )
    return {
        **counts,
        "next": list(queryset.values("activity_id", "title", "start_date", "end_date")[:UPCOMING_LIMIT]),
    }


def documents_now(filters: ReportFilters, now: datetime | None = None, types: list[DocumentType] | None = None) -> dict:
    """S3: overdue (activity, required type) pairs in non-Draft activities (the reminder rule);
    S4: activity documents waiting for a reviewer, and returned to the uploader."""
    types = required_document_types() if types is None else types
    overdue = 0
    if types:
        activities = _of_type(Activity.objects.exclude(status__name=ActivityStatus.DRAFT), filters)
        today = today_in_manila(now)
        counts = activities.aggregate(**{f"overdue_{t.pk}": Count("pk", filter=_overdue(t, today)) for t in types})
        overdue = sum(counts.values())
    review = _of_type(Document.objects.activity_documents(), filters, "activity__activity_type_id").aggregate(
        waiting=Count("pk", filter=Q(status__status_name=DocumentStatus.FOR_REVIEW, reviewed_at__isnull=True)),
        returned=Count("pk", filter=Q(status__status_name=DocumentStatus.FOR_REVIEW, reviewed_at__isnull=False)),
    )
    return {"overdue": overdue, "waiting_for_review": review["waiting"], "returned": review["returned"]}


def document_attention(filters: ReportFilters, now: datetime | None = None, types: list[DocumentType] | None = None) -> dict:
    """The dashboard's "Needs attention" document rows, as (activity, required type) pairs in non-Draft
    activities of the type filter:
    - "overdue": exactly the pairs S3 counts (past the due date, nothing Approved);
    - "due_soon": the "due soon" reminder rule (documents.services._reminder_kind): nothing uploaded
      and due within DOCUMENT_REMINDER_DAYS_BEFORE days (the due date itself included).
    Each row: activity_id, title, document_type, due_date, state (Missing / For Review). Oldest due first.
    One query per active required type."""
    types = required_document_types() if types is None else types
    today = today_in_manila(now)
    days_before = settings.DOCUMENT_REMINDER_DAYS_BEFORE
    activities = _of_type(Activity.objects.exclude(status__name=ActivityStatus.DRAFT), filters)
    overdue, due_soon = [], []
    for document_type in types:
        reference, offset = document_type.due_reference, document_type.due_offset_days
        # 0 <= (reference + offset) - today <= days_before, written on the plain reference date.
        soon = Q(**{
            f"{reference}__gte": today - timedelta(days=offset),
            f"{reference}__lte": today + timedelta(days=days_before - offset),
        }) & ~Q(_uploaded(document_type)) & _applies(document_type) & _counts_now(document_type)
        late = _overdue(document_type, today)
        rows = (
            activities.filter(late | soon)
            .annotate(is_overdue=ExpressionWrapper(late, output_field=BooleanField()), uploaded=_uploaded(document_type))
            .values("activity_id", "title", "start_date", "end_date", "is_overdue", "uploaded")
        )
        for row in rows:
            item = {
                "activity_id": row["activity_id"],
                "title": row["title"],
                "document_type": document_type.name,
                "document_type_id": document_type.pk,
                "due_date": row[reference] + timedelta(days=offset),
                "state": FOR_REVIEW if row["uploaded"] else MISSING,
            }
            (overdue if row["is_overdue"] else due_soon).append(item)
    order = lambda item: (item["due_date"], item["title"], item["document_type"])  # noqa: E731
    return {"today": today, "overdue": sorted(overdue, key=order), "due_soon": sorted(due_soon, key=order)}


def certificates_now(filters: ReportFilters) -> dict:
    """S5: ACTIVITIES that need certificate approval (certificates.services.needs_approval_q: a certificate waiting for
    approval and the activity not approved; ASSUMPTIONS 369), counted distinct (waiting_activities), with the number
    of those certificates (waiting_certificates); S6: Approved but not Issued yet. One query on CERTIFICATE (the only
    join is the activity, one to one, so nothing multiplies)."""
    from certificates.services import needs_approval_q

    status = Certificate.Status
    waiting = needs_approval_q()
    return _of_type(Certificate.objects.all(), filters, "activity__activity_type_id").aggregate(
        waiting_activities=Count("activity", filter=waiting, distinct=True),
        waiting_certificates=Count("pk", filter=waiting),
        approved_not_issued=Count("pk", filter=Q(status=status.APPROVED)),
    )


def certificates_waiting_by_activity(filters: ReportFilters, limit: int = 3) -> list[dict]:
    """The activities of S5 (they need certificate approval), most certificates first:
    activity_id, title, count. Respects the activity type filter, like S5."""
    from certificates.services import needs_approval_q

    rows = (
        _of_type(Certificate.objects.filter(needs_approval_q()), filters, "activity__activity_type_id")
        .order_by()
        .values("activity_id", "activity__title")
        .annotate(count=Count("pk"))
        .order_by("-count", "activity__title", "activity_id")[:limit]
    )
    return [{"activity_id": r["activity_id"], "title": r["activity__title"], "count": r["count"]} for r in rows]


# --- The dashboard ---------------------------------------------------------------------------------


def _card(key: str, label: str, scope: str, value, **detail) -> dict:
    return {"key": key, "label": label, "scope": scope, "value": value, **detail}


def dashboard(filters: ReportFilters, now: datetime | None = None) -> dict:
    """Cards and chart data for the dashboard. Each card says its scope: "period" (the date range)
    or "now" (a snapshot of today that ignores the range but respects the activity type)."""
    types = required_document_types()
    summary = period_summary(filters, now, types)
    statuses = activities_by_status(filters)
    upcoming = upcoming_activities(filters, now)
    documents = documents_now(filters, now, types)
    certificates = certificates_now(filters)
    evaluation = evaluation_results(filters)
    cards = [
        _card("activities_by_status", "Activities by status (start date in the range, Draft included)", PERIOD,
              sum(row["count"] for row in statuses), by_status=statuses),
        _card("upcoming_activities", "Upcoming activities (Scheduled, starting today or later)", NOW,
              upcoming["count"], within_30_days=upcoming["within_30_days"], next=upcoming["next"]),
        _card("activities_conducted", "Activities conducted", PERIOD, summary["activities_conducted"]),
        _card("registrations", "Registered participants", PERIOD, summary["registered"],
              attended=summary["attended"], attendance_rate=summary["attendance_rate"],
              unique_attendees=summary["unique_attendees"]),
        _card("evaluations", "Evaluations submitted", PERIOD, summary["evaluations"],
              response_rate=summary["evaluation_response_rate"], average_rating=summary["average_rating"],
              rating_answers=summary["rating_answers"]),
        _card("documents_complete", "Conducted activities with all required documents approved", PERIOD,
              summary["fully_compliant_activities"], out_of=summary["activities_conducted"]),
        _card("documents_overdue", "Required documents overdue", NOW, documents["overdue"]),
        _card("documents_waiting_for_review", "Documents waiting for a reviewer", NOW, documents["waiting_for_review"],
              returned=documents["returned"]),
        _card("certificates_waiting_for_approval", "Activities that need certificate approval", NOW,
              certificates["waiting_activities"], certificates=certificates["waiting_certificates"],
              approved_not_issued=certificates["approved_not_issued"]),
        _card("certificates_issued", "Certificates issued", PERIOD, summary["certificates_issued"],
              participation=summary["certificates_issued_participation"],
              appreciation=summary["certificates_issued_appreciation"], emailed=summary["certificates_emailed"]),
        _card("startups_engaged", "Startups engaged (attended at least one activity)", PERIOD, summary["startups_engaged"]),
    ]
    return {
        "today": today_in_manila(now),
        "cards": cards,
        "charts": {
            "per_month": [{**row, "month": f"{row['month']:%Y-%m}"} for row in per_month(filters)],
            "by_type": by_type(filters),
            "by_category": by_category(filters),
            "participant_mix": period_participant_mix(filters),
            "average_rating_by_question": evaluation["questions"],
        },
        "notes": [
            'Cards with scope "period" count Conducted activities whose end date is in the range '
            "(\"Activities by status\" uses the start date and includes every status).",
            'Cards with scope "now" show today\'s situation: they ignore the date range but respect the activity type.',
            CATEGORY_NOTE,
        ],
    }
