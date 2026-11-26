"""One test (or more) per number definition in reports.metrics (N1-N18, S1-S6; docs/ASSUMPTIONS.md, Step 10)."""

from datetime import UTC, date, datetime

import pytest
from django.core.exceptions import ValidationError
from django.utils import timezone

from activities.models import Activity, ActivityStatus, ActivityType, ResourcePerson
from certificates.models import Certificate, CertificateSignatory
from documents.models import DocumentStatus
from reports import metrics
from reports.metrics import ReportFilters

from .conftest import TEST_NOW, YEAR_2026, manila

pytestmark = pytest.mark.django_db

CONDUCTED, DRAFT, SCHEDULED, ONGOING = (
    ActivityStatus.CONDUCTED, ActivityStatus.DRAFT, ActivityStatus.SCHEDULED, ActivityStatus.ONGOING
)


def summary(filters: ReportFilters = YEAR_2026) -> dict:
    return metrics.period_summary(filters, TEST_NOW)


# --- Filters -------------------------------------------------------------------------------------


def test_default_filters_are_the_current_manila_year() -> None:
    # 2026-12-31 16:30 UTC is already January 1, 2027 in Manila.
    new_year_in_manila = timezone.make_aware(datetime(2026, 12, 31, 16, 30), UTC)
    assert metrics.default_filters(new_year_in_manila) == ReportFilters(date(2027, 1, 1), date(2027, 12, 31))
    assert metrics.parse_filters({}, TEST_NOW) == YEAR_2026


def test_parse_filters_checks_dates_and_activity_type(roles) -> None:
    workshop = ActivityType.objects.get(name="Workshop")
    filters = metrics.parse_filters({"start": "2026-03-01", "end": "2026-03-31", "activity_type": str(workshop.pk)})
    assert filters == ReportFilters(date(2026, 3, 1), date(2026, 3, 31), workshop.pk)
    with pytest.raises(ValidationError) as error:
        metrics.parse_filters({"start": "03/01/2026", "end": "2026-02-01", "activity_type": "x"})
    assert set(error.value.message_dict) == {"start", "activity_type"}
    with pytest.raises(ValidationError) as error:
        metrics.parse_filters({"start": "2026-05-01", "end": "2026-04-30"})
    assert "end" in error.value.message_dict
    with pytest.raises(ValidationError):
        metrics.parse_filters({"activity_type": "99999"})


# --- N1: which activities count ---------------------------------------------------------------------


def test_n1_counts_only_conducted_activities_by_end_date(make_activity) -> None:
    make_activity("Conducted in March", CONDUCTED, date(2026, 3, 10))
    for status in (DRAFT, SCHEDULED, ONGOING):
        make_activity(f"{status} in March", status, date(2026, 3, 10))
    make_activity("Conducted last year", CONDUCTED, date(2025, 6, 1))
    assert summary()["activities_conducted"] == 1


def test_n1_year_edges_use_the_end_date(make_activity) -> None:
    make_activity("Ends Jan 1", CONDUCTED, date(2026, 1, 1))
    make_activity("Ends Dec 31", CONDUCTED, date(2026, 12, 31))
    make_activity("Dec 30 to Jan 2", CONDUCTED, date(2025, 12, 30), end=date(2026, 1, 2))  # counted in the year it ends
    make_activity("Dec 30, 2026 to Jan 2, 2027", CONDUCTED, date(2026, 12, 30), end=date(2027, 1, 2))
    make_activity("Ends Dec 31, 2025", CONDUCTED, date(2025, 12, 31))
    titles = set(metrics.period_activities(YEAR_2026).values_list("title", flat=True))
    assert titles == {"Ends Jan 1", "Ends Dec 31", "Dec 30 to Jan 2"}


