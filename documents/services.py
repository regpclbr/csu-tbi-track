"""Business logic for activity documents, the checklist and reminders.

Views (and the Django admin) stay thin and call the functions in this module.

Approved copies (ASSUMPTIONS 432, NAVIGATU: only approved paper documents are uploaded; the system monitors what is
missing): an activity document of a type WITHOUT DOCUMENT_TYPE.needs_review (every type by default) is saved as Approved at
upload (reviewed_by empty = System, reviewed_at = the upload time), indexed for the assistant at once, nobody is notified,
and its uploader or a member with can_approve_documents may replace or delete it (guards: the creation-document rule, a
document cited by the AI). One History row ("Documents") and one documents.audit line per upload, replace and delete.

Review flow, ONLY for types with needs_review switched on and for certificate evidence (DOCUMENT_STATUS has only For
Review and Approved; there is no Rejected):
- Upload: any active staff member. The document starts For Review (reviewed_at empty). Every active member who
  can approve documents gets a "waiting for your review" notification, except the uploader.
- Approve: a member with can_approve_documents (ASSUMPTIONS 344), also for their own upload. Approved is final.
- Return: a member with can_approve_documents, never for their own upload (they replace or delete it instead).
  It stays For Review, but reviewed_by/reviewed_at are set, which marks it "returned"; the uploader gets a
  notification with the reason and must replace the file.
- Replace: the uploader or a member with can_approve_documents (ASSUMPTIONS 437: the Director edits other members'
  work), For Review only. Clears reviewed_by/reviewed_at (waiting for a reviewer again) and notifies the approvers
  again, except the member who replaced it.
- Delete: the uploader or can_approve_documents, For Review only (the file is removed too). Approved documents of a
  reviewed type are never deleted. The only Activity Design / Request Letter of a scheduled activity created with them
  is never deleted (ASSUMPTIONS 405).
- Unapprove (ASSUMPTIONS 437): can_approve_documents takes back an approval with a reason: Approved -> Returned, its
  AI chunks removed, the uploader notified, History + documents.audit. Approved copies are never "unapproved" (they
  are replaced or deleted); certificate evidence keeps its own flow.
- Rename (ASSUMPTIONS 437): the uploader or can_approve_documents changes the title in any state (History +
  documents.audit; an indexed document is indexed again, its title goes with the embeddings).
Certificate evidence: replace / delete stay with its uploader (Step 9).

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
from accounts.permissions import (
    Permission,
    has_any_permission,
    has_permission,
    has_permission_for,
    require_permission,
    require_permission_for,
    require_staff_member,
)
from activities.models import Activity, ActivityStatus
from activities.services import PageResult, _paginate
from assistant.indexing import MISSING, RETITLE, needs_scan_tick, queue_indexing, with_searchable
from notifications.models import Notification

from .models import Document, DocumentStatus, DocumentType

logger = logging.getLogger("documents.reminders")
audit_log = logging.getLogger("documents.audit")  # who uploaded, replaced, deleted, approved or returned (never contents)

APPROVED_COPY_NOTE = "uploaded as an approved copy"  # History and documents.audit (ASSUMPTIONS 432)
SAVED_SUBMISSION_NOTE = "saved from an approved submission"  # History (ASSUMPTIONS 437)


def needs_review(document_type: DocumentType, related_certificate=None) -> bool:
    """The one rule (ASSUMPTIONS 432): True when an upload of this type waits for the Director's review (the type's
    needs_review switch in the Django admin) or is certificate evidence (its own flow, Step 9). False = an approved
    copy: saved as Approved at upload."""
    return related_certificate is not None or document_type.needs_review


def review_in_use() -> bool:
    """Whether the review states matter at all right now: some type needs review, or older documents are still waiting
    or returned. The Documents page shows the "Waiting" / "Returned" cards and filters only then (display only)."""
    return (DocumentType.objects.filter(needs_review=True).exists()
            or Document.objects.activity_documents().filter(status__status_name=DocumentStatus.FOR_REVIEW).exists())


def _document_label(document: Document) -> str:
    return f'{document.document_type.name} "{document.title[:120]}"'


def _approve_copy(document: Document, now: datetime | None) -> None:
    """Mark an (unsaved or locked) document as an approved copy: Approved by System at the upload time."""
    document.status = DocumentStatus.objects.get(status_name=DocumentStatus.APPROVED)
    document.reviewed_by = None
    document.reviewed_at = now or timezone.now()


def _queue_index_on_commit(document: Document, mode: str = MISSING) -> None:
    """Index an activity document for the assistant once the change is saved: every review state (ASSUMPTIONS 438;
    robust: a problem queueing never undoes the upload). Certificate evidence is never indexed."""
    if document.related_certificate_id is None:
        document_id = document.pk
        transaction.on_commit(lambda: queue_indexing(document_id, mode), robust=True)

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
    auto_schedule: bool = True,
    approved_by: TBIMember | None = None,
    approved_at: datetime | None = None,
) -> Document:
    """Any active staff member uploads a document for an activity, in any activity status
    (including Conducted: the accomplishment report is due after the end date). An approved copy (ASSUMPTIONS 432: the
    type does not need review) is saved as Approved by System, written to History and documents.audit and queued for
    the assistant; otherwise (needs_review, certificate evidence) it starts For Review and the approvers are told.

    `related_certificate` is set only by certificates.services.upload_evidence (Step 9).
    In the same transaction a Draft created with its creation documents moves to Scheduled once both the Activity Design
    and the Request Letter are uploaded (schedule_when_documents_complete, ASSUMPTIONS 431); `document.scheduled_activity`
    says whether it did. `auto_schedule` False: create_activity_with_documents, which schedules the new activity itself.
    `approved_by` / `approved_at` (ASSUMPTIONS 437, only approvals.services.save_to_documents): the file of a submission
    the Director already approved; for a type that needs review it is saved Approved in the Director's name (no second
    review, nobody notified). Types without review are approved copies as usual."""
    require_staff_member(member)
    if not document_type.is_active:
        raise ValidationError({"document_type": ["This document type is not active."]})
    problems = {}
    if message := title_error(document_type, title):
        problems["title"] = [message]
    try:
        extension = validate_upload(file)
        if message := file_type_error(document_type, extension):
            problems["file"] = [message]
    except ValidationError as error:
        problems["file"] = error.messages
    if problems:  # the title and the file are reported together
        raise ValidationError(problems)
    title = (title or "").strip() or document_type.name

    # ASSUMPTIONS 472: while the Director has a document of this type returned (or unapproved) on this activity, a new
    # upload of the type is checked again too, even when the type itself is not reviewed.
    reviewed = approved_by is None and (needs_review(document_type, related_certificate)
                                        or (related_certificate is None and in_review_again(activity, document_type)))
    document = Document(
        activity=activity,
        document_type=document_type,
        uploaded_by=member,
        status=DocumentStatus.objects.get(status_name=DocumentStatus.FOR_REVIEW),
        title=title,
        related_certificate=related_certificate,
    )
    if not reviewed:  # ASSUMPTIONS 432: an approved copy
        _approve_copy(document, now)
        if approved_by is not None and document_type.needs_review:  # ASSUMPTIONS 437: approved as a submission
            document.reviewed_by, document.reviewed_at = approved_by, approved_at or now or timezone.now()
    _save_file(document, file, extension, now)
    try:
        with transaction.atomic():  # the document and its notifications / History are saved together
            document.save()
            if reviewed:
                notify_approvers(document, member)
            else:
                from activities.services import write_document_history  # activities.services imports this module

                note = SAVED_SUBMISSION_NOTE if approved_by is not None else APPROVED_COPY_NOTE
                write_document_history(activity, member, "", f"{_document_label(document)} {note}")
                audit_log.info("Document uploaded as an approved copy: document_id=%s activity_id=%s by member_id=%s%s",
                               document.pk, activity.pk, member.pk, " (approved submission)" if approved_by else "")
            _queue_index_on_commit(document)  # every review state is indexed (ASSUMPTIONS 438)
            document.scheduled_activity = (
                auto_schedule and related_certificate is None and is_creation_type(activity, document_type)
                and schedule_when_documents_complete(activity, member))
    except Exception:
        _delete_stored_file(document.file_path.name)  # don't leave an orphan file behind
        raise
    return document


def in_review_again(activity: Activity, document_type: DocumentType) -> bool:
    """ASSUMPTIONS 472 (owner): the Director returned or unapproved a document of this type on this activity and it is
    not approved again yet (a For Review activity document of the type). Uploads and replacements of the type then wait
    for the Director's check, also for a type without review (approved copies)."""
    return (Document.objects.activity_documents()
            .filter(activity=activity, document_type=document_type, status__status_name=DocumentStatus.FOR_REVIEW)
            .exists())


