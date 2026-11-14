"""Business logic for activity documents, the checklist and reminders.

Views (and the Django admin) stay thin and call the functions in this module.

Review flow (DOCUMENT_STATUS has only For Review and Approved; there is no Rejected):
- Upload: any active staff member. The document starts For Review (reviewed_at empty). Every active member who
  can approve documents gets a "waiting for your review" notification, except the uploader.
- Approve: a member with can_approve_documents (ASSUMPTIONS 344), also for their own upload. Approved is final.
- Return: a member with can_approve_documents, never for their own upload (they replace or delete it instead).
  It stays For Review, but reviewed_by/reviewed_at are set, which marks it "returned"; the uploader gets a
  notification with the reason and must replace the file.
- Replace: uploader only, For Review only. Clears reviewed_by/reviewed_at (waiting for a reviewer again) and
  notifies the approvers again, except the uploader.
- Delete: uploader only, For Review only (the file is removed too). Approved documents are never deleted.

Certificate evidence (related_certificate set, Step 9) uses the same upload, review, replace and
delete functions, but every listing and the checklist use Document.objects.activity_documents(),
so it never appears there.
"""

import logging
import math
import re
import zipfile
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import PurePosixPath

from django.conf import settings
from django.core.exceptions import PermissionDenied, ValidationError
from django.core.files.uploadedfile import UploadedFile
from django.db import IntegrityError, transaction
from django.db.models import Case, Exists, OuterRef, Q, QuerySet, Value, When
from django.utils import timezone
from django.utils.text import slugify

from accounts.models import TBIMember
from accounts.services import active_document_approvers
from accounts.permissions import Permission, has_permission, require_permission, require_staff_member
from activities.models import Activity, ActivityStatus
from activities.services import PageResult, _paginate
from assistant.indexing import queue_indexing, with_searchable
from notifications.models import Notification

from .models import Document, DocumentStatus, DocumentType

logger = logging.getLogger("documents.reminders")
audit_log = logging.getLogger("documents.audit")  # who approved or returned which document (never file contents)

DEFAULT_PAGE_SIZE = 20
RETURN_REASON_MAX_LENGTH = 300


class DocumentError(Exception):
    """An action on a document was refused. The message is safe to show to the member."""


# --- File checks ----------------------------------------------------------------------------

# Allowed extension -> the extension that is stored (".jpeg" is stored as "jpg").
ALLOWED_EXTENSIONS = {"pdf": "pdf", "docx": "docx", "xlsx": "xlsx", "jpg": "jpg", "jpeg": "jpg", "png": "png"}

# First bytes of each file type ("magic numbers").
PDF_SIGNATURE = b"%PDF-"
PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"
JPEG_SIGNATURE = b"\xff\xd8\xff"
# DOCX and XLSX are ZIP files; this file inside the ZIP tells them apart.
OFFICE_MAIN_PART = {"docx": "word/document.xml", "xlsx": "xl/workbook.xml"}


def max_upload_bytes() -> int:
    return settings.DOCUMENT_MAX_UPLOAD_MB * 1024 * 1024


def _content_matches(file: UploadedFile, extension: str) -> bool:
    """Check the real content (first bytes, or the parts inside a DOCX/XLSX), not the name."""
    file.seek(0)
    head = file.read(16)
    file.seek(0)
    if extension == "pdf":
        return head.startswith(PDF_SIGNATURE)
    if extension == "png":
        return head.startswith(PNG_SIGNATURE)
    if extension == "jpg":
        return head.startswith(JPEG_SIGNATURE)
    try:  # docx / xlsx
        with zipfile.ZipFile(file) as archive:
            names = set(archive.namelist())
    except (zipfile.BadZipFile, OSError):
        return False
    finally:
        file.seek(0)
    return "[Content_Types].xml" in names and OFFICE_MAIN_PART[extension] in names


def validate_upload(file: UploadedFile | None) -> str:
    """Refuse anything that is not a real PDF, DOCX, XLSX, JPG or PNG within the size limit.

    Returns the stored extension (lowercase, no dot).
    """
    if file is None:
        raise ValidationError({"file": ["Choose a file to upload."]})
    extension = PurePosixPath(file.name or "").suffix.lower().lstrip(".")
    if extension not in ALLOWED_EXTENSIONS:
        raise ValidationError({"file": ["Only PDF, DOCX, XLSX, JPG and PNG files can be uploaded."]})
    if not file.size:
        raise ValidationError({"file": ["The file is empty."]})
    if file.size > max_upload_bytes():
        raise ValidationError({"file": [f"The file is larger than {settings.DOCUMENT_MAX_UPLOAD_MB} MB."]})
    extension = ALLOWED_EXTENSIONS[extension]
    if not _content_matches(file, extension):
        raise ValidationError({"file": [f"The file's content is not a valid {extension.upper()} file."]})
    return extension


