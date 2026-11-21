"""Read-only numbers added for the dashboard page (Phase F1): S2 within_30_days, the "Needs attention"
document rows (same rules as S3 and the "due soon" reminders) and the sign-off list by activity."""

from datetime import timedelta

import pytest
from django.utils import timezone

from activities.models import Activity, ActivityStatus, ActivityType
from certificates.models import Certificate
from documents.models import DocumentStatus, DocumentType
from documents.services import send_document_reminders
from notifications.models import Notification
from reports import metrics

from .conftest import TEST_NOW, TODAY, YEAR_2026

pytestmark = pytest.mark.django_db


# --- S2: within_30_days ------------------------------------------------------------------------------


def test_within_30_days_counts_scheduled_activities_starting_in_the_next_30_days(make_activity) -> None:
    make_activity("Today", ActivityStatus.SCHEDULED, start=TODAY)
    make_activity("Day 30", ActivityStatus.SCHEDULED, start=TODAY + timedelta(days=30))
    make_activity("Day 31", ActivityStatus.SCHEDULED, start=TODAY + timedelta(days=31))
    make_activity("Draft soon", ActivityStatus.DRAFT, start=TODAY + timedelta(days=5))
    make_activity("Started yesterday", ActivityStatus.SCHEDULED, start=TODAY - timedelta(days=1))
    upcoming = metrics.upcoming_activities(YEAR_2026, TEST_NOW)
    assert upcoming["count"] == 3  # S2 itself is unchanged: every Scheduled activity from today
    assert upcoming["within_30_days"] == 2


def test_within_30_days_respects_the_activity_type(make_activity) -> None:
    make_activity("Workshop", ActivityStatus.SCHEDULED, start=TODAY + timedelta(days=3))
    make_activity("Seminar", ActivityStatus.SCHEDULED, start=TODAY + timedelta(days=3), type_name="Seminar")
    seminar = ActivityType.objects.get(name="Seminar")
    filters = metrics.ReportFilters(YEAR_2026.start, YEAR_2026.end, seminar.pk)
    assert metrics.upcoming_activities(filters, TEST_NOW)["within_30_days"] == 1


def test_the_dashboard_card_carries_within_30_days(make_activity) -> None:
    make_activity("Soon", ActivityStatus.SCHEDULED, start=TODAY + timedelta(days=10))
    make_activity("Later", ActivityStatus.SCHEDULED, start=TODAY + timedelta(days=60))
    card = next(c for c in metrics.dashboard(YEAR_2026, TEST_NOW)["cards"] if c["key"] == "upcoming_activities")
    assert (card["value"], card["within_30_days"]) == (2, 1)


# --- Needs attention: overdue and due soon --------------------------------------------------------


@pytest.fixture
def document_cases(make_activity, add_document):
    """Activities around TEST_NOW (Oct 23, 2026) with the seeded due rules: Activity Design and Request Letter
    start-5 (NAVIGATU's 5-day rule, ASSUMPTIONS 416), Attendance Sheet end+0 (the Invitation Letter and the
    Accomplishment Report are optional, ASSUMPTIONS 386: never due soon or overdue)."""
    soon = make_activity("Starts Oct 31", ActivityStatus.SCHEDULED, start=TODAY + timedelta(days=8))
    # Activity Design and Request Letter due Oct 26 (due soon, the last day of the window), Attendance later (nothing).
    uploaded = make_activity("Uploaded already", ActivityStatus.SCHEDULED, start=TODAY + timedelta(days=8))
    add_document(uploaded, "Invitation Letter", DocumentStatus.FOR_REVIEW)  # optional: never counted
    add_document(uploaded, "Activity Design", DocumentStatus.FOR_REVIEW)  # uploaded: never "due soon"
    # Starts Oct 27: both due Oct 22 (overdue); an uploaded but not Approved Activity Design is still overdue.
    late_design = make_activity("Starts Oct 27", ActivityStatus.SCHEDULED, start=TODAY + timedelta(days=4))
    add_document(late_design, "Activity Design", DocumentStatus.FOR_REVIEW)
    make_activity("Draft", ActivityStatus.DRAFT, start=TODAY + timedelta(days=8))  # Drafts never count
    ongoing = make_activity("Ends today", ActivityStatus.ONGOING, start=TODAY - timedelta(days=1), end=TODAY)
    add_document(ongoing, "Invitation Letter")
    add_document(ongoing, "Activity Design")
    add_document(ongoing, "Request Letter")
    # Attendance Sheet due today (due soon, 0 days); the Accomplishment Report is optional (nothing)
    late = make_activity("Ended Oct 20", ActivityStatus.CONDUCTED, start=TODAY - timedelta(days=3))
    return {"soon": soon, "uploaded": uploaded, "late_design": late_design, "ongoing": ongoing, "late": late}


def pairs(rows: list[dict]) -> set[tuple[int, str]]:
    return {(row["activity_id"], row["document_type"]) for row in rows}


