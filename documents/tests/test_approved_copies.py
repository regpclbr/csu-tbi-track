"""Uploads are approved copies (ASSUMPTIONS 432, NAVIGATU: only approved paper documents are uploaded; the system only
monitors what is missing). A type without DOCUMENT_TYPE.needs_review (every type by default) is saved as Approved by
System at upload, written to History and documents.audit, indexed at once, nobody is notified, no Approve / Return; its
uploader or the Director may replace or delete it (guards kept). A type with needs_review keeps the review flow;
certificate evidence keeps its own. "Complete" = Approved, the due rule and auto-Scheduled (431) are unchanged."""

import importlib
from datetime import date

import pytest
from django.apps import apps as django_apps
from django.core.exceptions import PermissionDenied
from django.test import Client
from django.urls import reverse
from django_q.models import OrmQ

from activities.models import ActivityAuditLog, ActivityStatus
from documents import services
from documents.models import Document, DocumentStatus, DocumentType
from notifications.models import Notification

from .conftest import TEST_NOW, make_file

pytestmark = pytest.mark.django_db
APPROVED_COPIES = True  # the default of ASSUMPTIONS 432 (no review flow fixture)


def doc_type(name: str) -> DocumentType:
    return DocumentType.objects.get(name=name)


def history(activity) -> list[str]:
    return list(ActivityAuditLog.objects.filter(activity=activity, field_changed="Documents").order_by("log_id")
                .values_list("new_value", flat=True))


@pytest.fixture
def activity(make_activity, head):
    return make_activity(created_by=head)


# --- Upload --------------------------------------------------------------------------------------------------------


def test_every_type_defaults_to_no_review(roles) -> None:
    assert DocumentType.objects.exists() and not DocumentType.objects.filter(needs_review=True).exists()


def test_an_upload_is_saved_as_an_approved_copy(activity, head, director, caplog,
                                               django_capture_on_commit_callbacks) -> None:
    with caplog.at_level("INFO", logger="documents.audit"), django_capture_on_commit_callbacks(execute=True):
        document = services.upload_document(head, activity, doc_type("Invitation Letter"), make_file(), "Invite",
                                            now=TEST_NOW)
    document.refresh_from_db()
    assert document.status.status_name == DocumentStatus.APPROVED and document.review_state == Document.APPROVED
    assert document.reviewed_by is None and document.reviewed_at == TEST_NOW  # System, at the upload time
    assert services.is_approved_copy(document)
    assert not Notification.objects.filter(type=Notification.DOCUMENT_WAITING_FOR_REVIEW).exists()  # nobody told
    assert history(activity) == ['Invitation Letter "Invite" uploaded as an approved copy']
    assert "uploaded as an approved copy" in caplog.text and "Invite" not in caplog.text  # IDs only
    assert OrmQ.objects.count() == 1  # indexed for the AI at upload


def test_no_approve_or_return_and_the_services_refuse(activity, head, director) -> None:
    document = services.upload_document(head, activity, doc_type("Invitation Letter"), make_file(), "")
    actions = services.allowed_actions(director, document)
    assert not actions["approve"] and not actions["return"] and actions["replace"] and actions["delete"]
    with pytest.raises(services.DocumentError, match="already approved"):
        services.approve_document(director, document)
    with pytest.raises(services.DocumentError, match="already approved"):
        services.return_document(director, document, "No")
    assert services.review_queue(director).count() == 0


def test_complete_due_rule_and_auto_scheduled_are_unchanged(make_activity, director, head) -> None:
    """Complete = Approved; overdue = after the due date and not uploaded; both creation documents -> Scheduled (431)."""
    activity = make_activity(status=ActivityStatus.SCHEDULED, start=date(2026, 10, 22), created_by=head)
    checklist = services.document_checklist(activity, now=TEST_NOW)
    design = next(item for item in checklist["required"] if item.document_type.name == "Activity Design")
    assert design.state == services.MISSING and design.is_overdue  # due Oct 17 (start - 5)
    services.upload_document(head, activity, doc_type("Activity Design"), make_file(), "Design", now=TEST_NOW)
    design = next(item for item in services.document_checklist(activity, now=TEST_NOW)["required"]
                  if item.document_type.name == "Activity Design")
    assert design.state == services.APPROVED and not design.is_overdue


# --- A type that needs review keeps today's flow -------------------------------------------------------------------


def test_needs_review_keeps_the_review_flow(activity, head, director) -> None:
    DocumentType.objects.filter(name="Invitation Letter").update(needs_review=True)
    document = services.upload_document(head, activity, doc_type("Invitation Letter"), make_file(), "")
    assert document.review_state == Document.WAITING_FOR_REVIEW and not services.is_approved_copy(document)
    assert Notification.objects.filter(type=Notification.DOCUMENT_WAITING_FOR_REVIEW, member=director).exists()
    assert services.allowed_actions(director, document)["approve"] and history(activity) == []
    services.approve_document(director, document)
    document.refresh_from_db()
    assert document.reviewed_by == director
    assert not services.allowed_actions(head, document)["replace"]  # a reviewed Approved document stays final
    with pytest.raises(services.DocumentError, match="cannot be deleted"):
        services.delete_document(head, document)


def test_certificate_evidence_keeps_its_own_flow(roles) -> None:
    assert services.needs_review(DocumentType(name="x"), related_certificate=object())


# --- Replace and delete an approved copy ----------------------------------------------------------------------------


