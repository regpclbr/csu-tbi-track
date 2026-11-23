"""Checklist: due dates (computed, Manila dates), state, overdue flag at the edges, optional types."""

from datetime import date, timedelta

import pytest

from documents import services
from documents.models import Document, DocumentType

from .conftest import TEST_NOW, at

pytestmark = pytest.mark.django_db

START, END = date(2026, 11, 10), date(2026, 11, 11)


@pytest.fixture
def activity(make_activity):
    return make_activity(start=START, end=END)


def by_name(checklist: dict) -> dict:
    return {item.document_type.name: item for item in checklist["required"]}


def test_due_dates_from_the_seeded_rules(activity) -> None:
    items = by_name(services.document_checklist(activity, now=TEST_NOW))
    assert items["Activity Design"].due_date == START - timedelta(days=5)  # NAVIGATU's 5-day rule (ASSUMPTIONS 416)
    assert items["Request Letter"].due_date == START - timedelta(days=5)
    assert "Invitation Letter" not in items and "Accomplishment Report" not in items  # optional (ASSUMPTIONS 386)
    assert items["Attendance Sheet"].due_date == END + timedelta(days=5)  # a payment document (ASSUMPTIONS 425)
    assert all(item.state == services.MISSING and not item.is_overdue for item in items.values())


def test_required_list_is_sorted_by_due_date(activity) -> None:
    dates = [item.due_date for item in services.document_checklist(activity, now=TEST_NOW)["required"]]
    assert dates == sorted(dates)


def test_overdue_flag_at_the_edges(activity) -> None:
    due = START - timedelta(days=5)  # Request Letter
    on_due_date = by_name(services.document_checklist(activity, now=at(due, 23)))["Request Letter"]
    assert on_due_date.is_overdue is False  # the due date itself is not overdue yet
    day_after = by_name(services.document_checklist(activity, now=at(due + timedelta(days=1), 0)))["Request Letter"]
    assert day_after.is_overdue is True


def test_today_is_the_manila_date(activity) -> None:
    from datetime import datetime, timezone as dt_timezone

    due = START - timedelta(days=5)
    # 4:30 PM UTC on the due date is already 12:30 AM the next day in Manila.
    utc = datetime(due.year, due.month, due.day, 16, 30, tzinfo=dt_timezone.utc)
    assert by_name(services.document_checklist(activity, now=utc))["Request Letter"].is_overdue is True


def test_states_and_completion(upload, director, head, activity) -> None:
    due_passed = at(START, 9)
    for_review = upload(director, activity)
    item = by_name(services.document_checklist(activity, now=due_passed))["Request Letter"]
    assert item.state == "For Review" and item.is_overdue is True  # not Approved -> still overdue
    assert item.documents == [for_review]

    services.approve_document(director, for_review)
    upload(director, activity)  # a newer For Review one does not undo completion
    item = by_name(services.document_checklist(activity, now=due_passed))["Request Letter"]
    assert item.state == "Approved" and item.is_overdue is False
    assert len(item.documents) == 2


def test_returned_document_counts_as_for_review(upload, director, head, activity) -> None:
    document = upload(head, activity)
    services.return_document(director, document, "Fix")
    assert by_name(services.document_checklist(activity, now=TEST_NOW))["Request Letter"].state == "For Review"


def test_other_activities_documents_do_not_count(upload, director, head, activity, make_activity) -> None:
    other = make_activity(title="Other")
    services.approve_document(director, upload(director, other))
    assert by_name(services.document_checklist(activity, now=TEST_NOW))["Request Letter"].state == "Missing"


def test_optional_types_are_listed_with_their_documents(upload, director, activity) -> None:
    form_type = DocumentType.objects.get(name="Evaluation Form")
    document = upload(director, activity, document_type=form_type)
    checklist = services.document_checklist(activity, now=TEST_NOW)
    optional = {entry["document_type"].name: entry["documents"] for entry in checklist["optional"]}
    # Activity Report / Event Photos: required only for activities ending from PAYMENT_DOCUMENTS_FROM (ASSUMPTIONS 425).
    assert set(optional) == {"Accomplishment Report", "Invitation Letter", "Pre-registration Form", "Evaluation Form",
                             "Certificate", "Other", "Activity Report", "Event Photos"}
    assert optional["Evaluation Form"] == [document]
    assert "Evaluation Form" not in by_name(checklist)


def test_inactive_types_are_left_out(activity) -> None:
    DocumentType.objects.filter(name__in=["Activity Design", "Certificate"]).update(is_active=False)
    checklist = services.document_checklist(activity, now=TEST_NOW)
    assert "Activity Design" not in by_name(checklist)
    assert "Certificate" not in {entry["document_type"].name for entry in checklist["optional"]}


def test_certificate_files_are_excluded(upload, director, head, activity, certificate_for) -> None:
    """Item 26: documents with related_certificate set never count in the checklist."""
    document = upload(director, activity)
    services.approve_document(director, document)
    Document.objects.filter(pk=document.pk).update(related_certificate=certificate_for(activity))
    assert by_name(services.document_checklist(activity, now=TEST_NOW))["Request Letter"].state == "Missing"


def test_due_dates_follow_activity_changes(activity) -> None:
    """Computed, never stored: moving the activity moves the due dates."""
    activity.start_date = START + timedelta(days=3)
    assert by_name(services.document_checklist(activity, now=TEST_NOW))["Request Letter"].due_date == START - timedelta(days=2)