def _original_name(file: UploadedFile) -> str:
    """The uploaded name without any folder part, at most 255 characters (keeping the extension)."""
    name = PurePosixPath((file.name or "").replace("\\", "/")).name.strip() or "file"
    if len(name) > 255:
        suffix = PurePosixPath(name).suffix[:20]
        name = name[: 255 - len(suffix)] + suffix
    return name


def _size_kb(file: UploadedFile) -> int:
    return max(1, math.ceil(file.size / 1024))


# --- Stored file path (DOCUMENT_TYPE.naming_pattern) --------------------------------------

UPLOAD_ROOT = "documents"
# Used while a document type has no naming_pattern yet (the TBI's official convention is an open item).
FALLBACK_NAMING_PATTERN = "activity-{activity_id}/{type}/{date}-{type}"
NAMING_PLACEHOLDERS = {
    "activity_id": "the activity's ID, e.g. 12",
    "activity_slug": "the activity title, e.g. pitching-workshop (at most 40 characters)",
    "type": "the document type, e.g. invitation-letter",
    "date": "the upload date (Manila time), e.g. 20261020",
    "year": "the upload year, e.g. 2026",
}
_PLACEHOLDER = re.compile(r"\{([^{}]*)\}")
_ALLOWED_PATTERN_CHARS = re.compile(r"^[A-Za-z0-9_\-/{}]+$")
# Room kept for the root folder, the extension and the random suffix Django adds if a name is taken.
MAX_PATTERN_RESULT = 200


def validate_naming_pattern(pattern: str) -> str:
    """Check an Admin-entered naming pattern. Returns it trimmed; an empty pattern is allowed."""
    pattern = (pattern or "").strip()
    if not pattern:
        return ""
    allowed = ", ".join("{" + name + "}" for name in NAMING_PLACEHOLDERS)
    if not _ALLOWED_PATTERN_CHARS.match(pattern):
        raise ValidationError("Use only letters, digits, - _ / and the placeholders " + allowed + ".")
    unknown = [name for name in _PLACEHOLDER.findall(pattern) if name not in NAMING_PLACEHOLDERS]
    if unknown:
        raise ValidationError(f"Unknown placeholder {{{unknown[0]}}}. Allowed: {allowed}.")
    if _PLACEHOLDER.sub("", pattern).count("{") or _PLACEHOLDER.sub("", pattern).count("}"):
        raise ValidationError("Every { must be closed by a }.")
    if pattern.startswith("/") or any(part in ("", ".", "..") for part in pattern.rstrip("/").split("/")):
        raise ValidationError("The pattern cannot start with /, contain // or use . or .. as a folder.")
    if pattern.endswith("/"):
        raise ValidationError("The pattern must end with a file name, not /.")
    return pattern


def build_file_path(document_type: DocumentType, activity: Activity, extension: str, now: datetime | None = None) -> str:
    """The stored path: documents/<naming pattern filled in>.<extension>, within 255 characters.

    The uploaded file's own name is never used.
    """
    today = timezone.localtime(now or timezone.now()).date()
    values = {
        "activity_id": str(activity.pk),
        "activity_slug": slugify(activity.title)[:40].strip("-") or "activity",
        "type": slugify(document_type.name)[:40].strip("-") or "document",
        "date": today.strftime("%Y%m%d"),
        "year": str(today.year),
    }
    pattern = document_type.naming_pattern.strip() or FALLBACK_NAMING_PATTERN
    try:
        pattern = validate_naming_pattern(pattern)
    except ValidationError:
        logger.warning("Invalid naming pattern on document type %s; using the fallback.", document_type.pk)
        pattern = FALLBACK_NAMING_PATTERN
    filled = _PLACEHOLDER.sub(lambda match: values[match.group(1)], pattern)[:MAX_PATTERN_RESULT].rstrip("/")
    return f"{UPLOAD_ROOT}/{filled}.{extension}"


