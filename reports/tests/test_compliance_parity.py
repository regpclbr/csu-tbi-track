"""Decision D1: document compliance (N14/N15/S3) is computed in SQL for speed, with the same rules as
documents.services.document_checklist(). This test keeps the two from ever drifting apart."""

from datetime import date, datetime

import pytest
from django.utils import timezone

from activities.models import Activity, ActivityStatus
from documents.models import DocumentStatus, DocumentType
from documents.services import counts_for_follow_up, document_checklist
from reports import metrics

pytestmark = pytest.mark.django_db

APPROVED, FOR_REVIEW = DocumentStatus.APPROVED, DocumentStatus.FOR_REVIEW


@pytest.fixture
def varied(make_activity, register, add_document, make_certificate_row):
    """Activities in every status, with approved, waiting, returned, missing and evidence documents,
    an inactive required type and an optional type."""
    past = make_activity("Past, complete", start=date(2026, 5, 4))
    for name in ("Invitation Letter", "Activity Design", "Request Letter", "Attendance Sheet", "Pre-registration Form"):
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


# --- Activities created with their Activity Design and Request Letter (ASSUMPTIONS 405) ----------------------------


@pytest.fixture
def created_with_documents(make_activity, add_document):
    """Flagged activities (creation_documents_required) with the two types waiting, returned, approved or missing,
    next to an old activity with the same documents."""
    def flagged(title: str, status: str = ActivityStatus.SCHEDULED, start: date = date(2026, 10, 25)) -> Activity:
        activity = make_activity(title, status, start=start)
        Activity.objects.filter(pk=activity.pk).update(creation_documents_required=True)
        return activity

    waiting = flagged("New, waiting")
    add_document(waiting, "Activity Design", FOR_REVIEW)
    add_document(waiting, "Request Letter", FOR_REVIEW, returned=True)
    approved = flagged("New, approved", ActivityStatus.ONGOING, start=date(2026, 10, 20))
    add_document(approved, "Activity Design")
    add_document(approved, "Request Letter", FOR_REVIEW)
    flagged("New Draft, nothing", ActivityStatus.DRAFT)
    old = make_activity("Old, waiting", ActivityStatus.SCHEDULED, start=date(2026, 10, 25))
    add_document(old, "Activity Design", FOR_REVIEW)
    add_document(old, "Request Letter", FOR_REVIEW, returned=True)
    return Activity.objects.all()


@pytest.mark.parametrize("now", [
    timezone.make_aware(datetime(2026, 10, 10, 9, 0)),
    timezone.make_aware(datetime(2026, 10, 23, 9, 0)),
    timezone.make_aware(datetime(2026, 12, 31, 12, 0)),
])
def test_sql_matches_the_checklist_for_activities_created_with_documents(created_with_documents, now) -> None:
    sql = {row["activity_id"]: row for row in metrics.document_compliance(Activity.objects.all(), now)["activities"]}
    for activity in created_with_documents:
        expected = [
            (item.document_type.name, item.state, item.is_overdue)
            for item in sorted(document_checklist(activity, now)["required"], key=lambda item: item.document_type.name)
        ]
        assert [(i["document_type"], i["state"], i["is_overdue"]) for i in sql[activity.pk]["items"]] == expected, activity.title
    # ASSUMPTIONS 472: a Draft created with its creation documents counts its Activity Design and Request Letter.
    expected_overdue = sum(
        item.is_overdue
        for activity in created_with_documents
        for item in document_checklist(activity, now)["required"]
        if counts_for_follow_up(activity, item.document_type)
    )
    filters = metrics.ReportFilters(date(2026, 1, 1), date(2026, 12, 31))
    assert metrics.documents_now(filters, now)["overdue"] == expected_overdue
    attention_overdue = metrics.document_attention(filters, now)["overdue"]
    assert len(attention_overdue) == expected_overdue


def test_waiting_files_are_overdue_only_on_the_old_activity(created_with_documents) -> None:
    now = timezone.make_aware(datetime(2026, 10, 23, 9, 0))
    overdue = {(row["title"], row["document_type"]) for row in metrics.document_attention(
        metrics.ReportFilters(date(2026, 1, 1), date(2026, 12, 31)), now)["overdue"]}
    assert ("Old, waiting", "Activity Design") in overdue and ("Old, waiting", "Request Letter") in overdue
    assert not any(title in ("New, waiting", "New, approved") and name in ("Activity Design", "Request Letter")
                   for title, name in overdue)
    # ASSUMPTIONS 472: the flagged Draft with nothing uploaded now counts (due Oct 20, start - 5)
    assert ("New Draft, nothing", "Activity Design") in overdue and ("New Draft, nothing", "Request Letter") in overdue
