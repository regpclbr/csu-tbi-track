"""Work submitted to the Director (ASSUMPTIONS 437). Every rule about SUBMISSION and SUBMISSION_EVENT lives here; views stay
thin.

- Submit: any active staff member uploads a letter, report or other file (the document upload checks: PDF, DOCX, XLSX,
  JPG, PNG by content, DOCUMENT_MAX_UPLOAD_MB), optionally for an activity and a document type, with an optional note.
  Every other active member with can_approve_documents (the Director by default; ROLE flags, never a role name) is
  notified. Refused when nobody else could approve it.
- Approve / return: can_approve_documents, never your own submission (separation of duties). A return needs a reason;
  the submitter is notified either way and can resubmit a returned one (a new file, the same row; the history keeps
  each round).
- Sign (PDF only, never delegated): while approving, the approver may stamp THEIR OWN signature image (the active
  CERTIFICATE_SIGNATORY linked to their account, certificates.services.signatory_for) at the "Noted:" line or a chosen
  place (approvals.signing). The stamped copy is a new file; the original is never changed. It is a stamped image, not
  a cryptographic digital signature.
- Unapprove: can_approve_documents, a reason required: Approved -> Returned; the signed copy is deleted (the original
  stays); a document saved to the activity from it is deleted with the usual guards (refused = nothing changes); the
  submitter is notified.
- Save to the activity's documents: the submitter or an approver saves an approved submission (the signed copy when
  there is one) as an activity document (documents.services.upload_document with approved_by: an approved copy, or
  Approved in the reviewer's name for a type that needs review).
Every step writes a SUBMISSION_EVENT row (who, when, the reason) and an approvals.audit line (IDs only).
"""

import logging
import secrets
from dataclasses import dataclass
from datetime import datetime

from django.core.exceptions import PermissionDenied, ValidationError
from django.core.files.base import ContentFile, File
from django.core.files.uploadedfile import UploadedFile
from django.db import transaction
from django.db.models import Q, QuerySet
from django.utils import timezone

from accounts.models import TBIMember
from accounts.permissions import (
    Permission,
    acting_on_behalf_of,
    has_permission,
    has_permission_for,
    require_permission,
    require_permission_for,
    require_staff_member,
)
from accounts.services import active_document_approvers
from activities.models import Activity
from documents import services as document_services
from documents.models import Document, DocumentType
from notifications.models import Notification

from . import signing
from .models import Submission, SubmissionEvent

audit_log = logging.getLogger("approvals.audit")  # who did what to which submission (IDs only, never contents)

REVIEW_PERMISSION = Permission.APPROVE_DOCUMENTS  # who reviews submissions (owner decision, ASSUMPTIONS 437)
NOTE_MAX_LENGTH = 1000
UPLOAD_ROOT = "submissions"

NO_OTHER_APPROVER = ("Nobody else can approve this yet: no other active member has the right to approve documents. "
                     "Ask the System Administrator.")
OWN_SUBMISSION = "You cannot approve or return your own submission (separation of duties)."
NOT_WAITING = "This submission is not waiting for approval."
NOT_APPROVED = "Only an approved submission can be unapproved."
NOT_RETURNED = "Only a returned submission can be submitted again."
ONLY_SUBMITTER = "Only the member who submitted it can submit it again."
SIGN_PDF_ONLY = "Only PDF files can be signed. Approve it without signing, or ask for a PDF."
SIGN_NO_SIGNATORY = ("Your account is not linked to a signature on the signatory list. Ask the member who manages "
                     "signatories to link it (Certificates → Signatories).")
SIGN_NO_IMAGE = "Your signature on the signatory list has no image yet. Ask the member who manages signatories to add it."
SIGN_NOT_DELEGATED = "Signing is never delegated: approve it without signing, or ask the Director to sign it."
ASSIGNEE_OWN = "You cannot review your own submission through an assignment (separation of duties)."
SAVE_NOT_APPROVED = "Only an approved submission can be saved to the activity's documents."
SAVE_NO_ACTIVITY = "This submission is not linked to an activity."
SAVE_ALREADY = "This submission is already saved to the activity's documents."
SAVE_CHOOSE_TYPE = "Choose the document type."


class SubmissionError(Exception):
    """An action on a submission was refused. The message is safe to show to the member."""


