"""ASSUMPTIONS 472 (owner): the documents to follow up in one Dashboard list (filters by activity and status), Drafts in
the overdue count, the colour key beside the checklist, and a document the Director returned or unapproved being
checked again after the uploader replaces it (also for a type that is normally an approved copy)."""

from datetime import date, datetime

import pytest
from django.test import Client
from django.urls import reverse

from activities.models import Activity, ActivityStatus
from documents import services
from documents.models import Document, DocumentType
from reports import dashboard_page, metrics

from .conftest import TEST_NOW, make_file

pytestmark = pytest.mark.django_db
APPROVED_COPIES = True  # the default of ASSUMPTIONS 432: uploads are approved copies
YEAR = metrics.ReportFilters(date(2026, 1, 1), date(2026, 12, 31))


def doc_type(name: str) -> DocumentType:
    return DocumentType.objects.get(name=name)


def flagged(activity: Activity) -> Activity:
    """Created with its creation documents (the New activity form), like every activity since 2026-11-19."""
    Activity.objects.filter(pk=activity.pk).update(creation_documents_required=True)
    activity.refresh_from_db()
    return activity


def pairs(rows) -> set[tuple[str, str, str]]:
    return {(row.activity.title, row.document_type.name, row.state) for row in rows}


# --- Drafts in the overdue count (S3 and the Dashboard's Needs attention) ------------------------------------------


def test_a_flagged_drafts_creation_documents_count_as_overdue(make_activity) -> None:
    """Start Oct 22: the Activity Design and Request Letter were due Oct 17 (start - 5); TEST_NOW is Oct 20."""
    draft = flagged(make_activity(ActivityStatus.DRAFT, start=date(2026, 10, 22), title="New draft"))
    make_activity(ActivityStatus.DRAFT, start=date(2026, 10, 22), title="Old draft")  # an old row: never counted
    assert metrics.documents_now(YEAR, TEST_NOW)["overdue"] == 2
    overdue = {(row["title"], row["document_type"]) for row in metrics.document_attention(YEAR, TEST_NOW)["overdue"]}
    assert overdue == {("New draft", "Activity Design"), ("New draft", "Request Letter")}
    services.upload_document(draft.created_by, draft, doc_type("Activity Design"), make_file(), "", now=TEST_NOW)
    assert metrics.documents_now(YEAR, TEST_NOW)["overdue"] == 1  # an uploaded one is never overdue (405)


def test_only_the_creation_types_of_a_draft_count(make_activity) -> None:
    flagged(make_activity(ActivityStatus.DRAFT, start=date(2026, 10, 1)))  # ended Oct 2: payment types past due too
    names = {row["document_type"] for row in metrics.document_attention(YEAR, TEST_NOW)["overdue"]}
    assert names == {"Activity Design", "Request Letter"}  # payment documents only after Conducted (425)


# --- One list of what to follow up ---------------------------------------------------------------------------------


@pytest.fixture
def some_activities(make_activity, director, head):
    late = make_activity(start=date(2026, 10, 22), title="Late workshop", created_by=head)  # due Oct 17: overdue
    later = make_activity(start=date(2026, 11, 20), title="Later seminar", created_by=head)
    services.upload_document(head, later, doc_type("Activity Design"), make_file(), "", now=TEST_NOW)
    DocumentType.objects.filter(name="Request Letter").update(needs_review=True)
    waiting = services.upload_document(head, later, doc_type("Request Letter"), make_file(), "Letter", now=TEST_NOW)
    draft = flagged(make_activity(ActivityStatus.DRAFT, start=date(2026, 12, 1), title="December draft"))
    make_activity(ActivityStatus.DRAFT, start=date(2026, 12, 1), title="Old draft")
    return {"late": late, "later": later, "waiting": waiting, "draft": draft}


def test_missing_waiting_and_returned_in_one_list_overdue_first(some_activities, director) -> None:
    rows = services.documents_to_follow_up(TEST_NOW)
    found = pairs(rows)
    assert ("Late workshop", "Activity Design", services.MISSING_STATE) in found
    assert ("Later seminar", "Request Letter", Document.WAITING_FOR_REVIEW) in found
    assert not any(title == "Later seminar" and name == "Activity Design" for title, name, _ in found)  # approved
    assert {name for title, name, _ in found if title == "December draft"} == {"Activity Design", "Request Letter"}
    assert not any(title == "Old draft" for title, _, _ in found)
    assert rows[0].is_overdue and rows[0].activity.title == "Late workshop"
    assert not any(row.document_type.name in ("Activity Report", "Attendance Sheet") for row in rows)  # not Conducted
    services.return_document(director, some_activities["waiting"], "Unsigned")
    assert ("Later seminar", "Request Letter", Document.RETURNED) in pairs(services.documents_to_follow_up(TEST_NOW))