def test_n1_activity_type_filter(make_activity) -> None:
    make_activity(type_name="Workshop")
    make_activity(type_name="Seminar")
    seminar = ActivityType.objects.get(name="Seminar")
    assert summary(ReportFilters(YEAR_2026.start, YEAR_2026.end, seminar.pk))["activities_conducted"] == 1


# --- N2 / N3 / N9: by type and by category --------------------------------------------------------


def test_n2_by_type_adds_up_to_the_total(make_activity) -> None:
    make_activity(type_name="Workshop")
    make_activity(type_name="Workshop")
    make_activity(type_name="Seminar")
    make_activity(type_name="Seminar", status=DRAFT)
    rows = {row["name"]: row["activities"] for row in metrics.by_type(YEAR_2026)}
    assert rows == {"Seminar": 1, "Workshop": 2}
    assert sum(rows.values()) == summary()["activities_conducted"]


def test_n3_multi_category_activity_counts_once_in_totals(make_activity, register) -> None:
    both = make_activity("For two", categories=("Faculty", "MSMEs"))
    make_activity("Faculty only", categories=("Faculty",))
    register(both, "Ana", attended=True, category="Faculty")
    register(both, "Ben", attended=False, category="MSMEs")

    assert summary()["activities_conducted"] == 2  # the two-category activity is counted once
    rows = {row["name"]: row for row in metrics.by_category(YEAR_2026)}
    assert rows["Faculty"]["activities"] == 2 and rows["MSMEs"]["activities"] == 1
    assert rows["Incubatees"]["activities"] == 0  # every category is listed, also with zero
    assert sum(row["activities"] for row in rows.values()) == 3  # more than the total: expected
    # N9: each registration has one category, so these add up to the totals.
    assert (rows["Faculty"]["registered"], rows["Faculty"]["attended"]) == (1, 1)
    assert (rows["MSMEs"]["registered"], rows["MSMEs"]["attended"]) == (1, 0)
    assert sum(row["registered"] for row in rows.values()) == summary()["registered"]


# --- N4: per month ------------------------------------------------------------------------------------


def test_n4_per_month_uses_the_end_date_and_lists_every_month(make_activity, register) -> None:
    march = make_activity(start=date(2026, 2, 27), end=date(2026, 3, 2))  # counts in March
    register(march, "Ana", attended=True)
    make_activity(start=date(2026, 3, 31))
    make_activity(start=date(2026, 3, 15), status=ONGOING)  # not conducted
    rows = metrics.per_month(ReportFilters(date(2026, 1, 15), date(2026, 4, 10)))
    assert [(r["label"], r["activities"], r["registered"], r["attended"]) for r in rows] == [
        ("Jan 2026", 0, 0, 0), ("Feb 2026", 0, 0, 0), ("Mar 2026", 2, 1, 1), ("Apr 2026", 0, 0, 0),
    ]


# --- N5-N8: registered vs attended -------------------------------------------------------------------


def test_n5_to_n8_registered_attended_rate_and_unique_attendees(make_activity, register) -> None:
    first, second = make_activity(), make_activity()
    register(first, "Ana", attended=True, email="ana@example.com")
    register(first, "Ben", attended=False)
    register(first, "Cara", attended=True)
    register(second, "Ana", attended=True, email="ANA@Example.com")  # same person: one unique attendee
    draft = make_activity(status=DRAFT)
    register(draft, "Dan", attended=True)  # not counted: Draft
    numbers = summary()
    assert (numbers["registered"], numbers["attended"], numbers["unique_attendees"]) == (4, 3, 2)
    assert numbers["attendance_rate"] == 0.75


def test_rates_are_none_when_nothing_to_divide_by(make_activity) -> None:
    make_activity()
    numbers = summary()
    assert numbers["attendance_rate"] is None and numbers["evaluation_response_rate"] is None
    assert numbers["average_rating"] is None


# --- N10-N13: evaluations and ratings --------------------------------------------------------------