# --- Who may do what ---------------------------------------------------------------------------------------------


def can_review(member: TBIMember) -> bool:
    """Approve, return, unapprove and see every submission: can_approve_documents."""
    return has_permission(member, REVIEW_PERMISSION)


def can_review_submission(member: TBIMember, submission: Submission) -> bool:
    """can_approve_documents, or this submission's "Review the submission" assignment (ASSUMPTIONS 437)."""
    return has_permission_for(member, REVIEW_PERMISSION, submission)


def visible_submissions(member: TBIMember) -> QuerySet:
    """The submissions `member` may open: their own and the ones assigned to them; every one for a reviewer. Anything
    else is 404."""
    queryset = Submission.objects.select_related("activity", "document_type", "submitted_by", "reviewed_by",
                                                 "signed_by", "saved_document")
    if can_review(member):
        return queryset
    return queryset.filter(Q(submitted_by=member) | Q(work_assignments__assigned_to=member,
                                                      work_assignments__status="open")).distinct()


def is_pdf(submission: Submission) -> bool:
    return submission.file_extension == "pdf"


def sign_refused_reason(member: TBIMember, submission: Submission) -> str | None:
    """Why `member` cannot stamp their signature on this submission, or None. PDF only; their own active, linked
    signature with an image (signing is never delegated)."""
    from certificates.services import signatory_for

    if not can_review(member):
        return SIGN_NOT_DELEGATED  # an assignee may approve, never sign (ASSUMPTIONS 437)
    if not is_pdf(submission):
        return SIGN_PDF_ONLY
    signatory = signatory_for(member)
    if signatory is None:
        return SIGN_NO_SIGNATORY
    if not signatory.signature_image_path:
        return SIGN_NO_IMAGE
    return None


def allowed_actions(member: TBIMember, submission: Submission) -> dict[str, bool]:
    """What `member` may do with `submission` now (for the page; the services check again)."""
    reviewer = can_review_submission(member, submission)
    own = submission.submitted_by_id == member.pk
    status = submission.status
    return {
        "approve": reviewer and not own and status == Submission.Status.SUBMITTED,
        "return": reviewer and not own and status == Submission.Status.SUBMITTED,
        "unapprove": can_review(member) and status == Submission.Status.APPROVED,  # never through an assignment
        "resubmit": own and status == Submission.Status.RETURNED,
        "save": ((own or can_review(member)) and status == Submission.Status.APPROVED and submission.activity_id is not None
                 and submission.saved_document_id is None),
    }


# --- Small helpers ----------------------------------------------------------------------------------------------------


def _event(submission: Submission, action: str, member: TBIMember, reason: str = "",
           on_behalf_of: TBIMember | None = None) -> SubmissionEvent:
    return SubmissionEvent.objects.create(submission=submission, action=action, member=member, reason=reason,
                                          on_behalf_of=on_behalf_of or acting_on_behalf_of(member))


def _locked(submission: Submission) -> Submission:
    return (Submission.objects.select_for_update(of=("self",))
            .select_related("activity", "document_type", "submitted_by", "reviewed_by").get(pk=submission.pk))


def _clean_text(title: str, note: str) -> tuple[str, str]:
    problems = {}
    title = (title or "").strip()
    note = (note or "").strip()
    if not title:
        problems["title"] = ["Give the submission a title, for example: Request letter for the pitching workshop."]
    elif len(title) > 255:
        problems["title"] = ["The title can be at most 255 characters."]
    if len(note) > NOTE_MAX_LENGTH:
        problems["note"] = [f"The note can be at most {NOTE_MAX_LENGTH} characters."]
    if problems:
        raise ValidationError(problems)
    return title, note


def _store(submission: Submission, file: UploadedFile, extension: str, now: datetime | None) -> None:
    """Store the file under submissions/<year>/submission-<random>.<ext> (never the uploaded name) and fill the file
    columns. The row is not saved here."""
    year = timezone.localtime(now or timezone.now()).year
    submission.file_path.save(f"{UPLOAD_ROOT}/{year}/submission-{secrets.token_hex(8)}.{extension}", file, save=False)
    submission.file_name = document_services._original_name(file)
    submission.file_extension = extension
    submission.file_size_kb = document_services._size_kb(file)