def _save_file(document: Document, file: UploadedFile, extension: str, now: datetime | None) -> None:
    """Store the file under the pattern path and fill the file columns (the row is not saved here)."""
    path = build_file_path(document.document_type, document.activity, extension, now)
    document.file_path.save(path, file, save=False)  # Django adds a suffix if the name is taken
    document.file_name = _original_name(file)
    document.file_extension = extension
    document.file_size_kb = _size_kb(file)


def _delete_stored_file(name: str) -> None:
    if name:
        Document._meta.get_field("file_path").storage.delete(name)


# --- Upload, replace, delete -----------------------------------------------------------------


def _locked(document: Document) -> Document:
    """Reload and lock the document row so two members cannot act on it at the same moment.
    (Activity documents and certificate evidence follow the same rules.)"""
    return (
        Document.objects.select_for_update()
        .select_related("status", "document_type", "activity")
        .get(pk=document.pk)
    )


def upload_document(
    member: TBIMember,
    activity: Activity,
    document_type: DocumentType,
    file: UploadedFile | None,
    title: str = "",
    now: datetime | None = None,
    related_certificate=None,
) -> Document:
    """Any active staff member uploads a document for an activity, in any activity status
    (including Conducted: the accomplishment report is due after the end date). It starts For Review.

    `related_certificate` is set only by certificates.services.upload_evidence (Step 9)."""
    require_staff_member(member)
    if not document_type.is_active:
        raise ValidationError({"document_type": ["This document type is not active."]})
    extension = validate_upload(file)
    title = (title or "").strip() or document_type.name
    if len(title) > 255:
        raise ValidationError({"title": ["The title can be at most 255 characters."]})

    document = Document(
        activity=activity,
        document_type=document_type,
        uploaded_by=member,
        status=DocumentStatus.objects.get(status_name=DocumentStatus.FOR_REVIEW),
        title=title,
        related_certificate=related_certificate,
    )
    _save_file(document, file, extension, now)
    try:
        with transaction.atomic():  # the document and its review notifications are saved together
            document.save()
            notify_approvers(document, member)
    except Exception:
        _delete_stored_file(document.file_path.name)  # don't leave an orphan file behind
        raise
    return document


def notify_approvers(document: Document, actor: TBIMember) -> int:
    """In-app "waiting for your review" notification for every active member who can approve documents,
    except `actor` (the member who uploaded or replaced the file). Returns how many were created.
    Call it inside the transaction that saves the document, so a rolled-back upload notifies nobody."""
    recipients = active_document_approvers().exclude(pk=actor.pk).order_by("pk")
    message = (
        f'{actor.name[:60]} uploaded the {document.document_type.name} "{document.title[:80]}" for '
        f'"{document.activity.title[:80]}". It is waiting for your review.'
    )[:500]
    created = Notification.objects.bulk_create(
        Notification(
            member=recipient,
            activity=document.activity,
            document_type=document.document_type,
            type=Notification.DOCUMENT_WAITING_FOR_REVIEW,
            message=message,
        )
        for recipient in recipients
    )
    return len(created)


# --- Documents uploaded with a new activity (ASSUMPTIONS 327) ---------------------------------------

MAX_DOCUMENTS_AT_CREATION = 10


@dataclass
class NewDocument:
    """One optional document row of the New activity form."""

    document_type: DocumentType | None
    file: UploadedFile | None
    title: str = ""


class NewActivityError(Exception):
    """Creating the activity with its documents was refused; nothing was saved.

    `activity_error`: the activity's own ValidationError (or None); `row_errors`: {row index: {field: message}}."""

    def __init__(self, activity_error: ValidationError | None, row_errors: dict[int, dict[str, str]]):
        super().__init__("The activity was not created.")
        self.activity_error = activity_error
        self.row_errors = row_errors


class _RollBack(Exception):
    """Undo the transaction of create_activity_with_documents (the errors are raised after it)."""


def types_allowed_at_creation() -> QuerySet:
    """Active types that make sense before the activity happens: not due after the end date and not named in
    settings.DOCUMENT_TYPES_AFTER_ACTIVITY (types without a due rule, such as Certificate or Evaluation Form)."""
    return (
        DocumentType.objects.filter(is_active=True)
        .exclude(due_reference=DocumentType.DueReference.END_DATE)
        .exclude(name__in=settings.DOCUMENT_TYPES_AFTER_ACTIVITY)
    )