OTHER_TITLE_REQUIRED = "Give this document a title, for example: MOA with ABC Corp."


def title_error(document_type: DocumentType | None, title: str) -> str | None:
    """The one title rule (ASSUMPTIONS 386): at most 255 characters; required for the catch-all type "Other" (its
    name says nothing about the file); optional for every other type (empty = the type's name)."""
    title = (title or "").strip()
    if len(title) > 255:
        return "The title can be at most 255 characters."
    if not title and document_type is not None and document_type.name == DocumentType.OTHER:
        return OTHER_TITLE_REQUIRED
    return None


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

    `activity_error`: the activity's own ValidationError (or None); `row_errors`: {row index: {field: message}}. (A refused Scheduled is no error since ASSUMPTIONS 431: the activity
    is then saved as Draft.)"""

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


# --- Documents required when an activity is created (ASSUMPTIONS 405, NAVIGATU) ---------------------------------


def creation_required_types() -> QuerySet:
    """The types every new activity must be created with (settings.DOCUMENT_TYPES_REQUIRED_AT_CREATION: the Activity
    Design and the Request Letter): only active, required types that can be uploaded at creation. A type the admin
    deactivates or makes optional simply drops out of the rule."""
    return types_allowed_at_creation().filter(is_required=True, name__in=settings.DOCUMENT_TYPES_REQUIRED_AT_CREATION)


def is_creation_type(activity: Activity, document_type: DocumentType) -> bool:
    """True when the creation rule applies to this (activity, type) pair: the activity was created with its creation
    documents (creation_documents_required) and the type is one of them (active and required, as in
    creation_required_types). Old activities keep the current rules."""
    return (activity.creation_documents_required and document_type.is_active and document_type.is_required
            and document_type.name in settings.DOCUMENT_TYPES_REQUIRED_AT_CREATION)


def _the_names(names: list[str]) -> str:
    """["Activity Design", "Request Letter"] -> "the Activity Design and the Request Letter"."""
    names = [f"the {name}" for name in names]
    return names[0] if len(names) == 1 else ", ".join(names[:-1]) + " and " + names[-1]


def missing_creation_types(activity: Activity) -> list[str]:
    """The creation-required types with no document uploaded for this activity (any review state), by name."""
    types = list(creation_required_types().order_by("name"))
    uploaded = set(
        Document.objects.activity_documents().filter(activity=activity, document_type__in=types)
        .values_list("document_type_id", flat=True)
    )
    return [document_type.name for document_type in types if document_type.pk not in uploaded]


def scheduling_refused_reason(activity: Activity) -> str | None:
    """Why this Draft cannot be Scheduled yet (activities.services._move_status calls it for activities with
    creation_documents_required), or None. Uploaded is enough: waiting, returned or approved."""
    if not activity.creation_documents_required:
        return None
    missing = missing_creation_types(activity)
    if not missing:
        return None
    return f"Upload {_the_names(missing)} before scheduling this activity (Documents tab)."


SCHEDULED_AUTOMATICALLY_NOTE = "both documents uploaded"  # History: "Draft -> Scheduled (both documents uploaded)"


def schedule_when_documents_complete(activity: Activity, uploader: TBIMember) -> bool:
    """ASSUMPTIONS 431 (NAVIGATU: the documents decide the status, not a choice): a Draft created with its creation
    documents (creation_documents_required) moves to Scheduled by the System as soon as both the Activity Design and the
    Request Letter are uploaded (any review state, the rule of scheduling_refused_reason). The one transition rule
    (activities.services.schedule_automatically -> _move_status) in a savepoint; a refusal is logged and the activity
    stays Draft (the upload is never refused because of it). The creator is told in-app unless they uploaded it
    themselves (notifications.services.notify_creator_scheduled). Registration still opens only once the registration
    form is marked ready (item 409). Returns True when it moved. Call it inside the upload's transaction."""
    from activities.services import ActivityError, schedule_automatically  # activities.services imports this module
    from notifications.services import notify_creator_scheduled

    if not activity.creation_documents_required or activity.status.name != ActivityStatus.DRAFT:
        return False
    if missing_creation_types(activity):
        return False
    try:
        moved = schedule_automatically(activity, SCHEDULED_AUTOMATICALLY_NOTE)
    except ActivityError as error:
        logger.info("Draft not scheduled automatically: activity_id=%s reason=%s", activity.pk, error)
        return False
    if moved:
        notify_creator_scheduled(activity, uploader)
    return moved