def _delete_file(name: str | None) -> None:
    if name:
        Submission._meta.get_field("file_path").storage.delete(name)


def _notify(members, submission: Submission, kind: str, message: str) -> int:
    created = Notification.objects.bulk_create(
        Notification(member=person, activity=submission.activity, submission=submission, type=kind, message=message[:500])
        for person in members)
    return len(created)


def _notify_submitter(submission: Submission, actor: TBIMember, kind: str, message: str) -> None:
    person = submission.submitted_by
    if person.pk != actor.pk and person.is_active:
        _notify([person], submission, kind, message)


def _reviewers_except(member: TBIMember) -> QuerySet:
    return active_document_approvers().exclude(pk=member.pk).order_by("pk")


def _check_links(activity: Activity | None, document_type: DocumentType | None) -> None:
    if document_type is not None and not document_type.is_active:
        raise ValidationError({"document_type": ["This document type is not active."]})


# --- Submit and resubmit ----------------------------------------------------------------------------------------------


def submit(member: TBIMember, file: UploadedFile | None, title: str, note: str = "", activity: Activity | None = None,
           document_type: DocumentType | None = None, now: datetime | None = None) -> Submission:
    """"Submit to the Director": any active staff member. The file passes the document upload checks. Every other
    active approver gets a "submission_waiting" notification (same transaction). Refused (SubmissionError) when nobody
    else could approve it."""
    require_staff_member(member)
    problems = {}
    try:
        title, note = _clean_text(title, note)
    except ValidationError as error:
        problems.update(error.message_dict)
    try:
        extension = document_services.validate_upload(file)
    except ValidationError as error:
        problems.update(error.message_dict)
    try:
        _check_links(activity, document_type)
    except ValidationError as error:
        problems.update(error.message_dict)
    if problems:
        raise ValidationError(problems)
    reviewers = list(_reviewers_except(member))
    if not reviewers:
        raise SubmissionError(NO_OTHER_APPROVER)
    now = now or timezone.now()
    submission = Submission(title=title, note=note, activity=activity, document_type=document_type,
                            submitted_by=member, submitted_at=now)
    _store(submission, file, extension, now)
    try:
        with transaction.atomic():
            submission.save()
            _event(submission, SubmissionEvent.Action.SUBMITTED, member, note)
            _notify(reviewers, submission, Notification.SUBMISSION_WAITING,
                    f'{member.name[:80]} submitted "{title[:150]}" for your approval.')
            audit_log.info("Submission created: submission_id=%s by member_id=%s", submission.pk, member.pk)
    except Exception:
        _delete_file(submission.file_path.name)
        raise
    return submission


def submit_generated(member: TBIMember, content: bytes, file_name: str, title: str, note: str = "",
                     activity: Activity | None = None, document_type: DocumentType | None = None,
                     now: datetime | None = None) -> Submission:
    """The same as submit() for a file the system generated (Prompts 6–7: letters, reports): the bytes and the name
    the file should have (its extension decides the type, checked like an upload)."""
    return submit(member, ContentFile(content, name=file_name), title, note, activity, document_type, now)


def resubmit(member: TBIMember, submission: Submission, file: UploadedFile | None, note: str = "",
             now: datetime | None = None) -> Submission:
    """Submit a RETURNED submission again with a new file (the submitter only): the same row, status back to waiting,
    the review fields cleared; the old file is removed after the commit (the history keeps each round). The approvers
    are told again."""
    require_staff_member(member)
    note = (note or "").strip()
    if len(note) > NOTE_MAX_LENGTH:
        raise ValidationError({"note": [f"The note can be at most {NOTE_MAX_LENGTH} characters."]})
    extension = document_services.validate_upload(file)
    now = now or timezone.now()
    with transaction.atomic():
        submission = _locked(submission)
        if submission.submitted_by_id != member.pk:
            raise PermissionDenied(ONLY_SUBMITTER)
        if submission.status != Submission.Status.RETURNED:
            raise SubmissionError(NOT_RETURNED)
        reviewers = list(_reviewers_except(member))
        if not reviewers:
            raise SubmissionError(NO_OTHER_APPROVER)
        old_name = submission.file_path.name
        _store(submission, file, extension, now)
        try:
            with transaction.atomic():
                submission.status = Submission.Status.SUBMITTED
                submission.reviewed_by = None
                submission.reviewed_at = None
                submission.review_reason = ""
                submission.submitted_at = now
                if note:
                    submission.note = note
                submission.save()
                _event(submission, SubmissionEvent.Action.RESUBMITTED, member, note)
                _notify(reviewers, submission, Notification.SUBMISSION_WAITING,
                        f'{member.name[:80]} submitted "{submission.title[:150]}" again for your approval.')
                audit_log.info("Submission resubmitted: submission_id=%s by member_id=%s", submission.pk, member.pk)
        except Exception:
            _delete_file(submission.file_path.name)
            raise
        transaction.on_commit(lambda: _delete_file(old_name))
    return submission


