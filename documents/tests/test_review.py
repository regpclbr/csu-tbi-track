"""Review: approve, return (with a reason), replace, delete, and the "waiting for my review" queue."""

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
def document(upload, admin_staff, scheduled) -> Document:
    """Uploaded by Admin Staff, waiting for a reviewer."""
    return upload(admin_staff, scheduled)


# --- Approve -------------------------------------------------------------------------------------


def test_approve_sets_status_reviewer_and_time(document, director) -> None:
    services.approve_document(director, document, now=TEST_NOW)
    document.refresh_from_db()
    assert document.status.status_name == DocumentStatus.APPROVED
    assert document.reviewed_by == director and document.reviewed_at == TEST_NOW
    assert document.review_state == Document.APPROVED


def test_self_approval_is_refused(upload, director, scheduled) -> None:
    own = upload(director, scheduled)
    with pytest.raises(PermissionDenied, match="you uploaded"):
        services.approve_document(director, own)
    own.refresh_from_db()
    assert own.status.status_name == DocumentStatus.FOR_REVIEW and own.reviewed_by is None


def test_member_without_can_create_activity_cannot_approve(upload, director, admin_staff, scheduled) -> None:
    other = upload(director, scheduled)
    with pytest.raises(PermissionDenied):
        services.approve_document(admin_staff, other)


def test_deactivated_reviewer_cannot_approve(document, director) -> None:
    director.is_active = False
    with pytest.raises(PermissionDenied):
        services.approve_document(director, document)


def test_approving_twice_is_refused(document, director, head) -> None:
    services.approve_document(director, document)
    with pytest.raises(services.DocumentError, match="already approved"):
        services.approve_document(head, document)


# --- Return --------------------------------------------------------------------------------------


def test_return_sends_the_reason_to_the_uploader(document, director, admin_staff, scheduled) -> None:
    services.return_document(director, document, "  Please add the signature page.  ", now=TEST_NOW)
    document.refresh_from_db()
    assert document.status.status_name == DocumentStatus.FOR_REVIEW
    assert document.reviewed_by == director and document.reviewed_at == TEST_NOW
    assert document.review_state == Document.RETURNED

    notification = Notification.objects.get()
    assert notification.member == admin_staff
    assert notification.type == Notification.DOCUMENT_RETURNED
    assert notification.activity == scheduled and notification.document_type == document.document_type
    assert "Please add the signature page." in notification.message
    assert document.title in notification.message and not notification.is_read


def test_return_needs_a_reason(document, director) -> None:
    with pytest.raises(ValidationError, match="reason"):
        services.return_document(director, document, "   ")
    with pytest.raises(ValidationError, match="at most 300"):
        services.return_document(director, document, "x" * 301)
    assert not Notification.objects.exists()


def test_returning_your_own_upload_is_refused(upload, director, scheduled) -> None:
    with pytest.raises(PermissionDenied):
        services.return_document(director, upload(director, scheduled), "Wrong file")


def test_member_without_can_create_activity_cannot_return(upload, director, admin_staff, scheduled) -> None:
    with pytest.raises(PermissionDenied):
        services.return_document(admin_staff, upload(director, scheduled), "Wrong file")


def test_approve_is_refused_on_a_returned_document(document, director, head) -> None:
    services.return_document(director, document, "Missing signature")
    with pytest.raises(services.DocumentError, match="returned"):
        services.approve_document(head, document)
    with pytest.raises(services.DocumentError, match="returned"):
        services.return_document(head, document, "Again")


def test_an_approved_document_cannot_be_returned(document, director, head) -> None:
    services.approve_document(director, document)
    with pytest.raises(services.DocumentError, match="already approved"):
        services.return_document(head, document, "Too late")


def test_a_document_can_be_returned_again_after_replacing(document, director, admin_staff) -> None:
    services.return_document(director, document, "First reason")
    services.replace_document_file(admin_staff, document, make_file("v2.pdf"))
    services.return_document(director, document, "Second reason")
    assert Notification.objects.filter(type=Notification.DOCUMENT_RETURNED).count() == 2


# --- Replace -------------------------------------------------------------------------------------


