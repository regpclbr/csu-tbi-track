"""A Draft moves to Scheduled by itself once both its Activity Design and Request Letter are uploaded (ASSUMPTIONS 431,
NAVIGATU owner decision 2026-11-24): the same transition rule (change_status / scheduling_refused_reason), History row
"Status: Draft -> Scheduled (both documents uploaded)" by System, the creator told in-app unless they uploaded it
themselves; a refused move never refuses the upload; registration still waits for the ready form (item 409)."""

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import Client
from django.urls import reverse

from activities import services
from activities.formatting import audit_sentence
from activities.models import ActivityAuditLog
from conftest import CREATION_PDF
from documents import services as document_services
from documents.models import DocumentType
from notifications.models import Notification
from registrations import services as registration_services

pytestmark = pytest.mark.django_db


def pdf(name: str = "a.pdf") -> SimpleUploadedFile:
    return SimpleUploadedFile(name, CREATION_PDF)


def doc_type(name: str) -> DocumentType:
    return DocumentType.objects.get(name=name)


@pytest.fixture
def draft(director, valid_data):
    """A Draft made on the New activity form with only its Activity Design."""
    rows = [document_services.NewDocument(doc_type("Activity Design"), pdf("d.pdf"), "Design")]
    activity = document_services.create_activity_with_documents(director, valid_data(title="Half ready"), rows)
    assert activity.status.name == "Draft"
    return activity


def last_status_row(activity) -> ActivityAuditLog:
    return ActivityAuditLog.objects.filter(activity=activity, field_changed="Status").order_by("-log_id").first()


def test_someone_else_uploads_the_last_one_and_the_creator_is_told(draft, director, make_member) -> None:
    staff = make_member("Admin Staff")
    document = document_services.upload_document(staff, draft, doc_type("Request Letter"), pdf(), "Letter")
    draft.refresh_from_db()
    assert document.scheduled_activity is True and draft.status.name == "Scheduled"
    row = last_status_row(draft)
    assert (row.old_value, row.new_value, row.changed_by_id) == ("Draft", "Scheduled (both documents uploaded)", None)
    assert audit_sentence(row) == ("System changed the status of Half ready from Draft to Scheduled (both documents "
                                   "uploaded)")
    [note] = Notification.objects.filter(type=Notification.ACTIVITY_SCHEDULED)
    assert note.member == director and note.activity == draft
    assert note.message.startswith("Half ready is now Scheduled: Test Admin Staff uploaded the last of its Activity Design")
    # Registration still waits for the ready form (item 409).
    assert registration_services.registration_closed_reason(draft) == registration_services.REGISTRATION_FORM_NOT_READY


def test_the_creator_uploading_it_is_not_told(draft, director) -> None:
    document_services.upload_document(director, draft, doc_type("Request Letter"), pdf(), "Letter")
    draft.refresh_from_db()
    assert draft.status.name == "Scheduled"
    assert not Notification.objects.filter(type=Notification.ACTIVITY_SCHEDULED).exists()


def test_other_documents_and_a_second_copy_change_nothing(draft, director) -> None:
    document_services.upload_document(director, draft, doc_type("Invitation Letter"), pdf(), "")
    document_services.upload_document(director, draft, doc_type("Activity Design"), pdf(), "Design v2")
    draft.refresh_from_db()
    assert draft.status.name == "Draft"


def test_old_drafts_are_never_moved(director, valid_data) -> None:
    """Activities without the creation rule (admin, demo seed, older rows) are left to staff."""
    activity = services.create_activity(director, valid_data())
    for name in ("Activity Design", "Request Letter"):
        document_services.upload_document(director, activity, doc_type(name), pdf(), name)
    activity.refresh_from_db()
    assert activity.status.name == "Draft"


def test_a_refused_move_keeps_the_upload_and_the_draft(draft, director, make_member, monkeypatch) -> None:
    monkeypatch.setitem(services.ALLOWED_STATUS_MOVES, "Draft", set())
    document = document_services.upload_document(make_member("Admin Staff"), draft, doc_type("Request Letter"), pdf(), "L")
    draft.refresh_from_db()
    assert document.pk and document.scheduled_activity is False and draft.status.name == "Draft"
    assert draft.documents.count() == 2
    assert not Notification.objects.filter(type=Notification.ACTIVITY_SCHEDULED).exists()


def test_the_documents_tab_says_it_is_now_scheduled(draft, director) -> None:
    client = Client()
    client.force_login(director)
    response = client.post(reverse("activities:document_upload", args=[draft.pk]),
                           {"document_type": doc_type("Request Letter").pk, "title": "Letter", "file": pdf()}, follow=True)
    draft.refresh_from_db()
    assert draft.status.name == "Scheduled"
    assert "the activity is now Scheduled" in response.content.decode()


def test_the_api_says_it_too(draft, director) -> None:
    client = Client()
    client.force_login(director)
    response = client.post(f"/documents/api/activities/{draft.pk}/upload/",
                           {"document_type": doc_type("Request Letter").pk, "file": pdf()})
    assert response.status_code == 201 and response.json()["activity_scheduled"] is True