def check_new_documents(documents: list[NewDocument]) -> dict[int, dict[str, str]]:
    """Every row's type, file (validate_upload: real content, size) and title, before anything is saved."""
    allowed = set(types_allowed_at_creation().values_list("pk", flat=True))
    errors: dict[int, dict[str, str]] = {}
    for index, row in enumerate(documents):
        row_errors = {}
        if row.document_type is None:
            row_errors["document_type"] = "Choose a document type."
        elif row.document_type.pk not in allowed:
            row_errors["document_type"] = (
                "This document type cannot be uploaded when creating an activity. "
                "Upload it from the activity's Documents tab later."
            )
        try:
            validate_upload(row.file)
        except ValidationError as error:
            row_errors["file"] = error.messages[0]
        if len((row.title or "").strip()) > 255:
            row_errors["title"] = "The title can be at most 255 characters."
        if row_errors:
            errors[index] = row_errors
    return errors


def create_activity_with_documents(
    member: TBIMember, data: dict, documents: list[NewDocument], now: datetime | None = None,
    signatories: dict | None = None,
) -> Activity:
    """Create a Draft activity (activities.services.create_activity) and upload its optional documents
    (upload_document, the same rules as the Documents tab) in one transaction: all or nothing.

    Errors of the activity and of every row are raised together as NewActivityError. Files already stored when
    something fails are deleted again. upload_document's review notifications are rows in this same transaction
    and it creates no email or task (indexing is queued only on approval, on commit), so a refused create never
    tells anyone anything."""
    from activities.services import create_activity  # documents imports activities, never the other way round

    require_permission(member, Permission.CREATE_ACTIVITY)
    if len(documents) > MAX_DOCUMENTS_AT_CREATION:
        raise ValidationError({"documents": [f"Add at most {MAX_DOCUMENTS_AT_CREATION} documents here."]})
    row_errors = check_new_documents(documents)
    activity_error = None
    stored: list[str] = []
    committed = False
    try:
        with transaction.atomic():
            try:
                activity = create_activity(member, data, now=now, signatories=signatories)
            except ValidationError as error:
                activity_error = error
                raise _RollBack from error
            if row_errors:
                raise _RollBack
            for index, row in enumerate(documents):
                try:
                    document = upload_document(member, activity, row.document_type, row.file, row.title, now=now)
                except ValidationError as error:  # e.g. the type was deactivated a moment ago
                    found = error.message_dict if hasattr(error, "error_dict") else {"file": error.messages}
                    row_errors[index] = {key: messages[0] for key, messages in found.items()}
                    raise _RollBack from error
                stored.append(document.file_path.name)
        committed = True
    except _RollBack:
        pass
    finally:
        if not committed:
            for name in stored:
                _delete_stored_file(name)
    if not committed:
        raise NewActivityError(activity_error, row_errors)
    return activity


# --- Preview (ASSUMPTIONS 329) ---------------------------------------------------------------------------

# Types the browser can show inline, keyed by the stored file_extension: the type validate_upload confirmed from the
# real content at upload (never the uploaded name). Word and Excel files are only downloaded (no converter).
PREVIEW_CONTENT_TYPES = {"pdf": "application/pdf", "png": "image/png", "jpg": "image/jpeg"}


def preview_content_type(document: Document) -> str | None:
    """The content type to show `document` inline with, or None when it can only be downloaded."""
    return PREVIEW_CONTENT_TYPES.get(document.file_extension)


def open_for_preview(document: Document):
    """Open the stored file for inline display. Raises DocumentError when it cannot be previewed or the stored
    content does not match its confirmed type, and FileNotFoundError when the file is missing."""
    if preview_content_type(document) is None:
        raise DocumentError("This file type cannot be previewed. Download it instead.")
    try:
        handle = document.file_path.open("rb")
    except ValueError as error:  # no file name stored
        raise FileNotFoundError(document.file_path.name) from error
    if not _content_matches(handle, document.file_extension):  # checked again on the stored bytes
        handle.close()
        raise DocumentError("This file cannot be previewed. Download it instead.")
    return handle


def _ensure_uploader_and_for_review(member: TBIMember, document: Document, action: str) -> None:
    if document.uploaded_by_id != member.pk:
        raise PermissionDenied(f"Only the member who uploaded this document can {action} it.")
    if document.status.status_name != DocumentStatus.FOR_REVIEW:
        raise DocumentError(f"An approved document cannot be {action}d. Upload a new document instead.")