def _delete_refused_reason(document: Document) -> str | None:
    """The only Activity Design / Request Letter of a non-Draft activity created with them cannot be deleted: the
    activity would show it as missing again. Replacing the file stays allowed. (A document an AI answer cites is refused
    by delete_document itself, CITED_REFUSED: one query per document would be too many for the lists' buttons.)"""
    activity = document.activity
    if document.related_certificate_id or not is_creation_type(activity, document.document_type):
        return None
    if activity.status.name == ActivityStatus.DRAFT:
        return None
    others = Document.objects.activity_documents().filter(activity=activity, document_type=document.document_type)
    if others.exclude(pk=document.pk).exists():
        return None
    return (f"This is the activity's only {document.document_type.name} and the activity is {activity.status.name}. "
            "Replace the file instead.")


def creation_title_error(document_type: DocumentType | None, title: str) -> str | None:
    """ASSUMPTIONS 410 (NAVIGATU): on the New activity form the Activity Design and the Request Letter need a real
    title (blank or spaces only is refused); the length limit is title_error's. The Documents tab keeps title_error
    alone (optional except for "Other")."""
    if document_type is None or (title or "").strip():
        return None
    if not creation_required_types().filter(pk=document_type.pk).exists():
        return None
    return f"Enter a title for the {document_type.name}."


def check_new_documents(documents: list[NewDocument]) -> dict[int, dict[str, str]]:
    """Every row's type, file (validate_upload: real content, size) and title (title_error, plus creation_title_error
    for the Activity Design and the Request Letter), before anything is saved."""
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
        if message := title_error(row.document_type, row.title) or creation_title_error(row.document_type, row.title):
            row_errors["title"] = message
        if row_errors:
            errors[index] = row_errors
    return errors


def create_activity_with_documents(
    member: TBIMember, data: dict, documents: list[NewDocument], now: datetime | None = None,
    signatories: dict | None = None,
    prepare_certificates: bool = False, certificate_texts: dict | None = None,
) -> Activity:
    """Create the activity (activities.services.create_activity), upload its documents (upload_document, the
    same rules as the Documents tab) in one transaction: all or nothing. The activity is saved with
    creation_documents_required=True (ASSUMPTIONS 405), so Draft -> Scheduled always needs the Activity Design and
    the Request Letter uploaded. Every row is optional (ASSUMPTIONS 417).

    The status (ASSUMPTIONS 431, replaces 417's "Create as" choice): with both the Activity Design and the Request
    Letter among the rows the activity is Scheduled at once, through change_status in the same transaction (the one
    transition rule; its own savepoint). If that move is refused for any reason, the activity, its documents and
    everything else stay saved as Draft and the reason is `activity.not_scheduled_reason` (for the success message;
    "" otherwise). Without both it is created as Draft.

    Errors of the activity and of every row are raised together as NewActivityError (the Scheduled check runs only
    once they are valid: it needs the saved documents). Files already stored when something fails are deleted again.
    `prepare_certificates` / `certificate_texts` (ASSUMPTIONS 420): "Prepare the certificates now" (see
    activities.services.save_new_activity); a refused text is part of activity_error ("<type>-<field>" keys).
    upload_document's review notifications are rows in this same transaction and it creates no email or task
    (indexing is queued only on approval, on commit), so a refused create never tells anyone anything."""
    from activities.services import ActivityError, change_status, create_activity  # documents imports activities

    require_permission(member, Permission.CREATE_ACTIVITY)
    if len(documents) > MAX_DOCUMENTS_AT_CREATION:
        raise ValidationError({"documents": [f"Add at most {MAX_DOCUMENTS_AT_CREATION} documents here."]})
    row_errors = check_new_documents(documents)
    activity_error = None
    not_scheduled_reason = ""
    stored: list[str] = []
    committed = False
    try:
        with transaction.atomic():
            try:
                activity = create_activity(member, data, now=now, signatories=signatories,
                                           creation_documents_required=True,
                                           prepare_certificates=prepare_certificates,
                                           certificate_texts=certificate_texts)
            except ValidationError as error:
                activity_error = error
                raise _RollBack from error
            if row_errors:
                raise _RollBack
            for index, row in enumerate(documents):
                try:
                    document = upload_document(member, activity, row.document_type, row.file, row.title, now=now,
                                               auto_schedule=False)
                except ValidationError as error:  # e.g. the type was deactivated a moment ago
                    found = error.message_dict if hasattr(error, "error_dict") else {"file": error.messages}
                    row_errors[index] = {key: messages[0] for key, messages in found.items()}
                    raise _RollBack from error
                stored.append(document.file_path.name)
            if not missing_creation_types(activity):
                # ASSUMPTIONS 431 (409): both documents -> Scheduled by the one transition rule (change_status ->
                # _move_status), so History shows "Draft" then "Draft -> Scheduled" by the creator. change_status is its
                # own savepoint: a refusal keeps the activity and its uploads as Draft. Registration still stays closed
                # until the registration form exists AND is marked ready (registration_form_ready).
                try:
                    change_status(member, activity, ActivityStatus.SCHEDULED)
                except ActivityError as error:
                    not_scheduled_reason = str(error)
        committed = True
    except _RollBack:
        pass
    finally:
        if not committed:
            for name in stored:
                _delete_stored_file(name)
    if not committed:
        raise NewActivityError(activity_error, row_errors)
    activity.not_scheduled_reason = not_scheduled_reason  # not a column: for the success message only
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


def is_approved_copy(document: Document) -> bool:
    """An Approved document of a type without review (ASSUMPTIONS 432): its uploader or the Director may still replace
    or delete it. Approved documents of a reviewed type and certificate evidence stay final."""
    return (document.status.status_name == DocumentStatus.APPROVED
            and not needs_review(document.document_type, document.related_certificate))


def _may_change(member: TBIMember, document: Document) -> bool:
    """Who may replace / delete: the uploader; a member with can_approve_documents (the Director) also for any approved
    copy (ASSUMPTIONS 432) and any activity document still waiting or returned (ASSUMPTIONS 437: the Director edits
    other members' work). Certificate evidence: its uploader only (its own flow)."""
    if document.uploaded_by_id == member.pk:
        return True
    if not has_permission(member, Permission.APPROVE_DOCUMENTS):
        return False
    return is_approved_copy(document) or (document.related_certificate_id is None
                                          and document.status.status_name == DocumentStatus.FOR_REVIEW)


def _ensure_can_change(member: TBIMember, document: Document, action: str) -> None:
    if document.status.status_name != DocumentStatus.FOR_REVIEW and not is_approved_copy(document):
        raise DocumentError(f"An approved document cannot be {action}d. Upload a new document instead.")
    if not _may_change(member, document):
        if document.related_certificate_id is not None:
            raise PermissionDenied(f"Only the member who uploaded this document can {action} it.")
        raise PermissionDenied(f"Only the member who uploaded this document or the Director can {action} it.")


CITED_REFUSED = ("This document is cited in an AI Assistant answer, so it cannot be deleted. Replace the file "
                 "instead.")


def _cited(document: Document) -> bool:
    """An AI answer cites it (AI_MESSAGE_SOURCE, PROTECT): deleting it would break that answer's sources."""
    return document.ai_citations.exists()


