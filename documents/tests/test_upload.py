"""Uploading: who may upload, file checks (real content, size), stored columns and the file path."""

from pathlib import Path

import pytest
from django.conf import settings
from django.core.exceptions import PermissionDenied, ValidationError

from activities.models import ActivityStatus
from documents import services
from documents.models import DocumentStatus, DocumentType

from .conftest import JPG_BYTES, PDF_BYTES, PNG_BYTES, make_file, office_bytes

pytestmark = pytest.mark.django_db


# --- Who may upload ----------------------------------------------------------------------------


def test_upload_starts_for_review_with_file_columns(upload, director, scheduled) -> None:
    document = upload(director, scheduled, make_file("My Invitation.PDF"))
    assert document.status.status_name == DocumentStatus.FOR_REVIEW
    assert document.review_state == document.WAITING_FOR_REVIEW
    assert document.reviewed_by is None and document.reviewed_at is None
    assert document.uploaded_by == director
    assert document.file_name == "My Invitation.PDF"
    assert document.file_extension == "pdf"
    assert document.file_size_kb == 1  # rounded up, at least 1
    assert document.title == "Request Letter"  # defaults to the type name


def test_any_active_staff_member_can_upload(upload, view_only, scheduled) -> None:
    """Admin Staff has no can_create_activity but may upload (pending TBI validation)."""
    assert upload(view_only, scheduled).uploaded_by == view_only


def test_deactivated_member_cannot_upload(upload, view_only, scheduled) -> None:
    view_only.is_active = False
    with pytest.raises(PermissionDenied):
        upload(view_only, scheduled)


@pytest.mark.parametrize("status", [ActivityStatus.DRAFT, ActivityStatus.ONGOING, ActivityStatus.CONDUCTED])
def test_upload_allowed_in_every_activity_status(upload, director, make_activity, status) -> None:
    """Conducted locks the activity's own fields, not its documents."""
    assert upload(director, make_activity(status=status)).pk


def test_inactive_document_type_is_refused(upload, director, scheduled, doc_type) -> None:
    doc_type.is_active = False
    doc_type.save()
    with pytest.raises(ValidationError, match="not active"):
        upload(director, scheduled)


def test_custom_title_is_kept(upload, director, scheduled) -> None:
    assert upload(director, scheduled, title="  Signed invitation  ").title == "Signed invitation"


# --- File checks --------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "name, content, stored_extension",
    [
        ("a.pdf", PDF_BYTES, "pdf"),
        ("a.png", PNG_BYTES, "png"),
        ("a.jpg", JPG_BYTES, "jpg"),
        ("a.JPEG", JPG_BYTES, "jpg"),
        ("a.docx", office_bytes("docx"), "docx"),
        ("a.xlsx", office_bytes("xlsx"), "xlsx"),
    ],
)
def test_allowed_types_are_accepted(upload, director, scheduled, name, content, stored_extension) -> None:
    assert upload(director, scheduled, make_file(name, content)).file_extension == stored_extension


@pytest.mark.parametrize(
    "name, content",
    [
        ("fake.pdf", b"MZ\x90\x00 this is really an .exe"),  # pdf extension, other content
        ("fake.pdf", PNG_BYTES),
        ("fake.png", PDF_BYTES),
        ("fake.jpg", PNG_BYTES),
        ("fake.docx", PDF_BYTES),
        ("fake.docx", office_bytes("xlsx")),  # a real XLSX renamed to .docx
        ("fake.xlsx", office_bytes("docx")),
    ],
)
def test_content_that_does_not_match_the_extension_is_refused(upload, director, scheduled, name, content) -> None:
    with pytest.raises(ValidationError, match="content is not a valid"):
        upload(director, scheduled, make_file(name, content))


@pytest.mark.parametrize("name", ["notes.txt", "old.doc", "sheet.xls", "script.exe", "no_extension"])
def test_other_extensions_are_refused(upload, director, scheduled, name) -> None:
    with pytest.raises(ValidationError, match="Only PDF, DOCX, XLSX, JPG and PNG"):
        upload(director, scheduled, make_file(name, PDF_BYTES))


def test_empty_file_is_refused(upload, director, scheduled) -> None:
    with pytest.raises(ValidationError, match="empty"):
        upload(director, scheduled, make_file("a.pdf", b""))


def test_missing_file_is_refused(director, scheduled, doc_type) -> None:
    with pytest.raises(ValidationError, match="Choose a file"):
        services.upload_document(director, scheduled, doc_type, None)


