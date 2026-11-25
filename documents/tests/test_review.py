"""Review: approve, return (with a reason), replace, delete, and the "waiting for my review" queue.

Only a role with can_approve_documents (the seeded Director) approves or returns (ASSUMPTIONS 344); the Incubation
Project Head can create activities but not review documents."""

from pathlib import Path

import pytest
from django.conf import settings
from django.core.exceptions import PermissionDenied, ValidationError

from documents import services
from documents.models import Document, DocumentStatus
from notifications.models import Notification

from .conftest import PNG_BYTES, TEST_NOW, make_file

pytestmark = pytest.mark.django_db


def stored(document: Document) -> Path:
    return Path(settings.MEDIA_ROOT) / document.file_path.name


@pytest.fixture
def document(upload, view_only, scheduled) -> Document:
    """Uploaded by Admin Staff, waiting for a reviewer."""
    return upload(view_only, scheduled)


# --- Approve -------------------------------------------------------------------------------------


def test_approve_sets_status_reviewer_and_time(document, director) -> None:
    services.approve_document(director, document, now=TEST_NOW)
    document.refresh_from_db()
    assert document.status.status_name == DocumentStatus.APPROVED
    assert document.reviewed_by == director and document.reviewed_at == TEST_NOW
    assert document.review_state == Document.APPROVED


def test_an_approver_may_approve_their_own_upload(upload, director, scheduled, caplog) -> None:
    own = upload(director, scheduled)
    with caplog.at_level("INFO", logger="documents.audit"):
        services.approve_document(director, own, now=TEST_NOW)
    own.refresh_from_db()
    assert own.status.status_name == DocumentStatus.APPROVED
    assert own.reviewed_by == director == own.uploaded_by and own.reviewed_at == TEST_NOW
    assert f"Document approved: document_id={own.pk} by member_id={director.pk} (own upload)" in caplog.text


def test_approve_is_logged_on_the_audit_logger(document, director, caplog) -> None:
    with caplog.at_level("INFO", logger="documents.audit"):
        services.approve_document(director, document)
    assert f"Document approved: document_id={document.pk} by member_id={director.pk}" in caplog.text
    assert "own upload" not in caplog.text


@pytest.mark.parametrize("role", ["view_only", "head", "system_admin"])
def test_member_without_can_approve_documents_cannot_approve(request, upload, director, scheduled, role) -> None:
    other = upload(director, scheduled)
    with pytest.raises(PermissionDenied, match="approve or return documents"):
        services.approve_document(request.getfixturevalue(role), other)
    other.refresh_from_db()
    assert other.status.status_name == DocumentStatus.FOR_REVIEW and other.reviewed_by is None


def test_project_head_cannot_approve_their_own_upload_either(upload, head, scheduled) -> None:
    with pytest.raises(PermissionDenied):
        services.approve_document(head, upload(head, scheduled))


def test_the_flag_decides_not_the_role_name(document, head) -> None:
    head.role.can_approve_documents = True
    head.role.save()
    services.approve_document(head, document)
    document.refresh_from_db()
    assert document.reviewed_by == head


def test_deactivated_reviewer_cannot_approve(document, director) -> None:
    director.is_active = False
    with pytest.raises(PermissionDenied):
        services.approve_document(director, document)


def test_approving_twice_is_refused(document, director, second_director) -> None:
    services.approve_document(director, document)
    with pytest.raises(services.DocumentError, match="already approved"):
        services.approve_document(second_director, document)


# --- Return --------------------------------------------------------------------------------------


def test_return_sends_the_reason_to_the_uploader(document, director, view_only, scheduled) -> None:
    services.return_document(director, document, "  Please add the signature page.  ", now=TEST_NOW)
    document.refresh_from_db()
    assert document.status.status_name == DocumentStatus.FOR_REVIEW
    assert document.reviewed_by == director and document.reviewed_at == TEST_NOW
    assert document.review_state == Document.RETURNED

    notification = Notification.objects.get(type=Notification.DOCUMENT_RETURNED)
    assert notification.member == view_only
    assert notification.type == Notification.DOCUMENT_RETURNED
    assert notification.activity == scheduled and notification.document_type == document.document_type
    assert "Please add the signature page." in notification.message
    assert document.title in notification.message and not notification.is_read