def replace_document_file(member: TBIMember, document: Document, file: UploadedFile | None, now: datetime | None = None) -> Document:
    """The uploader replaces the file of a For Review document (usually after it was returned).

    Same row, title and type. reviewed_by/reviewed_at are cleared, so it waits for a reviewer again.
    The old file is deleted only after the change is saved.
    """
    require_staff_member(member)
    extension = validate_upload(file)
    with transaction.atomic():
        document = _locked(document)
        _ensure_uploader_and_for_review(member, document, "replace")
        old_name = document.file_path.name
        _save_file(document, file, extension, now)
        document.reviewed_by = None
        document.reviewed_at = None
        try:
            with transaction.atomic():
                document.save()
                notify_approvers(document, member)
        except Exception:
            _delete_stored_file(document.file_path.name)
            raise
        transaction.on_commit(lambda: _delete_stored_file(old_name))
    return document


def delete_document(member: TBIMember, document: Document) -> None:
    """The uploader deletes a For Review document and its file. Approved documents are never deleted."""
    require_staff_member(member)
    with transaction.atomic():
        document = _locked(document)
        _ensure_uploader_and_for_review(member, document, "delete")
        name = document.file_path.name
        document.delete()
        transaction.on_commit(lambda: _delete_stored_file(name))


# --- Review: approve and return ------------------------------------------------------------


RETURN_OWN_REFUSED = "You cannot return a document you uploaded. Replace or delete it instead."


def _ensure_can_review(member: TBIMember, document: Document) -> None:
    """Reviewer rule (ASSUMPTIONS 344, pending TBI validation): can_approve_documents. Approving your own upload
    is allowed (reviewed_by is then the uploader)."""
    require_permission(member, Permission.APPROVE_DOCUMENTS)
    if document.status.status_name != DocumentStatus.FOR_REVIEW:
        raise DocumentError("This document is already approved.")
    if document.reviewed_at is not None:
        raise DocumentError("This document was returned to the uploader. It can be reviewed after the file is replaced.")


def approve_document(member: TBIMember, document: Document, now: datetime | None = None) -> Document:
    """Approve a document waiting for review. Status, reviewed_by and reviewed_at change together."""
    with transaction.atomic():
        document = _locked(document)
        _ensure_can_review(member, document)
        document.status = DocumentStatus.objects.get(status_name=DocumentStatus.APPROVED)
        document.reviewed_by = member
        document.reviewed_at = now or timezone.now()
        document.save(update_fields=["status", "reviewed_by", "reviewed_at", "updated_at"])
        audit_log.info(
            "Document approved: document_id=%s by member_id=%s%s", document.pk, member.pk,
            " (own upload)" if document.uploaded_by_id == member.pk else "",
        )

        # Step 8: index it for the assistant once the approval is saved (Django-Q2 task).
        # robust=True: a problem queueing the task never undoes or breaks the approval.
        # Certificate evidence is never indexed (item 151), so it is not queued.
        document_id = document.pk
        if document.related_certificate_id is None:
            transaction.on_commit(lambda: queue_indexing(document_id), robust=True)
    return document


def return_document(member: TBIMember, document: Document, reason: str, now: datetime | None = None) -> Document:
    """Send a document back to its uploader with a reason. It stays For Review but is marked returned
    (reviewed_by/reviewed_at set) until the uploader replaces the file."""
    reason = (reason or "").strip()
    if not reason:
        raise ValidationError({"reason": ["Give a reason for returning the document."]})
    if len(reason) > RETURN_REASON_MAX_LENGTH:
        raise ValidationError({"reason": [f"The reason can be at most {RETURN_REASON_MAX_LENGTH} characters."]})

    with transaction.atomic():
        document = _locked(document)
        _ensure_can_review(member, document)
        if document.uploaded_by_id == member.pk:
            raise DocumentError(RETURN_OWN_REFUSED)
        document.reviewed_by = member
        document.reviewed_at = now or timezone.now()
        document.save(update_fields=["reviewed_by", "reviewed_at", "updated_at"])
        audit_log.info("Document returned: document_id=%s by member_id=%s", document.pk, member.pk)
        Notification.objects.create(
            member=document.uploaded_by,
            activity=document.activity,
            document_type=document.document_type,
            type=Notification.DOCUMENT_RETURNED,
            message=(
                f'Your {document.document_type.name} "{document.title[:80]}" for "{document.activity.title[:80]}" '
                f"was returned by {member.name[:60]}: {reason}"
            )[:500],
        )
    return document


