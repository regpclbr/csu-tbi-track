"""ASSUMPTIONS 437: unapprove a document of a type that needs review, rename a document, and the Director editing other
members' waiting / returned documents. Every change is in History (who, when) and documents.audit."""

import pytest
from django.core.exceptions import PermissionDenied, ValidationError
from django.test import Client
from django.urls import reverse

from activities.models import ActivityAuditLog
from documents import services
from documents.models import Document, DocumentType
from notifications.models import Notification

from .conftest import TEST_NOW, make_file

pytestmark = pytest.mark.django_db


def history(activity) -> list[tuple]:
    return list(ActivityAuditLog.objects.filter(activity=activity, field_changed="Documents").order_by("pk")
                .values_list("changed_by", "old_value", "new_value"))


@pytest.fixture
def approved_document(upload, view_only, director, scheduled) -> Document:
    document = upload(view_only, scheduled, title="Letter v1")  # the review flow is on in this module (conftest)
    return services.approve_document(director, document, now=TEST_NOW)


# --- Unapprove ---------------------------------------------------------------------------------------------------


def test_unapprove_returns_it_to_the_uploader_and_logs_it(approved_document, director, view_only) -> None:
    document = services.unapprove_document(director, approved_document, "Wrong date in the letter", now=TEST_NOW)
    document.refresh_from_db()
    assert document.review_state == Document.RETURNED and document.reviewed_by == director
    assert history(document.activity)[-1] == (
        director.pk, 'Request Letter "Letter v1" approved',
        'Request Letter "Letter v1" unapproved (returned to the uploader): Wrong date in the letter')
    note = Notification.objects.get(type=Notification.DOCUMENT_UNAPPROVED)
    assert note.member == view_only and "Wrong date in the letter" in note.message


def test_unapprove_makes_it_missing_in_the_checklist_and_removes_its_chunks(approved_document, director) -> None:
    from assistant.models import DocumentEmbedding

    DocumentEmbedding.objects.create(document=approved_document, chunk_index=0, chunk_text="x",
                                     embedding_vector=[0.0] * 768)
    services.unapprove_document(director, approved_document, "Redo", now=TEST_NOW)
    assert not DocumentEmbedding.objects.filter(document=approved_document).exists()
    item = next(i for i in services.document_checklist(approved_document.activity, now=TEST_NOW)["required"]
                if i.document_type == approved_document.document_type)
    assert item.state != services.APPROVED


def test_unapprove_needs_a_reason_and_the_flag(approved_document, director, head) -> None:
    with pytest.raises(ValidationError):
        services.unapprove_document(director, approved_document, "")
    with pytest.raises(PermissionDenied):
        services.unapprove_document(head, approved_document, "No right")
    assert Document.objects.get(pk=approved_document.pk).review_state == Document.APPROVED


def test_an_approved_copy_is_never_unapproved(upload, view_only, director, scheduled, doc_type) -> None:
    DocumentType.objects.filter(pk=doc_type.pk).update(needs_review=False)
    copy = upload(view_only, scheduled, document_type=DocumentType.objects.get(pk=doc_type.pk))
    assert services.is_approved_copy(copy)
    assert services.allowed_actions(director, copy)["unapprove"] is False
    with pytest.raises(services.DocumentError, match="approved copy"):
        services.unapprove_document(director, copy, "No")


def test_a_cited_document_is_not_unapproved(approved_document, director) -> None:
    from assistant.models import AIChat, AIChatMessage, AIMessageSource

    chat = AIChat.objects.create(member=director, title="Q")
    message = AIChatMessage.objects.create(chat=chat, role="assistant", content="A [1]")
    AIMessageSource.objects.create(message=message, document=approved_document, chunk_index=0)
    with pytest.raises(services.DocumentError, match="cites"):
        services.unapprove_document(director, approved_document, "No")


def test_the_unapprove_page_action(approved_document, director) -> None:
    client = Client()
    client.force_login(director)
    detail = client.get(reverse("documents:detail", args=[approved_document.pk]))
    assert reverse("documents:action_form", args=[approved_document.pk, "unapprove"]).encode() in detail.content
    client.post(reverse("documents:unapprove", args=[approved_document.pk]), {"reason": "Redo it"})
    assert Document.objects.get(pk=approved_document.pk).review_state == Document.RETURNED


# --- Rename ------------------------------------------------------------------------------------------------------


def test_the_director_renames_someone_elses_document(approved_document, director) -> None:
    services.rename_document(director, approved_document, "Request Letter (signed)")
    assert Document.objects.get(pk=approved_document.pk).title == "Request Letter (signed)"
    assert history(approved_document.activity)[-1] == (
        director.pk, 'Request Letter "Letter v1"', 'Request Letter "Request Letter (signed)" (renamed)')


def test_the_uploader_renames_and_others_cannot(approved_document, view_only, head) -> None:
    services.rename_document(view_only, approved_document, "Mine")
    with pytest.raises(PermissionDenied):
        services.rename_document(head, approved_document, "Not mine")
    assert Document.objects.get(pk=approved_document.pk).title == "Mine"


def test_rename_uses_the_title_rule(approved_document, director) -> None:
    with pytest.raises(ValidationError):
        services.rename_document(director, approved_document, "x" * 256)
    services.rename_document(director, approved_document, "  ")
    assert Document.objects.get(pk=approved_document.pk).title == "Request Letter"  # empty = the type's name


def test_the_rename_page_action(approved_document, director) -> None:
    client = Client()
    client.force_login(director)
    client.post(reverse("documents:rename", args=[approved_document.pk]), {"title": "Renamed here"})
    assert Document.objects.get(pk=approved_document.pk).title == "Renamed here"


# --- The Director edits other members' waiting documents ------------------------------------------------------------


def test_the_director_replaces_and_deletes_someone_elses_waiting_document(upload, view_only, director, scheduled) -> None:
    waiting = upload(view_only, scheduled)
    assert services.allowed_actions(director, waiting)["replace"]
    services.replace_document_file(director, waiting, make_file("New.pdf"), now=TEST_NOW)
    assert history(scheduled)[-1][0] == director.pk
    other = upload(view_only, scheduled, title="Second")
    services.delete_document(director, other)
    assert not Document.objects.filter(pk=other.pk).exists()
    assert history(scheduled)[-1] == (director.pk, 'Request Letter "Second"', 'Request Letter "Second" deleted')


def test_a_member_without_the_flag_still_cannot(upload, view_only, head, scheduled) -> None:
    waiting = upload(view_only, scheduled)
    with pytest.raises(PermissionDenied):
        services.replace_document_file(head, waiting, make_file(), now=TEST_NOW)