# --- Review: approve (and sign), return, unapprove ------------------------------------------------------------------


def _ensure_reviewer(member: TBIMember, submission: Submission):
    """can_approve_documents or this submission's assignment (ASSUMPTIONS 437); never your own submission. Returns the
    assignment that allowed it (or None)."""
    assignment = require_permission_for(member, REVIEW_PERMISSION, submission)
    if submission.submitted_by_id == member.pk:
        raise SubmissionError(ASSIGNEE_OWN if assignment is not None else OWN_SUBMISSION)
    if submission.status != Submission.Status.SUBMITTED:
        raise SubmissionError(NOT_WAITING)
    return assignment


def parse_placement(data) -> signing.Placement:
    """The approve form's `placement` ("noted" or a POSITIONS key) and `page` (1-based) as a Placement."""
    how = (data.get("placement") or signing.NOTED).strip()
    if how == signing.NOTED:
        return signing.Placement(signing.NOTED)
    raw_page = (data.get("page") or "1").strip()
    page = int(raw_page) if raw_page.isdigit() else 0
    return signing.Placement("position", page=page, position=how)


def _original_bytes(submission: Submission) -> bytes:
    with submission.file_path.open("rb") as handle:
        return handle.read()


def _stamped(member: TBIMember, submission: Submission, placement: signing.Placement, now: datetime):
    """(signed PDF bytes, the signatory row) or SubmissionError."""
    from certificates.services import signatory_for

    if refused := sign_refused_reason(member, submission):
        raise SubmissionError(refused)
    signatory = signatory_for(member)
    with signatory.signature_image_path.open("rb") as handle:
        image = handle.read()
    try:
        data = signing.stamp(_original_bytes(submission), image, placement, timezone.localtime(now).date())
    except signing.SigningError as error:
        raise SubmissionError(str(error)) from error
    return data, signatory


def signed_preview(member: TBIMember, submission: Submission, placement: signing.Placement,
                   now: datetime | None = None) -> bytes:
    """The stamped copy as it would be saved, for checking before approving (never stored). The same rules as signing
    while approving."""
    require_permission(member, REVIEW_PERMISSION)
    if submission.submitted_by_id == member.pk:
        raise SubmissionError(OWN_SUBMISSION)
    return _stamped(member, submission, placement, now or timezone.now())[0]


def approve_submission(member: TBIMember, submission: Submission, *, sign: bool = False,
                       placement: signing.Placement | None = None, now: datetime | None = None) -> Submission:
    """Approve a waiting submission (can_approve_documents, never your own). `sign`: also stamp the approver's own
    signature on a COPY of the PDF (the original stays); a signing problem refuses the whole approval, so nothing is
    approved half-signed. Events "approved" (+ "signed"), approvals.audit, the submitter notified."""
    now = now or timezone.now()
    with transaction.atomic():
        submission = _locked(submission)
        _ensure_reviewer(member, submission)
        signed_name = None
        if sign:
            data, signatory = _stamped(member, submission, placement or signing.Placement(signing.NOTED), now)
            year = timezone.localtime(now).year
            signed_name = Submission._meta.get_field("signed_file_path").storage.save(
                f"{UPLOAD_ROOT}/{year}/submission-{submission.pk}-signed-{secrets.token_hex(6)}.pdf", ContentFile(data))
        try:
            with transaction.atomic():
                submission.status = Submission.Status.APPROVED
                submission.reviewed_by = member
                submission.reviewed_at = now
                submission.review_reason = ""
                if signed_name:
                    submission.signed_file_path.name = signed_name
                    submission.signed_by, submission.signed_at, submission.signatory = member, now, signatory
                submission.save()
                _event(submission, SubmissionEvent.Action.APPROVED, member)
                if signed_name:
                    _event(submission, SubmissionEvent.Action.SIGNED, member)
                audit_log.info("Submission approved: submission_id=%s by member_id=%s signed=%s", submission.pk,
                               member.pk, bool(signed_name))
                from .assignments import complete_for

                complete_for("submission_review", submission, member, now)
                _queue_index_on_commit(submission)
                _notify_submitter(submission, member, Notification.SUBMISSION_APPROVED,
                                  f'"{submission.title[:150]}" was approved by {member.name[:60]}'
                                  + (" and signed." if signed_name else "."))
        except Exception:
            _delete_file(signed_name)
            raise
    return submission