# --- Reading: detail, repository, review queue -------------------------------------------------


def documents_for_listing() -> QuerySet:
    """Activity documents with `searchable` (has embeddings for the assistant; no column)."""
    return with_searchable(Document.objects.activity_documents()).select_related(
        "activity", "document_type", "status", "uploaded_by", "reviewed_by"
    )


def allowed_actions(member: TBIMember, document: Document) -> dict[str, bool]:
    """What `member` may do with `document` right now (for the frontend; the services check again)."""
    for_review = document.status.status_name == DocumentStatus.FOR_REVIEW
    is_uploader = document.uploaded_by_id == member.pk
    can_review = for_review and document.reviewed_at is None and has_permission(member, Permission.APPROVE_DOCUMENTS)
    return {
        "approve": can_review,  # also the member's own upload (ASSUMPTIONS 344)
        "return": can_review and not is_uploader,
        "replace": for_review and is_uploader,
        "delete": for_review and is_uploader,
    }


def list_documents(filters: dict | None = None, page: int | str | None = 1, per_page: int = DEFAULT_PAGE_SIZE) -> PageResult:
    """Document Repository: activity documents across activities, newest first.

    Filters (all optional): activity, document_type, status, uploaded_by (IDs),
    review_state ("waiting_for_review" | "returned" | "approved"), searchable (True/False),
    q (searches title and file name).
    """
    filters = filters or {}
    queryset = documents_for_listing()
    for key in ("activity", "document_type", "status", "uploaded_by"):
        if filters.get(key):
            queryset = queryset.filter(**{f"{key}_id": filters[key]})
    state = filters.get("review_state")
    if state == Document.WAITING_FOR_REVIEW:
        queryset = queryset.waiting_for_reviewer()
    elif state == Document.RETURNED:
        queryset = queryset.returned()
    elif state == Document.APPROVED:
        queryset = queryset.approved()
    elif state:
        raise DocumentError("review_state must be waiting_for_review, returned or approved.")
    if filters.get("searchable") is not None:
        queryset = queryset.filter(searchable=filters["searchable"])
    search = (filters.get("q") or "").strip()
    if search:
        queryset = queryset.filter(Q(title__icontains=search) | Q(file_name__icontains=search))
    return _paginate(queryset.order_by("-uploaded_at", "-document_id"), page, per_page)


def review_queue(member: TBIMember) -> QuerySet:
    """For Review documents waiting for a reviewer, including `member`'s own uploads (they may approve them,
    ASSUMPTIONS 344). Returned documents are left out: they wait for the uploader. Empty without
    can_approve_documents."""
    if not has_permission(member, Permission.APPROVE_DOCUMENTS):
        return Document.objects.none()
    return documents_for_listing().waiting_for_reviewer().order_by("uploaded_at")


# --- Checklist (database checks; the Step 8 assistant reuses it for "what is missing") ---------

MISSING, FOR_REVIEW, APPROVED = "Missing", DocumentStatus.FOR_REVIEW, DocumentStatus.APPROVED


def today_in_manila(now: datetime | None = None) -> date:
    return timezone.localtime(now or timezone.now()).date()


def due_date(document_type: DocumentType, activity: Activity) -> date | None:
    """activity.start_date or end_date + due_offset_days. Calculated, never stored. None if not required."""
    if not document_type.due_reference or document_type.due_offset_days is None:
        return None
    reference = getattr(activity, document_type.due_reference)
    return reference + timedelta(days=document_type.due_offset_days)


@dataclass
class ChecklistItem:
    document_type: DocumentType
    due_date: date
    state: str  # Missing | For Review | Approved
    is_overdue: bool  # past the due date and not Approved (on the due date itself: not yet overdue)
    documents: list[Document]


def _documents_by_type(activity: Activity) -> dict[int, list[Document]]:
    grouped: dict[int, list[Document]] = {}
    for document in documents_for_listing().filter(activity=activity).order_by("-uploaded_at", "-document_id"):
        grouped.setdefault(document.document_type_id, []).append(document)
    return grouped


def _required_item(document_type: DocumentType, activity: Activity, documents: list[Document], today: date) -> ChecklistItem:
    if any(d.status.status_name == DocumentStatus.APPROVED for d in documents):
        state = APPROVED  # complete: at least one Approved document of this type
    elif documents:
        state = FOR_REVIEW
    else:
        state = MISSING
    due = due_date(document_type, activity)
    return ChecklistItem(document_type, due, state, today > due and state != APPROVED, documents)