def test_the_card_filters_by_activity_and_status(some_activities) -> None:
    later = some_activities["later"]
    card = dashboard_page.documents_card({"docs_activity": str(later.pk)}, now=TEST_NOW)
    assert {line["row"].activity.pk for line in card["lines"]} == {later.pk}
    card = dashboard_page.documents_card({"docs_state": Document.WAITING_FOR_REVIEW}, now=TEST_NOW)
    assert [(line["label"], line["action"]) for line in card["lines"]] == [("Waiting for review", "Open")]
    missing = dashboard_page.documents_card({"docs_state": services.MISSING_STATE}, now=TEST_NOW)
    assert missing["lines"] and all(line["action"] == "Upload" and "#upload" in line["url"] for line in missing["lines"])
    ignored = dashboard_page.documents_card({"docs_activity": "999999", "docs_state": "nope"}, now=TEST_NOW)
    assert ignored["chosen_activity"] is None and ignored["chosen_state"] == ""
    assert len(ignored["lines"]) == ignored["total"]


def test_the_card_reloads_alone_and_the_dashboard_shows_it(some_activities, head, clock) -> None:
    clock(datetime(2026, 10, 20, 9, 0))
    client = Client()
    client.force_login(head)
    url = reverse("reports:dashboard_documents")
    partial = client.get(url, {"docs_state": Document.WAITING_FOR_REVIEW}, HTTP_HX_REQUEST="true")
    assert partial.status_code == 200 and b"data-follow-up-card" in partial.content
    assert partial.content.count(b"data-follow-up-row=") == 1 and b'class="link">Later seminar' in partial.content
    assert b'class="link">Late workshop' not in partial.content  # still a choice in the Activity filter
    plain = client.get(url, {"docs_state": "missing"})
    assert plain.status_code == 302 and plain["Location"].endswith("docs_state=missing#documents-follow-up")
    page = client.get(reverse("home"), {"docs_state": "missing"})
    assert b"Documents to follow up" in page.content and b"data-state-key" in page.content


# --- The colour key ------------------------------------------------------------------------------------------------


def test_the_checklist_has_a_colour_key_with_the_badge_colours(scheduled, head) -> None:
    from documents import pages

    key = {state["label"]: state["tone"] for state in pages.state_key()}
    assert key == {"Missing": "neutral", "Waiting for review": "info", "Returned": "warning", "Approved": "success"}
    client = Client()
    client.force_login(head)
    page = client.get(reverse("activities:detail", args=[scheduled.pk]), {"tab": "documents"})
    assert b"Colour key" in page.content and b"badge badge-warning\">Returned" in page.content


# --- Returned / unapproved: upload or replace again, checked again ---------------------------------------------------


def test_a_new_upload_of_a_returned_type_waits_for_the_director(scheduled, head, director) -> None:
    copy = services.upload_document(head, scheduled, doc_type("Request Letter"), make_file(), "", now=TEST_NOW)
    assert services.is_approved_copy(copy)
    services.unapprove_document(director, copy, "Wrong date", now=TEST_NOW)
    assert services.in_review_again(scheduled, doc_type("Request Letter"))
    again = services.upload_document(head, scheduled, doc_type("Request Letter"), make_file("b.pdf"), "", now=TEST_NOW)
    assert again.review_state == Document.WAITING_FOR_REVIEW
    services.approve_document(director, again)
    services.delete_document(head, Document.objects.get(pk=copy.pk))  # the returned one is no longer needed
    third = services.upload_document(head, scheduled, doc_type("Request Letter"), make_file("c.pdf"), "", now=TEST_NOW)
    assert services.is_approved_copy(third)  # nothing returned any more: approved copies again


def test_a_waiting_replacement_cannot_skip_the_check_by_being_replaced_again(scheduled, head, director) -> None:
    copy = services.upload_document(head, scheduled, doc_type("Request Letter"), make_file(), "", now=TEST_NOW)
    services.unapprove_document(director, copy, "Wrong date", now=TEST_NOW)
    copy = services.replace_document_file(head, Document.objects.get(pk=copy.pk), make_file("b.pdf"))
    copy = services.replace_document_file(head, Document.objects.get(pk=copy.pk), make_file("c.pdf"))
    assert Document.objects.get(pk=copy.pk).review_state == Document.WAITING_FOR_REVIEW