def test_n10_n11_evaluations_and_response_rate(make_activity, register, evaluate) -> None:
    activity = make_activity()
    attended = [register(activity, name, attended=True) for name in ("Ana", "Ben", "Cara", "Dan")]
    evaluate(attended[0], {"Overall rating": 5})
    evaluate(attended[1], {"Overall rating": 3})
    numbers = summary()
    assert numbers["evaluations"] == 2 and numbers["evaluation_response_rate"] == 0.5


def test_n12_groups_rating_questions_by_label_ignoring_case_and_spaces(make_activity, register, evaluate) -> None:
    first, second = make_activity(), make_activity()
    evaluate(register(first, "Ana", attended=True), {"Overall rating": 5, "Venue": 4},
             text_answers={"Suggestions": "More coffee"})
    evaluate(register(first, "Ben", attended=True), {"Overall rating": 4, "Venue": 2})
    evaluate(register(second, "Cara", attended=True), {" overall RATING ": 3})
    not_conducted = make_activity(status=ONGOING)
    evaluate(register(not_conducted, "Dan", attended=True), {"Overall rating": 1})

    results = metrics.evaluation_results(YEAR_2026)
    questions = {q["question"]: q for q in results["questions"]}
    assert set(questions) == {"Overall rating", "Venue"}  # text questions are never averaged
    assert (questions["Overall rating"]["average"], questions["Overall rating"]["answers"]) == (4.0, 3)
    assert (questions["Venue"]["average"], questions["Venue"]["answers"]) == (3.0, 2)
    # N13: the mean of every rating answer.
    assert (results["average_rating"], results["rating_answers"]) == (3.6, 5)
    assert summary()["average_rating"] == 3.6


# --- N14/N15/S3/S4: documents ---------------------------------------------------------------------


def test_n15_fully_compliant_needs_every_required_type_approved(make_activity, add_document) -> None:
    complete, partial = make_activity("Complete"), make_activity("Partial")
    for name in ("Invitation Letter", "Activity Design", "Request Letter", "Attendance Sheet", "Pre-registration Form"):
        add_document(complete, name)
        add_document(partial, name, DocumentStatus.APPROVED if name != "Attendance Sheet" else DocumentStatus.FOR_REVIEW)
    assert summary()["fully_compliant_activities"] == 1
    # Optional types never decide it (ASSUMPTIONS 386): only the 3 required ones approved is fully compliant.
    only_required = make_activity("Only the required ones")
    for name in ("Activity Design", "Request Letter", "Attendance Sheet"):
        add_document(only_required, name)
    assert summary()["fully_compliant_activities"] == 2


def test_s3_overdue_now_ignores_the_range_and_skips_draft(make_activity, add_document) -> None:
    # Ended Oct 10, 2025: only the Attendance Sheet is missing (overdue); an optional type never counts.
    old = make_activity("Ended in 2025", start=date(2025, 10, 10))
    for name in ("Activity Design", "Request Letter"):
        add_document(old, name)
    make_activity("Draft", DRAFT, start=date(2026, 1, 10))  # never counted
    numbers = metrics.documents_now(YEAR_2026, TEST_NOW)
    assert numbers["overdue"] == 1  # the 2025 activity counts: "now" ignores the date range


def test_s3_due_date_itself_is_not_overdue_manila_midnight(make_activity) -> None:
    # Attendance Sheet is due end + 5 = Oct 23 and counts once Conducted (ASSUMPTIONS 425); the other types are due
    # before Oct 23.
    activity = make_activity(start=date(2026, 10, 18), status=CONDUCTED)
    late_evening = timezone.make_aware(datetime(2026, 10, 23, 23, 59))
    just_after_midnight = timezone.make_aware(datetime(2026, 10, 24, 0, 0))

    def attendance_sheet(now):
        (row,) = metrics.document_compliance(Activity.objects.filter(pk=activity.pk), now)["activities"]
        return next(item for item in row["items"] if item["document_type"] == "Attendance Sheet")

    assert attendance_sheet(late_evening)["is_overdue"] is False
    assert attendance_sheet(just_after_midnight)["is_overdue"] is True
    # Activity Design and Request Letter, due before Oct 23 (the Invitation Letter is optional, ASSUMPTIONS 386)
    assert metrics.documents_now(YEAR_2026, late_evening)["overdue"] == 2
    assert metrics.documents_now(YEAR_2026, just_after_midnight)["overdue"] == 3