def test_due_soon_and_overdue_rows(document_cases) -> None:
    rows = metrics.document_attention(YEAR_2026, TEST_NOW)
    soon, uploaded, ongoing = document_cases["soon"].pk, document_cases["uploaded"].pk, document_cases["ongoing"].pk
    late_design = document_cases["late_design"].pk
    assert pairs(rows["due_soon"]) == {
        (soon, "Activity Design"), (soon, "Request Letter"), (uploaded, "Request Letter"), (ongoing, "Attendance Sheet"),
    }
    assert not any(row["document_type"] == "Invitation Letter" for row in rows["due_soon"] + rows["overdue"])
    overdue = {(row["activity_id"], row["document_type"]): row for row in rows["overdue"]}
    assert not any(activity_id in (soon, uploaded) for activity_id, _ in overdue)  # due Oct 26: not yet
    assert overdue[(late_design, "Request Letter")]["state"] == metrics.MISSING
    assert overdue[(late_design, "Activity Design")]["state"] == metrics.FOR_REVIEW
    assert all(row["due_date"] < TODAY for row in rows["overdue"])
    assert [row["due_date"] for row in rows["overdue"]] == sorted(row["due_date"] for row in rows["overdue"])


def test_overdue_rows_are_exactly_what_s3_counts(document_cases) -> None:
    rows = metrics.document_attention(YEAR_2026, TEST_NOW)
    assert len(rows["overdue"]) == metrics.documents_now(YEAR_2026, TEST_NOW)["overdue"]


def test_rows_match_what_the_reminders_consider_due_soon_and_overdue(document_cases, settings) -> None:
    """The dashboard and send_document_reminders use the same rule (documents.services._reminder_kind)."""
    settings.DOCUMENT_REMINDER_DAYS_BEFORE = 3
    rows = metrics.document_attention(YEAR_2026, TEST_NOW)
    send_document_reminders(now=TEST_NOW)

    def reminded(kind: str) -> set[tuple[int, str]]:
        return set(Notification.objects.filter(type=kind).values_list("activity_id", "document_type__name"))

    assert pairs(rows["due_soon"]) == reminded(Notification.DOCUMENT_DUE_SOON)
    assert pairs(rows["overdue"]) == reminded(Notification.DOCUMENT_OVERDUE)


def test_the_due_soon_window_comes_from_the_setting(document_cases, settings) -> None:
    soon = document_cases["soon"].pk
    settings.DOCUMENT_REMINDER_DAYS_BEFORE = 3
    assert (soon, "Request Letter") in pairs(metrics.document_attention(YEAR_2026, TEST_NOW)["due_soon"])  # in 3 days
    settings.DOCUMENT_REMINDER_DAYS_BEFORE = 2
    assert (soon, "Request Letter") not in pairs(metrics.document_attention(YEAR_2026, TEST_NOW)["due_soon"])


def test_rows_respect_the_activity_type(document_cases, make_activity) -> None:
    seminar = make_activity("Seminar", ActivityStatus.SCHEDULED, start=TODAY + timedelta(days=8), type_name="Seminar")
    filters = metrics.ReportFilters(YEAR_2026.start, YEAR_2026.end, ActivityType.objects.get(name="Seminar").pk)
    rows = metrics.document_attention(filters, TEST_NOW)
    assert {row["activity_id"] for row in rows["due_soon"] + rows["overdue"]} == {seminar.pk}


def test_inactive_required_types_are_left_out(document_cases) -> None:
    DocumentType.objects.filter(name="Activity Design").update(is_active=False)
    rows = metrics.document_attention(YEAR_2026, TEST_NOW)
    assert "Activity Design" not in {row["document_type"] for row in rows["overdue"] + rows["due_soon"]}


# --- Certificates waiting for the Director's approval, by activity (ASSUMPTIONS 357) ----------------------


def test_certificates_waiting_by_activity(make_activity, register, make_certificate_row, evaluate) -> None:
    """Certificates waiting while their ACTIVITY is not approved (ASSUMPTIONS 369), evaluated or not (U); an approved
    activity is not counted."""
    big = make_activity("Big")
    small = make_activity("Small")
    for i in range(3):
        registration = register(big, f"B{i}")
        make_certificate_row(activity=big, registration=registration, status=Certificate.Status.GENERATED)
        evaluate(registration, {})
    registration = register(small, "S")
    make_certificate_row(activity=small, registration=registration, status=Certificate.Status.GENERATED)
    evaluate(registration, {})
    make_certificate_row(activity=small, registration=register(small, "U"), status=Certificate.Status.GENERATED)  # counted
    make_certificate_row(activity=small, registration=register(small, "T"), status=Certificate.Status.APPROVED)  # not S5
    approved = make_activity("Approved")
    make_certificate_row(activity=approved, registration=register(approved, "A"), status=Certificate.Status.GENERATED)
    Activity.objects.filter(pk=approved.pk).update(certificates_approved_at=timezone.now())  # approved: not a task
    rows = metrics.certificates_waiting_by_activity(YEAR_2026)
    assert [(row["title"], row["count"]) for row in rows] == [("Big", 3), ("Small", 2)]
    now = metrics.certificates_now(YEAR_2026)
    assert sum(row["count"] for row in rows) == now["waiting_certificates"] == 5
    assert len(rows) == now["waiting_activities"] == 2