def test_return_needs_a_reason(document, director) -> None:
    with pytest.raises(ValidationError, match="reason"):
        services.return_document(director, document, "   ")
    with pytest.raises(ValidationError, match="at most 300"):
        services.return_document(director, document, "x" * 301)
    assert not Notification.objects.filter(type=Notification.DOCUMENT_RETURNED).exists()


def test_returning_your_own_upload_is_refused(upload, director, scheduled) -> None:
    own = upload(director, scheduled)
    with pytest.raises(services.DocumentError, match="Replace or delete it instead"):
        services.return_document(director, own, "Wrong file")
    own.refresh_from_db()
    assert own.reviewed_at is None
    assert not Notification.objects.filter(type=Notification.DOCUMENT_RETURNED).exists()


@pytest.mark.parametrize("role", ["view_only", "head", "system_admin"])
def test_member_without_can_approve_documents_cannot_return(request, upload, director, scheduled, role) -> None:
    with pytest.raises(PermissionDenied, match="approve or return documents"):
        services.return_document(request.getfixturevalue(role), upload(director, scheduled), "Wrong file")


def test_approve_is_refused_on_a_returned_document(document, director, second_director) -> None:
    services.return_document(director, document, "Missing signature")
    with pytest.raises(services.DocumentError, match="returned"):
        services.approve_document(second_director, document)
    with pytest.raises(services.DocumentError, match="returned"):
        services.return_document(second_director, document, "Again")


def test_an_approved_document_cannot_be_returned(document, director, second_director) -> None:
    services.approve_document(director, document)
    with pytest.raises(services.DocumentError, match="already approved"):
        services.return_document(second_director, document, "Too late")


def test_a_document_can_be_returned_again_after_replacing(document, director, view_only) -> None:
    services.return_document(director, document, "First reason")
    services.replace_document_file(view_only, document, make_file("v2.pdf"))
    services.return_document(director, document, "Second reason")
    assert Notification.objects.filter(type=Notification.DOCUMENT_RETURNED).count() == 2


# --- Replace -------------------------------------------------------------------------------------


def test_replace_clears_the_return_and_swaps_the_file(
    document, director, view_only, django_capture_on_commit_callbacks
) -> None:
    services.return_document(director, document, "Wrong file")
    old_file = stored(document)
    assert old_file.exists()

    with django_capture_on_commit_callbacks(execute=True):
        services.replace_document_file(view_only, document, make_file("Scan.PNG", PNG_BYTES))
    document.refresh_from_db()
    assert document.reviewed_by is None and document.reviewed_at is None
    assert document.review_state == Document.WAITING_FOR_REVIEW
    assert document.file_name == "Scan.PNG" and document.file_extension == "png"
    assert stored(document).read_bytes() == PNG_BYTES
    assert not old_file.exists()  # the old file is removed after the change is saved
    assert Document.objects.count() == 1  # same row

    services.approve_document(director, document)  # now it can be approved


def test_only_the_uploader_or_the_director_can_replace(document, director, head) -> None:
    with pytest.raises(PermissionDenied, match="uploaded"):
        services.replace_document_file(head, document, make_file())
    services.replace_document_file(director, document, make_file())  # ASSUMPTIONS 437: the Director edits others' work


def test_an_approved_document_cannot_be_replaced(document, director, view_only) -> None:
    services.approve_document(director, document)
    with pytest.raises(services.DocumentError, match="approved document cannot be replaced"):
        services.replace_document_file(view_only, document, make_file())


def test_replace_checks_the_file_too(document, view_only) -> None:
    with pytest.raises(ValidationError):
        services.replace_document_file(view_only, document, make_file("fake.pdf", b"not a pdf"))