def test_s4_waiting_and_returned_exclude_certificate_evidence(make_activity, register, add_document, make_certificate_row) -> None:
    activity = make_activity()
    add_document(activity, "Activity Design", DocumentStatus.FOR_REVIEW)
    add_document(activity, "Invitation Letter", DocumentStatus.FOR_REVIEW, returned=True)
    add_document(activity, "Request Letter", DocumentStatus.APPROVED)
    certificate = make_certificate_row(activity=activity, registration=register(activity, attended=True))
    add_document(activity, "Certificate", DocumentStatus.FOR_REVIEW, certificate=certificate)  # evidence: never listed
    numbers = metrics.documents_now(YEAR_2026, TEST_NOW)
    assert (numbers["waiting_for_review"], numbers["returned"]) == (1, 1)


# --- N16/N17/S5/S6: certificates ------------------------------------------------------------------


def test_n16_issued_counts_by_manila_date_of_issued_at(make_activity, register, make_certificate_row) -> None:
    activity = make_activity()
    people = [register(activity, f"P{i}", attended=True) for i in range(5)]
    make_certificate_row(activity=activity, registration=people[0], issued_at=manila(date(2026, 12, 31), 23, 59))  # in
    make_certificate_row(activity=activity, registration=people[1], issued_at=manila(date(2027, 1, 1), 0, 0))  # out
    make_certificate_row(activity=activity, registration=people[2], issued_at=manila(date(2026, 1, 1), 0, 0))  # in
    make_certificate_row(activity=activity, registration=people[3], issued_at=manila(date(2025, 12, 31), 23, 59))  # out
    make_certificate_row(activity=activity, registration=people[4], status=Certificate.Status.APPROVED)  # not issued
    person = ResourcePerson.objects.create(name="Dr. Speaker")
    make_certificate_row(activity=activity, resource_person=person, issued_at=manila(date(2026, 6, 1)))
    numbers = summary()
    assert (numbers["certificates_issued_participation"], numbers["certificates_issued_appreciation"]) == (2, 1)
    assert numbers["certificates_issued"] == 3


def test_n17_emailed_counts_by_manila_date_of_sent_at(make_activity, register, make_certificate_row) -> None:
    activity = make_activity()
    a, b = register(activity, "Ana", attended=True), register(activity, "Ben", attended=True)
    make_certificate_row(activity=activity, registration=a, issued_at=manila(date(2026, 12, 31), 20),
                         sent_at=manila(date(2026, 12, 31), 23, 30))
    make_certificate_row(activity=activity, registration=b, issued_at=manila(date(2026, 12, 31), 20),
                         sent_at=manila(date(2027, 1, 1), 0, 5))
    assert summary()["certificates_emailed"] == 1