def replace_document_file(member: TBIMember, document: Document, file: UploadedFile | None, now: datetime | None = None) -> Document:
    """Replace the file of a document: the uploader of a For Review document (usually after it was returned), or the
    uploader or a member with can_approve_documents for an approved copy (ASSUMPTIONS 432).

    Same row, title and type. An approved copy stays Approved by System; a waiting or returned document (also of a type
    without review that the Director returned or unapproved, ASSUMPTIONS 472) clears reviewed_by/reviewed_at, so it
    waits for the Director's check again and the approvers are told. Either way the assistant chunks are removed and it is indexed again (ASSUMPTIONS 438), and a "Read this scan
    with AI" tick is cleared. History + documents.audit for every replace.
    The old file is deleted only after the change is saved.
    """
    from activities.services import write_document_history  # activities.services imports this module

    require_staff_member(member)
    extension = validate_upload(file)
    if message := file_type_error(document.document_type, extension):
        raise ValidationError({"file": [message]})
    with transaction.atomic():
        document = _locked(document)
        _ensure_can_change(member, document, "replace")
        # For Review (waiting or returned) = checked again (ASSUMPTIONS 472); only an approved copy stays approved.
        reviewed = document.status.status_name == DocumentStatus.FOR_REVIEW
        old_name = document.file_path.name
        _save_file(document, file, extension, now)
        document.ai_scan_allowed_by = document.ai_scan_allowed_at = None  # a new file needs a new tick (ASSUMPTIONS 438)
        if reviewed:
            document.reviewed_by = None
            document.reviewed_at = None
        else:
            _approve_copy(document, now)
        try:
            with transaction.atomic():
                document.save()
                document.embeddings.all().delete()  # never answer from the old file; indexed again below
                _queue_index_on_commit(document)
                if reviewed:
                    notify_approvers(document, member)
                if document.related_certificate_id is None:
                    write_document_history(document.activity, member, "", f"{_document_label(document)} replaced"
                                           + ("" if reviewed else f" ({APPROVED_COPY_NOTE})"))
                audit_log.info("Document replaced: document_id=%s by member_id=%s", document.pk, member.pk)
        except Exception:
            _delete_stored_file(document.file_path.name)
            raise
        transaction.on_commit(lambda: _delete_stored_file(old_name))
    return document


def delete_document(member: TBIMember, document: Document) -> None:
    """Delete a document and its file: the uploader of a For Review document, or the uploader or a member with
    can_approve_documents for an approved copy (ASSUMPTIONS 432). Refused for an Approved document of a reviewed type,
    the only Activity Design / Request Letter of a non-Draft activity (_delete_refused_reason) and a document an AI
    answer cites. History + documents.audit."""
    from activities.services import write_document_history  # activities.services imports this module

    require_staff_member(member)
    with transaction.atomic():
        document = _locked(document)
        _ensure_can_change(member, document, "delete")
        if reason := _delete_refused_reason(document) or (CITED_REFUSED if _cited(document) else None):
            raise DocumentError(reason)
        name = document.file_path.name
        if document.related_certificate_id is None:
            write_document_history(document.activity, member, f"{_document_label(document)}",
                                   f"{_document_label(document)} deleted")
        audit_log.info("Document deleted: document_id=%s by member_id=%s", document.pk, member.pk)
        document.delete()
        transaction.on_commit(lambda: _delete_stored_file(name))


# --- Review: approve and return ------------------------------------------------------------


RETURN_OWN_REFUSED = "You cannot return a document you uploaded. Replace or delete it instead."


ASSIGNEE_OWN_UPLOAD = "You cannot review your own upload through an assignment (separation of duties)."


def _ensure_can_review(member: TBIMember, document: Document):
    """Reviewer rule (ASSUMPTIONS 344, pending TBI validation): can_approve_documents, or this document's "Review the
    document" assignment (ASSUMPTIONS 437). Approving your own upload is allowed with the flag (reviewed_by is then the
    uploader), never through an assignment. Returns the assignment that allowed it (or None)."""
    assignment = require_permission_for(member, Permission.APPROVE_DOCUMENTS, document)
    if assignment is not None and document.uploaded_by_id == member.pk:
        raise DocumentError(ASSIGNEE_OWN_UPLOAD)
    if document.status.status_name != DocumentStatus.FOR_REVIEW:
        raise DocumentError("This document is already approved.")
    if document.reviewed_at is not None:
        raise DocumentError("This document was returned to the uploader. It can be reviewed after the file is replaced.")
    return assignment


def approve_document(member: TBIMember, document: Document, now: datetime | None = None) -> Document:
    """Approve a document waiting for review. Status, reviewed_by and reviewed_at change together."""
    with transaction.atomic():
        document = _locked(document)
        assignment = _ensure_can_review(member, document)
        document.status = DocumentStatus.objects.get(status_name=DocumentStatus.APPROVED)
        document.reviewed_by = member
        document.reviewed_at = now or timezone.now()
        document.save(update_fields=["status", "reviewed_by", "reviewed_at", "updated_at"])
        audit_log.info(
            "Document approved: document_id=%s by member_id=%s%s%s", document.pk, member.pk,
            " (own upload)" if document.uploaded_by_id == member.pk else "",
            f" on behalf of member_id={assignment.assigned_by_id}" if assignment else "",
        )
        from approvals.assignments import complete_for  # approvals imports this module

        complete_for("document_review", document, member, document.reviewed_at)

        # Step 8: index it for the assistant once the approval is saved (Django-Q2 task). Since ASSUMPTIONS 438 it was
        # usually indexed at upload already: the task then does nothing (mode MISSING); otherwise this retries it.
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
    reason = clean_reason(reason)

    with transaction.atomic():
        document = _locked(document)
        assignment = _ensure_can_review(member, document)
        if document.uploaded_by_id == member.pk:
            raise DocumentError(RETURN_OWN_REFUSED)
        document.reviewed_by = member
        document.reviewed_at = now or timezone.now()
        document.save(update_fields=["reviewed_by", "reviewed_at", "updated_at"])
        audit_log.info("Document returned: document_id=%s by member_id=%s%s", document.pk, member.pk,
                       f" on behalf of member_id={assignment.assigned_by_id}" if assignment else "")
        from approvals.assignments import complete_for  # approvals imports this module

        complete_for("document_review", document, member, document.reviewed_at)
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


# --- Unapprove and rename (ASSUMPTIONS 437) -------------------------------------------------------

UNAPPROVE_EVIDENCE_REFUSED = "Certificate evidence keeps its own review flow and cannot be unapproved here."
UNAPPROVE_NOT_APPROVED = "Only an approved document can be unapproved."
UNAPPROVE_CITED = ("An AI Assistant answer cites this document, so its approval cannot be taken back. Ask the uploader "
                   "to upload a corrected document instead.")