def document_checklist(activity: Activity, now: datetime | None = None) -> dict:
    """Every active required document type with its due date, state and overdue flag, plus the
    optional (non-required) active types with their uploaded documents."""
    today = today_in_manila(now)
    by_type = _documents_by_type(activity)
    required, optional = [], []
    for document_type in DocumentType.objects.filter(is_active=True).order_by("name"):
        documents = by_type.get(document_type.pk, [])
        if document_type.is_required:
            required.append(_required_item(document_type, activity, documents, today))
        else:
            optional.append({"document_type": document_type, "documents": documents})
    required.sort(key=lambda item: (item.due_date, item.document_type.name))
    return {"today": today, "required": required, "optional": optional}


def documents_by_activity(activity_ids: list[int]) -> dict[int, list[Document]]:
    """The activity documents (documents_for_listing) of many activities in one query, newest first."""
    grouped: dict[int, list[Document]] = {}
    documents = documents_for_listing().filter(activity_id__in=activity_ids).order_by("-uploaded_at", "-document_id")
    for document in documents:
        grouped.setdefault(document.activity_id, []).append(document)
    return grouped


def required_items(activity: Activity, documents: list[Document], types: list[DocumentType], today: date) -> list[ChecklistItem]:
    """document_checklist()'s required items, from documents already loaded (documents_by_activity) and the active
    required types: the same rule (_required_item), without a query per activity (the Activities list's
    "needs attention" counts, activities.attention)."""
    by_type: dict[int, list[Document]] = {}
    for document in documents:
        by_type.setdefault(document.document_type_id, []).append(document)
    items = [_required_item(document_type, activity, by_type.get(document_type.pk, []), today) for document_type in types]
    return sorted(items, key=lambda item: (item.due_date, item.document_type.name))


def reminder_kind(item: ChecklistItem, today: date) -> str | None:
    """What the daily reminder says about this checklist item: Notification.DOCUMENT_OVERDUE, DOCUMENT_DUE_SOON
    (not uploaded, due within DOCUMENT_REMINDER_DAYS_BEFORE days) or None. The reminders' own rule."""
    return _reminder_kind(item, today, settings.DOCUMENT_REMINDER_DAYS_BEFORE)


# --- Documents page "By activity" (ASSUMPTIONS 352): the checklist of many activities at once -------------------


@dataclass
class ActivityDocuments:
    """One activity on the Documents page: document_checklist()'s required items (due-date order) and every other
    activity document (types that are not required, or no longer active), newest first."""

    activity: Activity
    required: list[ChecklistItem]
    other: list[Document]

    @property
    def documents(self) -> list[Document]:
        return [document for item in self.required for document in item.documents] + self.other

    @property
    def approved_count(self) -> int:
        """Required types complete (at least one Approved document)."""
        return sum(item.state == APPROVED for item in self.required)

    @property
    def missing_count(self) -> int:
        """Required types with nothing uploaded."""
        return sum(item.state == MISSING for item in self.required)

    @property
    def counts_missing(self) -> bool:
        """Missing required documents are counted (summary card, "Missing" filter) only outside Draft: the scope of
        the reminders and of the dashboard's overdue number (S3)."""
        return self.activity.status.name != ActivityStatus.DRAFT


def overview_activities() -> QuerySet:
    """The activities on the Documents page: every non-Draft activity, and Draft activities that already have
    documents. Ongoing first, then the newest start date."""
    has_documents = Document.objects.activity_documents().filter(activity=OuterRef("pk"))
    return (
        Activity.objects.filter(~Q(status__name=ActivityStatus.DRAFT) | Exists(has_documents))
        .select_related("status", "activity_type")
        .annotate(ongoing_first=Case(When(status__name=ActivityStatus.ONGOING, then=Value(0)), default=Value(1)))
        .order_by("ongoing_first", "-start_date", "title", "pk")
    )


def activity_document_overview(activities: list[Activity], now: datetime | None = None) -> list[ActivityDocuments]:
    """document_checklist() for many activities in two queries (the active required types and the documents), with
    the same rule (required_items / _required_item). Order of `activities` is kept."""
    today = today_in_manila(now)
    types = list(DocumentType.objects.filter(is_active=True, is_required=True).order_by("name"))
    required_ids = {document_type.pk for document_type in types}
    by_activity = documents_by_activity([activity.pk for activity in activities])
    overview = []
    for activity in activities:
        documents = by_activity.get(activity.pk, [])
        overview.append(ActivityDocuments(
            activity=activity,
            required=required_items(activity, documents, types, today),
            other=[document for document in documents if document.document_type_id not in required_ids],
        ))
    return overview