def test_s5_s6_certificates_now(make_activity, register, make_certificate_row, evaluate) -> None:
    """S5 counts ACTIVITIES that need certificate approval (ASSUMPTIONS 369: a certificate waiting and the activity not
    approved, evaluated or not), plus those certificates; a join never multiplies them. An approved activity is not
    counted."""
    first = make_activity("First", start=date(2024, 5, 1))  # outside the range: "now" ignores it
    second = make_activity("Second", start=date(2024, 6, 1))
    statuses = [(first, Certificate.Status.GENERATED), (first, Certificate.Status.GENERATED),
                (first, Certificate.Status.APPROVED), (second, Certificate.Status.GENERATED),
                (second, Certificate.Status.ISSUED)]
    for number, (activity, status) in enumerate(statuses):
        registration = register(activity, f"P{number}", attended=True)
        make_certificate_row(activity=activity, registration=registration, status=status)
        evaluate(registration, {})
    unevaluated = register(second, "Not yet", attended=True)  # waiting for the evaluation: its activity still needs approval
    make_certificate_row(activity=second, registration=unevaluated, status=Certificate.Status.GENERATED)
    assert metrics.certificates_now(YEAR_2026) == {"waiting_activities": 2, "waiting_certificates": 4, "approved_not_issued": 1}
    Activity.objects.filter(pk=second.pk).update(certificates_approved_at=timezone.now())  # approved: not a staff task
    assert metrics.certificates_now(YEAR_2026)["waiting_activities"] == 1
    assert metrics.certificates_now(YEAR_2026)["waiting_certificates"] == 2


# --- S1 / S2 -------------------------------------------------------------------------------------------


def test_s1_by_status_uses_start_date_in_range_and_includes_draft(make_activity) -> None:
    make_activity(status=DRAFT, start=date(2026, 11, 20))
    make_activity(status=SCHEDULED, start=date(2026, 11, 25))
    make_activity(status=CONDUCTED, start=date(2026, 2, 1))
    make_activity(status=DRAFT, start=date(2027, 1, 5))  # outside the range
    make_activity(status=SCHEDULED, start=date(2026, 11, 26), type_name="Seminar")
    counts = {row["status"]: row["count"] for row in metrics.activities_by_status(YEAR_2026)}
    assert counts == {"Draft": 1, "Scheduled": 2, "Ongoing": 0, "Conducted": 1}
    workshop = ActivityType.objects.get(name="Workshop")
    filtered = metrics.activities_by_status(ReportFilters(YEAR_2026.start, YEAR_2026.end, workshop.pk))
    assert {row["status"]: row["count"] for row in filtered}["Scheduled"] == 1


def test_s2_upcoming_starts_today_or_later_in_manila(make_activity) -> None:
    make_activity("Starts Oct 23", SCHEDULED, start=date(2026, 10, 23))
    make_activity("Started Oct 22", SCHEDULED, start=date(2026, 10, 22))
    make_activity("Draft later", DRAFT, start=date(2026, 12, 1))
    make_activity("Next year", SCHEDULED, start=date(2027, 2, 1))  # "now": ignores the range
    # 16:30 UTC on Oct 22 is 00:30 on Oct 23 in Manila.
    just_after_midnight = timezone.make_aware(datetime(2026, 10, 22, 16, 30), UTC)
    upcoming = metrics.upcoming_activities(YEAR_2026, just_after_midnight)
    assert upcoming["count"] == 2
    assert [a["title"] for a in upcoming["next"]] == ["Starts Oct 23", "Next year"]


def test_s2_lists_at_most_five(make_activity) -> None:
    for day in range(1, 8):
        make_activity(status=SCHEDULED, start=date(2026, 11, day))
    upcoming = metrics.upcoming_activities(YEAR_2026, TEST_NOW)
    assert upcoming["count"] == 7 and len(upcoming["next"]) == metrics.UPCOMING_LIMIT


# --- Activity rows -------------------------------------------------------------------------------------


def test_activity_rows_have_their_own_numbers(make_activity, register, evaluate) -> None:
    activity = make_activity("Pitch", categories=("MSMEs", "Faculty"))
    evaluate(register(activity, "Ana", attended=True), {"Overall rating": 4, "Venue": 5})
    register(activity, "Ben")
    (row,) = metrics.activity_rows(YEAR_2026)
    assert row["title"] == "Pitch" and row["categories"] == "Faculty, MSMEs"
    assert (row["registered"], row["attended"], row["evaluations"], row["average_rating"]) == (2, 1, 1, 4.5)
    assert row["attendance_rate"] == 0.5