def test_the_uploader_and_the_director_replace_and_delete(activity, head, director, make_member,
                                                         django_capture_on_commit_callbacks) -> None:
    other = make_member("Admin Staff")
    document = services.upload_document(head, activity, doc_type("Invitation Letter"), make_file(), "Invite")
    assert not services.allowed_actions(other, document)["replace"]
    with pytest.raises(PermissionDenied, match="uploaded this document or the Director"):
        services.replace_document_file(other, document, make_file("b.pdf"))
    OrmQ.objects.all().delete()
    with django_capture_on_commit_callbacks(execute=True):
        document = services.replace_document_file(director, document, make_file("b.pdf"))
    document.refresh_from_db()
    assert document.status.status_name == DocumentStatus.APPROVED and document.reviewed_by is None
    assert OrmQ.objects.count() == 1  # indexed again
    services.replace_document_file(head, document, make_file("c.pdf"))
    services.delete_document(director, document)
    assert not Document.objects.filter(pk=document.pk).exists()
    assert history(activity) == ['Invitation Letter "Invite" uploaded as an approved copy',
                                 'Invitation Letter "Invite" replaced (uploaded as an approved copy)',
                                 'Invitation Letter "Invite" replaced (uploaded as an approved copy)',
                                 'Invitation Letter "Invite" deleted']
    row = ActivityAuditLog.objects.filter(activity=activity, field_changed="Documents").order_by("-log_id").first()
    assert row.changed_by == director


def test_replacing_removes_the_old_chunks(activity, head) -> None:
    from assistant.models import DocumentEmbedding

    document = services.upload_document(head, activity, doc_type("Invitation Letter"), make_file(), "")
    DocumentEmbedding.objects.create(document=document, chunk_index=0, chunk_text="old",
                                     embedding_vector=[0.0] * 768)
    services.replace_document_file(head, document, make_file("b.pdf"))
    assert not DocumentEmbedding.objects.filter(document=document).exists()


def test_the_only_creation_document_of_a_scheduled_activity_is_never_deleted(make_activity, head, director) -> None:
    activity = make_activity(status=ActivityStatus.SCHEDULED, created_by=head)
    activity.creation_documents_required = True
    activity.save(update_fields=["creation_documents_required"])
    document = services.upload_document(head, activity, doc_type("Activity Design"), make_file(), "Design")
    with pytest.raises(services.DocumentError, match="only Activity Design"):
        services.delete_document(director, document)
    services.replace_document_file(director, document, make_file("b.pdf"))  # replacing stays allowed


def test_a_document_cited_by_the_ai_is_never_deleted(activity, head, director) -> None:
    from assistant.models import AIChat, AIChatMessage, AIMessageSource

    document = services.upload_document(head, activity, doc_type("Invitation Letter"), make_file(), "")
    chat = AIChat.objects.create(member=director, title="Q")
    message = AIChatMessage.objects.create(chat=chat, role="assistant", content="See [1].")
    AIMessageSource.objects.create(message=message, document=document)
    with pytest.raises(services.DocumentError, match="cited in an AI Assistant answer"):
        services.delete_document(director, document)
    assert Document.objects.filter(pk=document.pk).exists()


# --- Pages ---------------------------------------------------------------------------------------------------------


def test_the_documents_tab_says_approved_copy_and_hides_review_cards(activity, head) -> None:
    client = Client()
    client.force_login(head)
    response = client.post(reverse("activities:document_upload", args=[activity.pk]),
                           {"document_type": doc_type("Invitation Letter").pk, "title": "Invite", "file": make_file()},
                           follow=True)
    html = response.content.decode()
    assert "was uploaded as an approved copy." in html and "It is waiting for review" not in html
    page = client.get(reverse("documents:list")).content.decode()
    assert "Waiting for the Director" not in page and "Returned" not in page
    DocumentType.objects.filter(name="Other").update(needs_review=True)  # review in use again: the cards come back
    assert "Waiting for the Director" in client.get(reverse("documents:list")).content.decode()


def test_the_new_form_says_upload_the_approved_copies(director) -> None:
    client = Client()
    client.force_login(director)
    html = client.get(reverse("activities:create")).content.decode()
    assert "Upload the approved copies." in html and "waits for the Director" not in html


# --- Migration documents/0009 --------------------------------------------------------------------------------------


def test_the_data_migration_approves_waiting_documents_only(activity, head, director) -> None:
    waiting = services.upload_document(head, activity, doc_type("Invitation Letter"), make_file(), "Waiting")
    returned = services.upload_document(head, activity, doc_type("Pre-registration Form"), make_file(), "Returned")
    reviewed_type = doc_type("Other")
    reviewed_type.needs_review = True
    reviewed_type.save(update_fields=["needs_review"])
    kept = services.upload_document(head, activity, reviewed_type, make_file(), "Needs review")
    for_review = DocumentStatus.objects.get(status_name=DocumentStatus.FOR_REVIEW)
    Document.objects.filter(pk=waiting.pk).update(status=for_review, reviewed_at=None, reviewed_by=None)
    Document.objects.filter(pk=returned.pk).update(status=for_review, reviewed_at=TEST_NOW, reviewed_by=director)

    migration = importlib.import_module("documents.migrations.0009_waiting_uploads_approved")
    migration.approve_waiting(django_apps, None)
    states = {d.title: (d.review_state, d.reviewed_by_id) for d in Document.objects.all()}
    assert states["Waiting"] == (Document.APPROVED, None)
    assert states["Returned"] == (Document.RETURNED, director.pk)  # left alone
    assert states["Needs review"][0] == Document.WAITING_FOR_REVIEW  # a reviewed type is never touched
    migration.back_to_waiting(django_apps, None)
    assert Document.objects.get(title="Waiting").review_state == Document.WAITING_FOR_REVIEW