UNAPPROVED_HISTORY = "unapproved (returned to the uploader)"


def unapprove_refused_reason(document: Document) -> str | None:
    """Why the approval of `document` cannot be taken back, or None (the one rule). Any Approved activity document: since
    ASSUMPTIONS 472 (owner) also an approved copy, which the Director returns this way so that the uploader's new file
    is checked again. Never certificate evidence, never a document an AI answer cites (the answer's sources would point
    at an unapproved file)."""
    if document.related_certificate_id is not None:
        return UNAPPROVE_EVIDENCE_REFUSED
    if document.status.status_name != DocumentStatus.APPROVED:
        return UNAPPROVE_NOT_APPROVED
    if _cited(document):
        return UNAPPROVE_CITED
    return None


def clean_reason(reason: str, field: str = "reason") -> str:
    """A return / unapprove reason: required, at most RETURN_REASON_MAX_LENGTH characters (also for submissions)."""
    reason = (reason or "").strip()
    if not reason:
        raise ValidationError({field: ["Give a reason."]})
    if len(reason) > RETURN_REASON_MAX_LENGTH:
        raise ValidationError({field: [f"The reason can be at most {RETURN_REASON_MAX_LENGTH} characters."]})
    return reason


def unapprove_document(member: TBIMember, document: Document, reason: str, now: datetime | None = None) -> Document:
    """Take back the approval of a document, also an approved copy (ASSUMPTIONS 472; can_approve_documents, a reason
    required): it becomes Returned (For Review, reviewed_by = this member, reviewed_at = now), so it counts as not done in the
    checklist again (it can become overdue; reminders already sent are not repeated) and the uploader replaces the
    file (replace_document_file / a new upload: it then waits for the Director's check again). Its AI chunks stay (every review state is searchable since ASSUMPTIONS 438; its source card then says
    "Returned"). An activity that was Scheduled
    automatically stays Scheduled. One History row ("Documents"), a documents.audit line and a notification to the
    uploader (unless they unapproved it themselves)."""
    from activities.services import write_document_history  # activities.services imports this module

    require_permission(member, Permission.APPROVE_DOCUMENTS)
    reason = clean_reason(reason)
    with transaction.atomic():
        document = _locked(document)
        if refused := unapprove_refused_reason(document):
            raise DocumentError(refused)
        document.status = DocumentStatus.objects.get(status_name=DocumentStatus.FOR_REVIEW)
        document.reviewed_by = member
        document.reviewed_at = now or timezone.now()
        document.save(update_fields=["status", "reviewed_by", "reviewed_at", "updated_at"])
        write_document_history(document.activity, member, f"{_document_label(document)} approved",
                               f"{_document_label(document)} {UNAPPROVED_HISTORY}: {reason}")
        audit_log.info("Document unapproved: document_id=%s by member_id=%s", document.pk, member.pk)
        uploader = document.uploaded_by
        if uploader.pk != member.pk and uploader.is_active:
            Notification.objects.create(
                member=uploader, activity=document.activity, document_type=document.document_type,
                type=Notification.DOCUMENT_UNAPPROVED,
                message=(f'The approval of your {document.document_type.name} "{document.title[:80]}" for '
                         f'"{document.activity.title[:80]}" was taken back by {member.name[:60]}: {reason}')[:500],
            )
    return document


def may_rename(member: TBIMember, document: Document) -> bool:
    """The uploader, or a member with can_approve_documents (the Director edits other members' work, ASSUMPTIONS 437)."""
    return document.uploaded_by_id == member.pk or has_permission(member, Permission.APPROVE_DOCUMENTS)


def rename_document(member: TBIMember, document: Document, title: str) -> Document:
    """Change a document's title, in any state (the file and its approval stay). The one title rule (title_error, also
    "Other" needs a title); an empty title becomes the type's name, like an upload. History ("Documents": old -> new
    title) for activity documents and a documents.audit line. An indexed document is indexed again (the title goes
    with its embeddings). Refused for everyone else (PermissionDenied)."""
    from activities.services import write_document_history  # activities.services imports this module

    require_staff_member(member)
    with transaction.atomic():
        document = _locked(document)
        if not may_rename(member, document):
            raise PermissionDenied("Only the member who uploaded this document or the Director can rename it.")
        if message := title_error(document.document_type, title):
            raise ValidationError({"title": [message]})
        title = (title or "").strip() or document.document_type.name
        if title == document.title:
            return document
        old_label = _document_label(document)
        document.title = title
        document.save(update_fields=["title", "updated_at"])
        if document.related_certificate_id is None:
            write_document_history(document.activity, member, old_label, f"{_document_label(document)} (renamed)")
            _queue_index_on_commit(document, RETITLE)  # the same text, embedded with the new title (ASSUMPTIONS 438)
        audit_log.info("Document renamed: document_id=%s by member_id=%s", document.pk, member.pk)
    return document


# --- "Read this scan with AI" (ASSUMPTIONS 438) -----------------------------------------------------------------

SCAN_EXTENSIONS = ("pdf", "jpg", "png")
SCAN_TICK_NOT_NEEDED = "Only a scanned Attendance Sheet or Confirmation of Attendance needs this tick."
SCAN_TICK_DONE = "This file may already be read with AI."
SCAN_TICK_HISTORY = "may be read with AI (scan ticked)"


def ai_scan_refused_reason(document: Document) -> str | None:
    """Why "Read this scan with AI" is not offered for this file, or None. Only an activity document of a type in
    settings.ASSISTANT_SCAN_TICK_TYPES (handwriting and signatures), a PDF / JPG / PNG, not ticked yet."""
    if document.related_certificate_id is not None or not needs_scan_tick(document):
        return SCAN_TICK_NOT_NEEDED
    if document.file_extension.lower() not in SCAN_EXTENSIONS:
        return SCAN_TICK_NOT_NEEDED
    if document.ai_scan_allowed_at is not None:
        return SCAN_TICK_DONE
    return None


def may_allow_ai_scan(member: TBIMember, document: Document) -> bool:
    """The uploader, a member who creates activities (they generate and upload these forms) or an approver."""
    return document.uploaded_by_id == member.pk or has_any_permission(
        member, Permission.CREATE_ACTIVITY, Permission.APPROVE_DOCUMENTS)


