"""Decision D1: document compliance (N14/N15/S3) is computed in SQL for speed, with the same rules as
documents.services.document_checklist(). This test keeps the two from ever drifting apart."""

from datetime import date, datetime

import pytest
from django.utils import timezone

from activities.models import Activity, ActivityStatus
from documents.models import DocumentStatus, DocumentType
from documents.services import document_checklist
from reports import metrics

pytestmark = pytest.mark.django_db

APPROVED, FOR_REVIEW = DocumentStatus.APPROVED, DocumentStatus.FOR_REVIEW


@pytest.fixture
def varied(make_activity, register, add_document, make_certificate_row):
    """Activities in every status, with approved, waiting, returned, missing and evidence documents,
    an inactive required type and an optional type."""
    past = make_activity("Past, complete", start=date(2026, 5, 4))
    for name in ("Invitation Letter", "Activity Design", "Request Letter", "Attendance Sheet", "Accomplishment Report"):
        add_document(past, name)

    mixed = make_activity("Past, mixed", start=date(2026, 9, 1), end=date(2026, 9, 2))
    add_document(mixed, "Activity Design")
    add_document(mixed, "Activity Design", FOR_REVIEW)  # an approved one already completes the type
    add_document(mixed, "Invitation Letter", FOR_REVIEW)
    add_document(mixed, "Request Letter", FOR_REVIEW, returned=True)
    add_document(mixed, "Evaluation Form")  # optional type: never in the required list
    certificate = make_certificate_row(activity=mixed, registration=register(mixed, attended=True))
    add_document(mixed, "Attendance Sheet", APPROVED, certificate=certificate)  # evidence never counts

    on_due_date = make_activity("Due today", ActivityStatus.ONGOING, start=date(2026, 10, 16), end=date(2026, 10, 23))
    add_document(on_due_date, "Invitation Letter")
    make_activity("Upcoming", ActivityStatus.SCHEDULED, start=date(2026, 11, 3))
    make_activity("Draft", ActivityStatus.DRAFT, start=date(2026, 10, 25))

    DocumentType.objects.filter(name="Request Letter").update(is_active=False)  # inactive types are left out
    return Activity.objects.all()


@pytest.mark.parametrize("now", [
    timezone.make_aware(datetime(2026, 10, 23, 9, 0)),
    timezone.make_aware(datetime(2026, 10, 23, 23, 59)),
    timezone.make_aware(datetime(2026, 10, 24, 0, 0)),
    timezone.make_aware(datetime(2026, 12, 31, 12, 0)),
])
def test_sql_compliance_matches_the_checklist_for_every_activity(varied, now) -> None:
    sql = {row["activity_id"]: row for row in metrics.document_compliance(Activity.objects.all(), now)["activities"]}
    assert set(sql) == set(varied.values_list("pk", flat=True))
    for activity in varied:
        expected = [
            (item.document_type.name, item.due_date, item.state, item.is_overdue)
            for item in sorted(document_checklist(activity, now)["required"], key=lambda item: item.document_type.name)
        ]
        actual = [(i["document_type"], i["due_date"], i["state"], i["is_overdue"]) for i in sql[activity.pk]["items"]]
        assert actual == expected, activity.title


def test_overdue_count_matches_the_checklist_for_non_draft_activities(varied) -> None:
    now = timezone.make_aware(datetime(2026, 10, 23, 9, 0))
    expected = sum(
        item.is_overdue
        for activity in varied.exclude(status__name=ActivityStatus.DRAFT)
        for item in document_checklist(activity, now)["required"]
    )
    assert metrics.documents_now(metrics.ReportFilters(date(2026, 1, 1), date(2026, 12, 31)), now)["overdue"] == expected


def test_fully_compliant_matches_the_checklist(varied) -> None:
    now = timezone.make_aware(datetime(2026, 10, 23, 9, 0))
    filters = metrics.ReportFilters(date(2026, 1, 1), date(2026, 12, 31))
    expected = sum(
        all(item.state == "Approved" for item in document_checklist(activity, now)["required"])
        for activity in metrics.period_activities(filters)
    )
    assert metrics.fully_compliant_count(filters) == expected == 1