def test_size_limit_comes_from_the_setting(upload, director, scheduled, settings) -> None:
    settings.DOCUMENT_MAX_UPLOAD_MB = 1
    limit = 1024 * 1024
    assert upload(director, scheduled, make_file("ok.pdf", PDF_BYTES.ljust(limit, b" "))).file_size_kb == 1024
    with pytest.raises(ValidationError, match="larger than 1 MB"):
        upload(director, scheduled, make_file("big.pdf", PDF_BYTES.ljust(limit + 1, b" ")))


def test_default_size_limit_is_10_mb() -> None:
    assert settings.DOCUMENT_MAX_UPLOAD_MB == 10


# --- File path (naming pattern) ----------------------------------------------------------------------


def test_fallback_pattern_is_used_and_the_uploaded_name_is_not(upload, director, scheduled) -> None:
    document = upload(director, scheduled, make_file("../../secret name.pdf"))
    assert document.file_path.name == f"documents/activity-{scheduled.pk}/request-letter/20261020-request-letter.pdf"
    assert "secret" not in document.file_path.name
    assert document.file_name == "secret name.pdf"  # only the base name is kept


def test_file_is_saved_in_the_temporary_media_root(upload, director, scheduled) -> None:
    document = upload(director, scheduled)
    stored = Path(settings.MEDIA_ROOT) / document.file_path.name
    assert stored.read_bytes() == PDF_BYTES
    assert Path(settings.MEDIA_ROOT) != settings.BASE_DIR / "media"  # never the real media folder


def test_admin_naming_pattern_is_used(upload, director, scheduled, doc_type) -> None:
    doc_type.naming_pattern = "{year}/{activity_slug}/{type}_{date}_{activity_id}"
    doc_type.save()
    document = upload(director, scheduled)
    assert document.file_path.name == f"documents/2026/pitching-workshop/request-letter_20261020_{scheduled.pk}.pdf"


def test_same_name_twice_gets_a_unique_path(upload, director, scheduled) -> None:
    first, second = upload(director, scheduled), upload(director, scheduled)
    assert first.file_path.name != second.file_path.name


def test_path_stays_within_255_characters(upload, director, make_activity, doc_type) -> None:
    doc_type.naming_pattern = "/".join(["{activity_slug}-{type}-{date}"] * 6)
    doc_type.save()
    activity = make_activity(title="A very long activity title " * 9)
    for _ in range(2):  # the second one also needs Django's random suffix
        assert len(upload(director, activity).file_path.name) <= 255


def test_long_original_name_is_cut_to_255(upload, director, scheduled) -> None:
    document = upload(director, scheduled, make_file("x" * 300 + ".pdf"))
    assert len(document.file_name) == 255 and document.file_name.endswith(".pdf")


@pytest.mark.parametrize("pattern", ["{type}/{date}", "tbi/{year}/{activity_id}-{type}", ""])
def test_valid_naming_patterns(pattern) -> None:
    assert services.validate_naming_pattern(pattern) == pattern


@pytest.mark.parametrize(
    "pattern, message",
    [
        ("{type}/{name}", "Unknown placeholder"),
        ("../{type}", "Use only letters"),
        ("/{type}", "cannot start with /"),
        ("a//{type}", "cannot start with /"),
        ("{type}/", "must end with a file name"),
        ("{type}.pdf", "Use only letters"),
        ("{type", "must be closed"),
        ("C:\\{type}", "Use only letters"),
    ],
)
def test_invalid_naming_patterns(pattern, message) -> None:
    with pytest.raises(ValidationError, match=message):
        services.validate_naming_pattern(pattern)


def test_build_file_path_uses_manila_date(scheduled, doc_type) -> None:
    from datetime import datetime, timezone as dt_timezone

    late_utc = datetime(2026, 10, 20, 17, 30, tzinfo=dt_timezone.utc)  # Oct 21, 1:30 AM in Manila
    assert "20261021" in services.build_file_path(doc_type, scheduled, "pdf", late_utc)


def test_document_type_admin_refuses_a_bad_pattern(client, admin_member, doc_type) -> None:
    client.force_login(admin_member)
    data = {
        "name": doc_type.name, "description": "", "naming_pattern": "{nope}", "is_required": "on",
        "due_reference": doc_type.due_reference, "due_offset_days": doc_type.due_offset_days, "is_active": "on",
    }
    response = client.post(f"/admin/documents/documenttype/{doc_type.pk}/change/", data)
    assert response.status_code == 200 and "Unknown placeholder" in response.content.decode()
    doc_type.refresh_from_db()
    assert doc_type.naming_pattern == ""
    data["naming_pattern"] = "{year}/{type}"
    assert client.post(f"/admin/documents/documenttype/{doc_type.pk}/change/", data).status_code == 302
    assert DocumentType.objects.get(pk=doc_type.pk).naming_pattern == "{year}/{type}"