def allow_ai_scan(member: TBIMember, document: Document, now: datetime | None = None) -> Document:
    """Tick "Read this scan with AI" for ONE file (owner decision 2026-11-25): the scan, with its handwriting and
    signatures, may now be sent to Gemini once to be read (OCR) and indexed. Who and when are stored on the document;
    one History row ("Documents") and a documents.audit line (IDs only). A replaced file needs a new tick."""
    from activities.services import write_document_history  # activities.services imports this module

    require_staff_member(member)
    with transaction.atomic():
        document = _locked(document)
        if not may_allow_ai_scan(member, document):
            raise PermissionDenied("Only the uploader, a member who creates activities or an approver can tick this.")
        if refused := ai_scan_refused_reason(document):
            raise DocumentError(refused)
        document.ai_scan_allowed_by = member
        document.ai_scan_allowed_at = now or timezone.now()
        document.save(update_fields=["ai_scan_allowed_by", "ai_scan_allowed_at", "updated_at"])
        write_document_history(document.activity, member, "", f"{_document_label(document)} {SCAN_TICK_HISTORY}")
        audit_log.info("Document scan may be read with AI: document_id=%s by member_id=%s", document.pk, member.pk)
        _queue_index_on_commit(document)
    return document


def remove_saved_submission_document(member: TBIMember, document: Document, reason: str) -> None:
    """Delete the activity document that was saved from a submission whose approval is taken back (ASSUMPTIONS 437,
    approvals.services.unapprove_submission, inside its transaction). The usual guards apply (the only Activity Design /
    Request Letter of a non-Draft activity, a document an AI answer cites): refused = DocumentError, so the unapprove
    is refused as a whole. History + documents.audit; the file is removed after the commit."""
    from activities.services import write_document_history  # activities.services imports this module

    document = _locked(document)
    if refused := _delete_refused_reason(document) or (CITED_REFUSED if _cited(document) else None):
        raise DocumentError(f"The document saved to the activity cannot be removed: {refused}")
    name = document.file_path.name
    write_document_history(document.activity, member, _document_label(document),
                           f"{_document_label(document)} deleted (its submission was unapproved: {reason})")
    audit_log.info("Document deleted with its unapproved submission: document_id=%s by member_id=%s",
                   document.pk, member.pk)
    document.delete()
    transaction.on_commit(lambda: _delete_stored_file(name))


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
    # Approve / Return for any document waiting for a reviewer: a type that needs review (ASSUMPTIONS 432), or one the
    # Director returned / unapproved and the uploader replaced (472). A returned one waits for its uploader.
    can_review = (for_review and document.reviewed_at is None
                  and has_permission_for(member, Permission.APPROVE_DOCUMENTS, document))  # or its assignment (437)
    if can_review and is_uploader and not has_permission(member, Permission.APPROVE_DOCUMENTS):
        can_review = False  # an assignee never reviews their own upload
    changeable = (for_review or is_approved_copy(document)) and _may_change(member, document)
    # ASSUMPTIONS 437, 472: Unapprove for any Approved activity document, approved copies too (an AI citation is checked
    # on the click, like Delete, so the lists keep a fixed number of queries); Rename for the uploader or the Director.
    unapprovable = (document.related_certificate_id is None and not for_review
                    and has_permission(member, Permission.APPROVE_DOCUMENTS))
    return {
        "approve": can_review,  # also the member's own upload (ASSUMPTIONS 344)
        "return": can_review and not is_uploader,
        "replace": changeable,
        "delete": changeable and _delete_refused_reason(document) is None,
        "unapprove": unapprovable,
        "rename": may_rename(member, document),
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
    counts_now: bool = True  # False = a payment document before Conducted: never overdue, no reminder (ASSUMPTIONS 425)

    @property
    def is_payment(self) -> bool:
        return is_payment_type(self.document_type)


# --- Payment documents for the Supply Office (ASSUMPTIONS 425) -------------------------------------------------------


def is_payment_type(document_type: DocumentType) -> bool:
    """One of settings.DOCUMENT_TYPES_FOR_PAYMENT (Activity Report, Attendance Sheet, Event Photos)."""
    return document_type.name in settings.DOCUMENT_TYPES_FOR_PAYMENT


def type_applies(document_type: DocumentType, activity: Activity) -> bool:
    """Whether a required type is required for this activity: a type in DOCUMENT_TYPES_REQUIRED_FROM (the types new in
    item 425, and the Confirmation of Attendance of item 434) only for activities ending on or after its date; every other
    type always. reports.metrics._applies is the same rule in SQL."""
    since = settings.DOCUMENT_TYPES_REQUIRED_FROM.get(document_type.name)
    return since is None or activity.end_date >= since


def counts_now(document_type: DocumentType, activity: Activity) -> bool:
    """A payment document counts (overdue, due soon, Needs attention, reminders) only once the activity is Conducted;
    every other required type always. reports.metrics._counts_now is the same rule in SQL."""
    return not is_payment_type(document_type) or activity.status.name == ActivityStatus.CONDUCTED


IMAGES_ONLY_ERROR = "{name}: only JPG and PNG pictures can be uploaded."


def file_type_error(document_type: DocumentType, extension: str) -> str | None:
    """Types in DOCUMENT_TYPES_IMAGES_ONLY (Event Photos) take pictures only (ASSUMPTIONS 425)."""
    if document_type.name in settings.DOCUMENT_TYPES_IMAGES_ONLY and extension not in ("jpg", "png"):
        return IMAGES_ONLY_ERROR.format(name=document_type.name)
    return None


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
    counts = counts_now(document_type, activity)
    if not counts:
        overdue = False  # a payment document before Conducted (ASSUMPTIONS 425)
    elif is_creation_type(activity, document_type):
        # ASSUMPTIONS 405: uploaded at creation, so overdue only while nothing is uploaded (only possible in Draft);
        # a file waiting for the Director is never overdue. reports.metrics._overdue is the same rule in SQL.
        overdue = today > due and state == MISSING
    else:
        overdue = today > due and state != APPROVED
    return ChecklistItem(document_type, due, state, overdue, documents, counts)


def document_checklist(activity: Activity, now: datetime | None = None) -> dict:
    """Every active required document type with its due date, state and overdue flag, plus the
    optional (non-required) active types with their uploaded documents. A required type that does not apply to this
    activity (type_applies: a payment type new in item 425 on an older activity) is listed with the optional ones.
    An INACTIVE type is listed with the optional ones only when this activity has documents of it (ASSUMPTIONS 430:
    old "Accomplishment Report" uploads stay visible and downloadable); it is never required."""
    today = today_in_manila(now)
    by_type = _documents_by_type(activity)
    required, optional = [], []
    types = DocumentType.objects.filter(Q(is_active=True) | Q(pk__in=list(by_type))).order_by("name")
    for document_type in types:
        documents = by_type.get(document_type.pk, [])
        if not document_type.is_active:
            optional.append({"document_type": document_type, "documents": documents})
        elif document_type.is_required and type_applies(document_type, activity):
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
    items = [_required_item(document_type, activity, by_type.get(document_type.pk, []), today)
             for document_type in types if type_applies(document_type, activity)]
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
    by_activity = documents_by_activity([activity.pk for activity in activities])
    overview = []
    for activity in activities:
        documents = by_activity.get(activity.pk, [])
        required = required_items(activity, documents, types, today)
        required_ids = {item.document_type.pk for item in required}
        overview.append(ActivityDocuments(
            activity=activity,
            required=required,
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


# --- For payment (Supply Office), per activity (ASSUMPTIONS 463) -------------------------------------------------------


@dataclass
class PaymentActivity:
    """One Conducted activity with the payment documents it still needs (document_checklist()'s own items: required
    payment types that apply to it, counting now, and not Approved yet)."""

    activity: Activity
    missing: list[ChecklistItem]

    @property
    def overdue(self) -> bool:
        return any(item.is_overdue for item in self.missing)

    @property
    def first_due(self) -> date:
        return min(item.due_date for item in self.missing)


def payment_missing(now: datetime | None = None, activities: list[Activity] | None = None) -> list[PaymentActivity]:
    """ASSUMPTIONS 463: the payment documents still needed, ONE ENTRY PER ACTIVITY (never grouped by type): every
    Conducted activity (or `activities`) whose payment items (is_payment, counts_now: after Conducted, item 425) are not
    Approved. The same rule as document_checklist() (activity_document_overview: two queries). Oldest due first."""
    if activities is None:
        activities = list(Activity.objects.filter(status__name=ActivityStatus.CONDUCTED)
                          .select_related("status", "activity_type").order_by("end_date", "pk"))
    result = []
    for entry in activity_document_overview(activities, now):
        missing = [item for item in entry.required if item.is_payment and item.counts_now and item.state != APPROVED]
        if missing:
            result.append(PaymentActivity(entry.activity, missing))
    return sorted(result, key=lambda row: (row.first_due, row.activity.pk))


# --- Documents to follow up, every activity in one list (ASSUMPTIONS 472, the Dashboard) --------------------------------

FOLLOW_UP_STATES = (MISSING_STATE, Document.WAITING_FOR_REVIEW, Document.RETURNED)


@dataclass
class FollowUpRow:
    """One line of the Dashboard's "Documents to follow up": a required type with nothing uploaded (document None), or
    one document waiting for the Director's review or returned to its uploader."""

    activity: Activity
    document_type: DocumentType
    state: str  # one of FOLLOW_UP_STATES
    due_date: date | None  # None: a document of a type that is not required for this activity
    is_overdue: bool
    document: Document | None = None


def counts_for_follow_up(activity: Activity, document_type: DocumentType) -> bool:
    """Whether a MISSING required type of this activity is followed up (the reminders' and S3's scope, ASSUMPTIONS 417,
    472): every non-Draft activity; on a Draft only the Activity Design and Request Letter of a Draft created with them."""
    return activity.status.name != ActivityStatus.DRAFT or is_creation_type(activity, document_type)


def follow_up_activities(activity_type_id: int | None = None) -> QuerySet:
    """The activities the list looks at: non-Draft ones, Drafts created with their creation documents, and any activity
    with a document waiting or returned."""
    in_review = Document.objects.activity_documents().filter(activity=OuterRef("pk"),
                                                             status__status_name=DocumentStatus.FOR_REVIEW)
    queryset = Activity.objects.filter(~Q(status__name=ActivityStatus.DRAFT) | Q(creation_documents_required=True)
                                       | Exists(in_review))
    if activity_type_id:
        queryset = queryset.filter(activity_type_id=activity_type_id)
    return queryset.select_related("status", "activity_type").order_by("start_date", "pk")


def documents_to_follow_up(now: datetime | None = None, activity_type_id: int | None = None) -> list[FollowUpRow]:
    """ASSUMPTIONS 472 (owner): every activity's missing required documents and its documents waiting for review or
    returned, in ONE list, from document_checklist()'s own rule (activity_document_overview: two queries plus the
    activities). Missing = nothing uploaded, counting now (a payment document only once Conducted) and in the
    follow-up scope (counts_for_follow_up). Overdue first, then the oldest due date, the activity and the type."""
    rows: list[FollowUpRow] = []
    for entry in activity_document_overview(list(follow_up_activities(activity_type_id)), now):
        activity = entry.activity
        items = {item.document_type.pk: item for item in entry.required}
        for item in entry.required:
            if item.state == MISSING and item.counts_now and counts_for_follow_up(activity, item.document_type):
                rows.append(FollowUpRow(activity, item.document_type, MISSING_STATE, item.due_date, item.is_overdue))
        for document in entry.documents:
            if document.review_state in (Document.WAITING_FOR_REVIEW, Document.RETURNED):
                item = items.get(document.document_type_id)
                rows.append(FollowUpRow(activity, document.document_type, document.review_state,
                                        item.due_date if item else None, bool(item and item.is_overdue), document))
    return sorted(rows, key=lambda row: (not row.is_overdue, row.due_date is None, row.due_date or date.max,
                                         row.activity.title.lower(), row.activity.pk, row.document_type.name))


# --- Reminders (run daily by Django-Q2; see documents/tasks.py) ----------------------------------


def _format_date(value: date) -> str:
    return f"{value:%b} {value.day}, {value.year}"


def _reminder_message(kind: str, item: ChecklistItem, activity: Activity) -> str:
    type_name, title, due = item.document_type.name, activity.title[:150], _format_date(item.due_date)
    if kind == Notification.DOCUMENT_DUE_SOON:
        return f'Reminder: the {type_name} for "{title}" is due on {due} and has not been uploaded yet.'
    return f'Overdue: the {type_name} for "{title}" was due on {due} and is not approved yet.'


def _reminder_kind(item: ChecklistItem, today: date, days_before: int) -> str | None:
    if not item.counts_now:  # a payment document before Conducted (ASSUMPTIONS 425)
        return None
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
    - "overdue": a required type past its due date and not Approved (the Activity Design / Request Letter of an
      activity created with them: past the due date and nothing uploaded, _required_item).
    Draft activities are skipped, EXCEPT the Activity Design and the Request Letter of a Draft created with them
    (creation_documents_required, is_creation_type; ASSUMPTIONS 417, NAVIGATU): the same rule and timing, to the
    creator AND every active Director (notifications.services.active_directors). Inactive types are skipped.
    A deactivated creator is skipped and logged (the Directors of a Draft still get theirs).
    At most one reminder per (member, activity, document type, kind): running it again creates nothing new.
    """
    from notifications.services import active_directors  # notifications.services imports activities.services

    today = today_in_manila(now)
    days_before = settings.DOCUMENT_REMINDER_DAYS_BEFORE
    counts = {Notification.DOCUMENT_DUE_SOON: 0, Notification.DOCUMENT_OVERDUE: 0, "already_sent": 0, "skipped_inactive_creator": 0}

    activities = (
        Activity.objects.filter(~Q(status__name=ActivityStatus.DRAFT) | Q(creation_documents_required=True))
        .select_related("created_by", "status").order_by("pk")
    )
    directors = None  # loaded once, only when a Draft needs them
    for activity in activities:
        is_draft = activity.status.name == ActivityStatus.DRAFT
        due = [
            (item, kind)
            for item in document_checklist(activity, now)["required"]
            if (not is_draft or is_creation_type(activity, item.document_type))
            and (kind := _reminder_kind(item, today, days_before))
        ]
        if not due:
            continue
        recipients = []
        creator = activity.created_by
        if creator.is_active:
            recipients.append(creator)
        else:
            # Open item for the TBI: who should get these reminders instead?
            logger.warning(
                "Skipped %d document reminder(s) for activity %s: its creator (member %s) is deactivated.",
                len(due), activity.pk, creator.pk,
            )
            counts["skipped_inactive_creator"] += len(due)
        if is_draft:
            if directors is None:
                directors = list(active_directors())
            recipients += [director for director in directors if director.pk != creator.pk]
        for member in recipients:
            for item, kind in due:
                if _create_reminder_once(member, activity, item, kind):
                    counts[kind] += 1
                else:
                    counts["already_sent"] += 1
    return {
        "due_soon": counts[Notification.DOCUMENT_DUE_SOON],
        "overdue": counts[Notification.DOCUMENT_OVERDUE],
        "already_sent": counts["already_sent"],
        "skipped_inactive_creator": counts["skipped_inactive_creator"],
    }


# --- Payment packet for the Supply Office (ASSUMPTIONS 425) ----------------------------------------------------------

PACKET_NOT_CONDUCTED = "The payment packet is ready once the activity is Conducted."
PACKET_NOTHING_APPROVED = "No payment document is approved yet."
PACKET_CONTENTS = "contents.txt"


@dataclass
class PacketEntry:
    """One payment type in the packet: its Approved documents (included) and the others (listed in contents.txt)."""

    document_type: DocumentType
    approved: list[Document]
    waiting: list[Document]
    returned: list[Document]


def payment_packet_entries(activity: Activity) -> list[PacketEntry]:
    """The active payment types that apply to this activity (type_applies), in settings order, with their activity
    documents split by review state (documents_for_listing: certificate evidence never included)."""
    by_name = {t.name: t for t in DocumentType.objects.filter(is_active=True, name__in=settings.DOCUMENT_TYPES_FOR_PAYMENT)}
    types = [by_name[name] for name in settings.DOCUMENT_TYPES_FOR_PAYMENT
             if name in by_name and type_applies(by_name[name], activity)]
    documents = documents_for_listing().filter(activity=activity, document_type__in=types).order_by("uploaded_at", "document_id")
    entries = {t.pk: PacketEntry(t, [], [], []) for t in types}
    for document in documents:
        entry = entries[document.document_type_id]
        state = document.review_state
        (entry.approved if state == Document.APPROVED else entry.waiting if state == Document.WAITING_FOR_REVIEW
         else entry.returned).append(document)
    return list(entries.values())


def payment_packet_refused_reason(activity: Activity, entries: list[PacketEntry] | None = None) -> str | None:
    """Why there is no packet to download yet: not Conducted, or no Approved payment document. None = ready."""
    if activity.status.name != ActivityStatus.CONDUCTED:
        return PACKET_NOT_CONDUCTED
    entries = payment_packet_entries(activity) if entries is None else entries
    if not any(entry.approved for entry in entries):
        return PACKET_NOTHING_APPROVED
    return None


def _packet_contents(activity: Activity, entries: list[PacketEntry], now: datetime) -> str:
    """contents.txt: per type what is included, and what is missing or still waiting for approval (plain text)."""
    local = timezone.localtime(now)
    lines = [
        f"Payment packet for the Supply Office: {activity.title}",
        f"Activity dates: {_format_date(activity.start_date)} to {_format_date(activity.end_date)}",
        f"Prepared on {_format_date(local.date())} {local:%I:%M %p} (Philippine time). Only APPROVED files are included.",
        "",
    ]
    for entry in entries:
        lines.append(f"{entry.document_type.name}:")
        if entry.approved:
            lines.append(f"  Included: {len(entry.approved)} approved file(s)")
            lines += [f"    - {_packet_name(entry, index, document)} ({document.title})"
                      for index, document in enumerate(entry.approved, start=1)]
        else:
            lines.append("  Included: nothing")
        if entry.waiting:
            lines.append(f"  Waiting for approval (not included): {len(entry.waiting)} file(s)")
        if entry.returned:
            lines.append(f"  Returned to the uploader (not included): {len(entry.returned)} file(s)")
        if not entry.approved and not entry.waiting and not entry.returned:
            lines.append("  MISSING: nothing uploaded yet")
        elif not entry.approved:
            lines.append("  MISSING: no approved file yet")
        lines.append("")
    return "\r\n".join(lines)  # Windows line endings: opened in Notepad at the office


def _packet_name(entry: PacketEntry, index: int, document: Document) -> str:
    """Folder per type, numbered files: '<type>/01-<stored file name>' (the stored name comes from build_file_path,
    never the uploaded name)."""
    folder = slugify(entry.document_type.name) or "documents"
    return f"{folder}/{index:02d}-{PurePosixPath(document.file_path.name).name}"


def build_payment_packet(member: TBIMember, activity: Activity, now: datetime | None = None):
    """The zip of the activity's APPROVED payment documents plus contents.txt (ASSUMPTIONS 425). Made on request,
    never stored. Any active staff member (like download_document). Returns (file object at position 0, file name).
    Raises DocumentError with payment_packet_refused_reason; a missing stored file is listed in contents.txt."""
    import tempfile

    require_staff_member(member)
    now = now or timezone.now()
    entries = payment_packet_entries(activity)
    if reason := payment_packet_refused_reason(activity, entries):
        raise DocumentError(reason)
    handle = tempfile.SpooledTemporaryFile(max_size=32 * 1024 * 1024)
    missing_files = []
    with zipfile.ZipFile(handle, "w", zipfile.ZIP_DEFLATED) as archive:
        for entry in entries:
            for index, document in enumerate(entry.approved, start=1):
                try:
                    with document.file_path.open("rb") as source:
                        archive.writestr(_packet_name(entry, index, document), source.read())
                except (FileNotFoundError, ValueError):
                    missing_files.append(_packet_name(entry, index, document))
        contents = _packet_contents(activity, entries, now)
        if missing_files:
            contents += "\r\nThese files could not be read and are not in the zip:\r\n" + "\r\n".join(
                f"  - {name}" for name in missing_files)
        archive.writestr(PACKET_CONTENTS, contents)
    handle.seek(0)
    name = f"payment-packet-{slugify(activity.title)[:60] or 'activity'}-{activity.pk}.zip"
    return handle, name