def return_submission(member: TBIMember, submission: Submission, reason: str, now: datetime | None = None) -> Submission:
    """Return a waiting submission with a reason (can_approve_documents, never your own): the submitter is notified and
    can submit it again with a new file."""
    reason = document_services.clean_reason(reason)
    with transaction.atomic():
        submission = _locked(submission)
        _ensure_reviewer(member, submission)
        submission.status = Submission.Status.RETURNED
        submission.reviewed_by = member
        submission.reviewed_at = now or timezone.now()
        submission.review_reason = reason
        submission.save()
        _event(submission, SubmissionEvent.Action.RETURNED, member, reason)
        audit_log.info("Submission returned: submission_id=%s by member_id=%s", submission.pk, member.pk)
        from .assignments import complete_for

        complete_for("submission_review", submission, member, submission.reviewed_at)
        _notify_submitter(submission, member, Notification.SUBMISSION_RETURNED,
                          f'"{submission.title[:150]}" was returned by {member.name[:60]}: {reason}')
    return submission


def unapprove_submission(member: TBIMember, submission: Submission, reason: str,
                         now: datetime | None = None) -> Submission:
    """Take back an approval (can_approve_documents, a reason required). What depends on it and how it is undone: the
    status goes back to Returned (the submitter can submit it again); the signed copy is deleted after the commit (the
    original stays); a document saved to the activity from it is deleted with the documents' guards
    (documents.services.remove_saved_submission_document; refused = nothing changes). The submitter is notified."""
    require_permission(member, REVIEW_PERMISSION)
    reason = document_services.clean_reason(reason)
    with transaction.atomic():
        submission = _locked(submission)
        if submission.status != Submission.Status.APPROVED:
            raise SubmissionError(NOT_APPROVED)
        if submission.saved_document_id:
            try:
                document_services.remove_saved_submission_document(member, submission.saved_document, reason)
            except document_services.DocumentError as error:
                raise SubmissionError(str(error)) from error
            submission.saved_document = None
        signed_name = submission.signed_file_path.name if submission.signed_file_path else None
        submission.status = Submission.Status.RETURNED
        submission.reviewed_by = member
        submission.reviewed_at = now or timezone.now()
        submission.review_reason = reason
        submission.signed_file_path = None
        submission.signed_by = submission.signed_at = submission.signatory = None
        submission.save()
        _event(submission, SubmissionEvent.Action.UNAPPROVED, member, reason)
        submission.embeddings.all().delete()  # only approved submissions are searchable (ASSUMPTIONS 438)
        audit_log.info("Submission unapproved: submission_id=%s by member_id=%s", submission.pk, member.pk)
        _notify_submitter(submission, member, Notification.SUBMISSION_UNAPPROVED,
                          f'The approval of "{submission.title[:150]}" was taken back by {member.name[:60]}: {reason}')
        if signed_name:
            transaction.on_commit(lambda: _delete_file(signed_name))
    return submission


def _queue_index_on_commit(submission: Submission) -> None:
    """Index an approved submission for the assistant once the approval is saved (ASSUMPTIONS 438; robust: a problem
    queueing never undoes the approval)."""
    from assistant.indexing import SUBMISSION, queue_source_indexing

    submission_id = submission.pk
    transaction.on_commit(lambda: queue_source_indexing(SUBMISSION, submission_id), robust=True)