def document_state_counts(overview: list[ActivityDocuments]) -> dict[str, int]:
    """The Documents page's summary cards, from the overview of overview_activities() (every activity document is in
    it): documents waiting for a reviewer, returned to the uploader and approved (the S4 / review_state rules), and
    required types missing in non-Draft activities."""
    counts = {Document.WAITING_FOR_REVIEW: 0, Document.RETURNED: 0, Document.APPROVED: 0, MISSING_STATE: 0}
    for entry in overview:
        for document in entry.documents:
            counts[document.review_state] += 1
        if entry.counts_missing:
            counts[MISSING_STATE] += entry.missing_count
    return counts


MISSING_STATE = "missing"  # the fourth state on the Documents page (a required type with nothing uploaded)


# --- Reminders (run daily by Django-Q2; see documents/tasks.py) ----------------------------------


def _format_date(value: date) -> str:
    return f"{value:%b} {value.day}, {value.year}"


def _reminder_message(kind: str, item: ChecklistItem, activity: Activity) -> str:
    type_name, title, due = item.document_type.name, activity.title[:150], _format_date(item.due_date)
    if kind == Notification.DOCUMENT_DUE_SOON:
        return f'Reminder: the {type_name} for "{title}" is due on {due} and has not been uploaded yet.'
    return f'Overdue: the {type_name} for "{title}" was due on {due} and is not approved yet.'


def _reminder_kind(item: ChecklistItem, today: date, days_before: int) -> str | None:
    if item.is_overdue:
        return Notification.DOCUMENT_OVERDUE
    if item.state == MISSING and 0 <= (item.due_date - today).days <= days_before:
        return Notification.DOCUMENT_DUE_SOON
    return None


def _create_reminder_once(member: TBIMember, activity: Activity, item: ChecklistItem, kind: str) -> bool:
    """Create the reminder unless it already exists. The unique constraint decides, so two runs at
    the same moment cannot both create it. Returns True if it was created now."""
    try:
        with transaction.atomic():  # savepoint: a duplicate only undoes this one row
            Notification.objects.create(
                member=member,
                activity=activity,
                document_type=item.document_type,
                type=kind,
                message=_reminder_message(kind, item, activity)[:500],
            )
    except IntegrityError:
        return False
    return True


def send_document_reminders(now: datetime | None = None) -> dict[str, int]:
    """Create in-app reminders for each activity's creator (created_by):
    - "due soon": a required type with no uploaded document, due within DOCUMENT_REMINDER_DAYS_BEFORE days;
    - "overdue": a required type past its due date and not Approved.
    Draft activities and inactive types are skipped. A deactivated creator is skipped and logged.
    At most one reminder per (member, activity, document type, kind): running it again creates nothing new.
    """
    today = today_in_manila(now)
    days_before = settings.DOCUMENT_REMINDER_DAYS_BEFORE
    counts = {Notification.DOCUMENT_DUE_SOON: 0, Notification.DOCUMENT_OVERDUE: 0, "already_sent": 0, "skipped_inactive_creator": 0}

    activities = Activity.objects.exclude(status__name=ActivityStatus.DRAFT).select_related("created_by").order_by("pk")
    for activity in activities:
        due = [
            (item, kind)
            for item in document_checklist(activity, now)["required"]
            if (kind := _reminder_kind(item, today, days_before))
        ]
        if not due:
            continue
        creator = activity.created_by
        if not creator.is_active:
            # Open item for the TBI: who should get these reminders instead?
            logger.warning(
                "Skipped %d document reminder(s) for activity %s: its creator (member %s) is deactivated.",
                len(due), activity.pk, creator.pk,
            )
            counts["skipped_inactive_creator"] += len(due)
            continue
        for item, kind in due:
            if _create_reminder_once(creator, activity, item, kind):
                counts[kind] += 1
            else:
                counts["already_sent"] += 1
    return {
        "due_soon": counts[Notification.DOCUMENT_DUE_SOON],
        "overdue": counts[Notification.DOCUMENT_OVERDUE],
        "already_sent": counts["already_sent"],
        "skipped_inactive_creator": counts["skipped_inactive_creator"],
    }
