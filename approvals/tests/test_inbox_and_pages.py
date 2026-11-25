"""The "For your approval" inbox (ASSUMPTIONS 437): what each role sees, the filters, the counts, the pages (404 for
anyone who may not open a submission, files never from /media/), the sidebar badge and the Dashboard card."""

import pytest
from django.db import connection
from django.test import Client
from django.test.utils import CaptureQueriesContext
from django.urls import reverse

from approvals import inbox, services
from approvals.models import Submission
from documents import services as document_services
from documents.models import DocumentType

from .conftest import NOW, pdf_file

pytestmark = pytest.mark.django_db


def client_for(member) -> Client:
    client = Client()
    client.force_login(member)
    return client


@pytest.fixture
def waiting_document(roles, staff, activity):
    """A document of a type that needs review, uploaded by staff (waiting for the Director)."""
    letter = DocumentType.objects.get(name="Invitation Letter")
    DocumentType.objects.filter(pk=letter.pk).update(needs_review=True)
    letter.refresh_from_db()
    return document_services.upload_document(staff, activity, letter, pdf_file(), "Invitation", now=NOW)


@pytest.fixture
def prepared_activity(activity, director):
    """Certificates prepared before the activity (item 420): waiting for the Director's approval."""
    from activities.models import Activity

    Activity.objects.filter(pk=activity.pk).update(certificates_prepared_by=director, certificates_prepared_at=NOW)
    return Activity.objects.get(pk=activity.pk)


# --- What each role sees ---------------------------------------------------------------------------------------------


def test_the_director_sees_every_kind(submit, staff, director, waiting_document, prepared_activity) -> None:
    submit(staff, title="Budget request")
    kinds = sorted(item.kind for item in inbox.inbox(director))
    assert kinds == [inbox.CERTIFICATES, inbox.DOCUMENT, inbox.SUBMISSION]
    assert inbox.inbox_counts(director) == {inbox.CERTIFICATES: 1, inbox.SUBMISSION: 1, inbox.DOCUMENT: 1,
                                           inbox.ASSIGNMENT: 0, "total": 3}


def test_members_without_approval_flags_have_no_inbox(submit, staff, head, system_admin, view_only, waiting_document,
                                                      prepared_activity) -> None:
    submit(staff)
    for member in (staff, head, system_admin, view_only):
        assert inbox.inbox(member) == [] and not inbox.has_inbox(member)
        assert inbox.inbox_counts(member)["total"] == 0


def test_flags_decide_not_the_role_name(submit, staff, make_member, roles) -> None:
    from accounts.models import Role

    Role.objects.filter(role_name="Incubation Project Head").update(can_approve_documents=True)
    head = make_member("Incubation Project Head", email="head.approver@example.com")
    head.role.refresh_from_db()
    submit(staff)
    assert [item.kind for item in inbox.inbox(head)] == [inbox.SUBMISSION]


def test_own_submissions_are_never_in_your_inbox(submit, director, make_member) -> None:
    second = make_member("Director", email="second.director@example.com")
    submit(director, title="Mine")
    assert inbox.inbox(director) == []
    assert [item.title for item in inbox.inbox(second)] == ["Mine"]


def test_approved_and_returned_leave_the_inbox(submit, staff, director) -> None:
    services.approve_submission(director, submit(staff, title="A"))
    services.return_submission(director, submit(staff, title="B"), "Fix")
    assert inbox.inbox(director) == []


def test_filters(submit, staff, director, waiting_document, activity) -> None:
    submit(staff, title="Budget request", activity=activity)
    submit(staff, title="Letter to the dean")
    assert [i.title for i in inbox.inbox(director, {"kind": inbox.SUBMISSION, "q": "dean"})] == ["Letter to the dean"]
    assert {i.title for i in inbox.inbox(director, {"activity": activity.pk})} == {"Budget request", "Invitation"}
    assert [i.kind for i in inbox.inbox(director, {"kind": inbox.DOCUMENT})] == [inbox.DOCUMENT]