# --- Save to the activity's documents -------------------------------------------------------------------------------


def save_to_documents(member: TBIMember, submission: Submission, document_type: DocumentType | None = None,
                      now: datetime | None = None) -> Document:
    """Save an approved submission linked to an activity as one of its documents (the submitter or an approver): the
    signed copy when there is one, else the original; title = the submission's title; through
    documents.services.upload_document (the file checks, History, indexing, auto-Scheduled) with approved_by = the
    reviewer, so it is never reviewed twice. Once only."""
    with transaction.atomic():
        submission = _locked(submission)
        if not allowed_actions(member, submission)["save"]:
            if submission.status != Submission.Status.APPROVED:
                raise SubmissionError(SAVE_NOT_APPROVED)
            if submission.activity_id is None:
                raise SubmissionError(SAVE_NO_ACTIVITY)
            if submission.saved_document_id is not None:
                raise SubmissionError(SAVE_ALREADY)
            raise PermissionDenied("Only the member who submitted it or the Director can save it to the documents.")
        document_type = document_type or submission.document_type
        if document_type is None:
            raise ValidationError({"document_type": [SAVE_CHOOSE_TYPE]})
        stored = submission.signed_file_path if submission.signed_file_path else submission.file_path
        stem = submission.file_name.rsplit(".", 1)[0] or "submission"
        name = f"{stem}-signed.pdf" if submission.signed_file_path else submission.file_name
        with stored.open("rb") as handle:
            document = document_services.upload_document(
                member, submission.activity, document_type, File(handle, name=name), submission.title, now=now,
                approved_by=submission.reviewed_by, approved_at=submission.reviewed_at)
        submission.saved_document = document
        submission.save(update_fields=["saved_document", "updated_at"])
        submission.embeddings.all().delete()  # the saved document is indexed instead (never the same text twice)
        _event(submission, SubmissionEvent.Action.SAVED, member, f"{document_type.name}: {document.title}")
        audit_log.info("Submission saved to documents: submission_id=%s document_id=%s by member_id=%s", submission.pk,
                       document.pk, member.pk)
    return document


# --- Reading ---------------------------------------------------------------------------------------------------------


@dataclass
class SubmissionFile:
    handle: object
    name: str
    content_type: str


CONTENT_TYPES = {"pdf": "application/pdf", "png": "image/png", "jpg": "image/jpeg",
                 "docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                 "xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"}


def open_file(submission: Submission, signed: bool = False) -> SubmissionFile:
    """The original or the signed copy (FileNotFoundError when missing). The views check who may see it first."""
    if signed:
        if not submission.signed_file_path:
            raise FileNotFoundError("no signed copy")
        stem = submission.file_name.rsplit(".", 1)[0] or "submission"
        return SubmissionFile(submission.signed_file_path.open("rb"), f"{stem}-signed.pdf", "application/pdf")
    try:
        handle = submission.file_path.open("rb")
    except ValueError as error:
        raise FileNotFoundError(submission.file_path.name) from error
    return SubmissionFile(handle, submission.file_name, CONTENT_TYPES.get(submission.file_extension,
                                                                          "application/octet-stream"))


def list_submissions(member: TBIMember, *, mine: bool = False, status: str = "", q: str = "") -> QuerySet:
    """My submissions (mine=True) or every submission a reviewer may see, newest first, filtered."""
    queryset = visible_submissions(member)
    if mine:
        queryset = queryset.filter(submitted_by=member)
    if status in Submission.Status.values:
        queryset = queryset.filter(status=status)
    if q := (q or "").strip()[:100]:
        queryset = queryset.filter(Q(title__icontains=q) | Q(activity__title__icontains=q)
                                   | Q(submitted_by__name__icontains=q))
    return queryset.order_by("-submitted_at", "-submission_id")


def waiting_for(member: TBIMember) -> QuerySet:
    """Submissions waiting for `member`'s approval: can_approve_documents, never their own. (Submissions assigned to a
    member without the flag are in their "Assigned to me" list instead.)"""
    if not can_review(member):
        return Submission.objects.none()
    return (Submission.objects.filter(status=Submission.Status.SUBMITTED).exclude(submitted_by=member)
            .select_related("activity", "submitted_by"))