def test_replace_clears_the_return_and_swaps_the_file(
    document, director, admin_staff, django_capture_on_commit_callbacks
) -> None:
    services.return_document(director, document, "Wrong file")
    old_file = stored(document)
    assert old_file.exists()

    with django_capture_on_commit_callbacks(execute=True):
        services.replace_document_file(admin_staff, document, make_file("Scan.PNG", PNG_BYTES))
    document.refresh_from_db()
    assert document.reviewed_by is None and document.reviewed_at is None
    assert document.review_state == Document.WAITING_FOR_REVIEW
    assert document.file_name == "Scan.PNG" and document.file_extension == "png"
    assert stored(document).read_bytes() == PNG_BYTES
    assert not old_file.exists()  # the old file is removed after the change is saved
    assert Document.objects.count() == 1  # same row

    services.approve_document(director, document)  # now it can be approved


def test_only_the_uploader_can_replace(document, director) -> None:
    with pytest.raises(PermissionDenied, match="uploaded"):
        services.replace_document_file(director, document, make_file())


def test_an_approved_document_cannot_be_replaced(document, director, admin_staff) -> None:
    services.approve_document(director, document)
    with pytest.raises(services.DocumentError, match="approved document cannot be replaced"):
        services.replace_document_file(admin_staff, document, make_file())


def test_replace_checks_the_file_too(document, admin_staff) -> None:
    with pytest.raises(ValidationError):
        services.replace_document_file(admin_staff, document, make_file("fake.pdf", b"not a pdf"))


# --- Delete --------------------------------------------------------------------------------------


def test_uploader_deletes_a_for_review_document_and_its_file(
    document, admin_staff, django_capture_on_commit_callbacks
) -> None:
    path = stored(document)
    with django_capture_on_commit_callbacks(execute=True):
        services.delete_document(admin_staff, document)
    assert not Document.objects.exists()
    assert not path.exists()


def test_only_the_uploader_can_delete(document, director) -> None:
    with pytest.raises(PermissionDenied):
        services.delete_document(director, document)
    assert Document.objects.exists()


def test_an_approved_document_is_never_deleted(document, director, admin_staff) -> None:
    services.approve_document(director, document)
    with pytest.raises(services.DocumentError, match="approved document cannot be deleted"):
        services.delete_document(admin_staff, document)
    assert Document.objects.exists() and stored(document).exists()


def test_a_returned_document_can_be_deleted_by_its_uploader(document, director, admin_staff) -> None:
    services.return_document(director, document, "Wrong activity")
    services.delete_document(admin_staff, document)
    assert not Document.objects.exists()


# --- Review queue and allowed actions -------------------------------------------------------------------


def test_review_queue_shows_only_documents_waiting_for_me(upload, director, head, admin_staff, scheduled) -> None:
    mine = upload(director, scheduled, title="Mine")
    waiting = upload(admin_staff, scheduled, title="Waiting")
    returned = upload(head, scheduled, title="Returned")
    approved = upload(admin_staff, scheduled, title="Approved")
    services.return_document(director, returned, "Fix it")
    services.approve_document(head, approved)

    queue = list(services.review_queue(director))
    assert queue == [waiting]
    assert mine not in queue and returned not in queue and approved not in queue


def test_review_queue_is_empty_without_can_create_activity(upload, director, admin_staff, scheduled) -> None:
    upload(director, scheduled)
    assert list(services.review_queue(admin_staff)) == []


def test_allowed_actions(document, director, admin_staff) -> None:
    assert services.allowed_actions(director, document) == {"approve": True, "return": True, "replace": False, "delete": False}
    assert services.allowed_actions(admin_staff, document) == {"approve": False, "return": False, "replace": True, "delete": True}
    # The services return the reloaded (locked) row; use what they return.
    document = services.return_document(director, document, "Fix")
    assert services.allowed_actions(director, document)["approve"] is False
    services.replace_document_file(admin_staff, document, make_file())
    document = services.approve_document(director, document)
    nothing = {"approve": False, "return": False, "replace": False, "delete": False}
    assert services.allowed_actions(admin_staff, document) == nothing
    assert services.allowed_actions(director, document) == nothing


def test_approve_has_a_marked_place_for_step_8() -> None:
    import inspect

    assert "STEP 8: queue the embedding job here" in inspect.getsource(services.approve_document)