# --- Delete --------------------------------------------------------------------------------------


def test_uploader_deletes_a_for_review_document_and_its_file(
    document, view_only, django_capture_on_commit_callbacks
) -> None:
    path = stored(document)
    with django_capture_on_commit_callbacks(execute=True):
        services.delete_document(view_only, document)
    assert not Document.objects.exists()
    assert not path.exists()


def test_only_the_uploader_or_the_director_can_delete(document, director, head) -> None:
    with pytest.raises(PermissionDenied):
        services.delete_document(head, document)
    assert Document.objects.exists()
    services.delete_document(director, document)  # ASSUMPTIONS 437
    assert not Document.objects.exists()


def test_an_approved_document_is_never_deleted(document, director, view_only) -> None:
    services.approve_document(director, document)
    with pytest.raises(services.DocumentError, match="approved document cannot be deleted"):
        services.delete_document(view_only, document)
    assert Document.objects.exists() and stored(document).exists()


def test_a_returned_document_can_be_deleted_by_its_uploader(document, director, view_only) -> None:
    services.return_document(director, document, "Wrong activity")
    services.delete_document(view_only, document)
    assert not Document.objects.exists()


# --- Review queue and allowed actions -------------------------------------------------------------------


def test_review_queue_lists_waiting_documents_including_my_own(upload, director, head, view_only, scheduled) -> None:
    mine = upload(director, scheduled, title="Mine")
    waiting = upload(view_only, scheduled, title="Waiting")
    returned = upload(head, scheduled, title="Returned")
    approved = upload(view_only, scheduled, title="Approved")
    services.return_document(director, returned, "Fix it")
    services.approve_document(director, approved)

    queue = list(services.review_queue(director))
    assert queue == [mine, waiting]  # oldest first; my own upload is there: I may approve it
    assert returned not in queue and approved not in queue


@pytest.mark.parametrize("role", ["view_only", "head", "system_admin"])
def test_review_queue_is_empty_without_can_approve_documents(request, upload, director, scheduled, role) -> None:
    upload(director, scheduled)
    assert list(services.review_queue(request.getfixturevalue(role))) == []


def test_allowed_actions(document, director, view_only, head) -> None:
    # ASSUMPTIONS 437: the Director may also replace / delete / rename another member's waiting document
    assert services.allowed_actions(director, document) == {"approve": True, "return": True, "replace": True,
                                                            "delete": True, "unapprove": False, "rename": True}
    assert services.allowed_actions(view_only, document) == {"approve": False, "return": False, "replace": True,
                                                             "delete": True, "unapprove": False, "rename": True}
    assert services.allowed_actions(head, document) == {"approve": False, "return": False, "replace": False,
                                                         "delete": False, "unapprove": False, "rename": False}
    # The services return the reloaded (locked) row; use what they return.
    document = services.return_document(director, document, "Fix")
    assert services.allowed_actions(director, document)["approve"] is False
    services.replace_document_file(view_only, document, make_file())
    document = services.approve_document(director, document)
    nothing = {"approve": False, "return": False, "replace": False, "delete": False, "unapprove": False}
    assert services.allowed_actions(view_only, document) == {**nothing, "rename": True}
    assert services.allowed_actions(director, document) == {**nothing, "unapprove": True, "rename": True}


def test_allowed_actions_on_an_approvers_own_upload(upload, director, scheduled) -> None:
    own = upload(director, scheduled)
    assert services.allowed_actions(director, own) == {"approve": True, "return": False, "replace": True, "delete": True,
                                                       "unapprove": False, "rename": True}


def test_approve_queues_indexing_after_commit_step_8() -> None:
    """Step 8 filled the marked place: indexing is queued once the approval is committed."""
    import inspect

    assert "transaction.on_commit(lambda: queue_indexing(document_id), robust=True)" in inspect.getsource(services.approve_document)