def test_the_inbox_query_count_does_not_grow(submit, staff, director, waiting_document) -> None:
    for number in range(2):
        submit(staff, title=f"S{number}")
    with CaptureQueriesContext(connection) as few:
        inbox.inbox(director)
    for number in range(6):
        submit(staff, title=f"More {number}")
    with CaptureQueriesContext(connection) as many:
        assert len(inbox.inbox(director)) == 9
    assert len(many) == len(few)


# --- Pages -----------------------------------------------------------------------------------------------------------


def test_the_inbox_page_and_one_click_open(submit, staff, director) -> None:
    submission = submit(staff, title="Budget request")
    page = client_for(director).get(reverse("approvals:inbox"))
    assert page.status_code == 200 and b"Budget request" in page.content
    assert reverse("approvals:detail", args=[submission.pk]).encode() in page.content
    partial = client_for(director).get(reverse("approvals:inbox"), {"kind": "document"}, HTTP_HX_REQUEST="true")
    assert b"Budget request" not in partial.content and b"<html" not in partial.content


def test_members_without_an_inbox_go_to_their_submissions(staff) -> None:
    response = client_for(staff).get(reverse("approvals:inbox"))
    assert response.status_code == 302 and response["Location"] == reverse("approvals:mine")
    assert client_for(staff).get(reverse("approvals:all")).status_code == 403


def test_a_submission_is_404_for_other_members(submit, staff, head, director) -> None:
    submission = submit(staff)
    for url in (reverse("approvals:detail", args=[submission.pk]),
                reverse("approvals:file", args=[submission.pk, "original"])):
        assert client_for(head).get(url).status_code == 404
        assert client_for(staff).get(url).status_code == 200
        assert client_for(director).get(url).status_code == 200
    download = client_for(staff).get(reverse("approvals:file", args=[submission.pk, "original"]))
    assert download["Content-Disposition"].startswith("attachment") and download["Cache-Control"] == "private, no-store"


def test_submit_page_and_approve_and_sign_through_the_pages(director, staff, director_signature) -> None:
    response = client_for(staff).post(reverse("approvals:submit"), {"title": "Letter", "file": pdf_file()})
    submission = Submission.objects.get()
    assert response.status_code == 302 and response["Location"] == reverse("approvals:detail", args=[submission.pk])
    page = client_for(director).get(reverse("approvals:detail", args=[submission.pk]))
    assert b"At the \xe2\x80\x9cNoted:\xe2\x80\x9d line (page 1)" in page.content and b"not a cryptographic" in page.content
    preview = client_for(director).get(reverse("approvals:sign_preview", args=[submission.pk]), {"placement": "noted"})
    assert preview["Content-Type"] == "application/pdf" and preview.content.startswith(b"%PDF")
    assert not Submission.objects.get().signed_file_path  # the preview is never stored
    client_for(director).post(reverse("approvals:approve", args=[submission.pk]), {"sign": "true", "placement": "noted"})
    submission.refresh_from_db()
    assert submission.status == Submission.Status.APPROVED and submission.signed_file_path
    signed = client_for(staff).get(reverse("approvals:file", args=[submission.pk, "signed"]), {"inline": "1"})
    assert signed["Content-Type"] == "application/pdf" and signed["Content-Disposition"].startswith("inline")


def test_submit_page_errors_stay_on_the_form(director, staff) -> None:
    response = client_for(staff).post(reverse("approvals:submit"), {"title": ""})
    assert response.status_code == 400 and b"Choose a file to upload" in response.content


def test_the_sidebar_badge_and_the_dashboard_card(submit, staff, director) -> None:
    submit(staff, title="Budget request")
    page = client_for(director).get(reverse("home"))
    assert b"items waiting for your approval" in page.content and b"data-approvals-card" in page.content
    assert b"Budget request" in page.content
    staff_page = client_for(staff).get(reverse("home"))
    assert b"data-approvals-card" not in staff_page.content
    assert reverse("approvals:inbox").encode() in staff_page.content  # everyone can submit: the item is there


def test_a_submission_notification_opens_the_submission(submit, staff, director) -> None:
    from notifications.models import Notification
    from notifications.pages import target_label, target_url

    submission = submit(staff)
    note = Notification.objects.get(member=director, type=Notification.SUBMISSION_WAITING)
    assert target_url(note) == reverse("approvals:detail", args=[submission.pk])
    assert target_label(note) == "Open submission"
