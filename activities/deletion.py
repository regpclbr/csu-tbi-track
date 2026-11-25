"""Deleting an activity (ASSUMPTIONS 389). The rules live only here (a separate module because they reach into
certificates, documents and registrations, which themselves import activities.services).

- Only members whose role has can_delete_activities (System Administrator by default).
- REFUSED while the activity holds real records: any Issued certificate, any Approved certificate evidence, or a
  document an AI answer cites (`deletion_refused_reason`, the one rule). Approved activity documents no longer block it
  (ASSUMPTIONS 432: uploads are approved copies of paper documents); they are deleted with the activity, and the
  confirm dialog and ACTIVITY_DELETION.counts say how many documents and files.
- Otherwise, after the member typed the activity's title: one transaction deletes the certificates (waiting and approved
  not issued), their evidence, the documents (and their AI index rows), evaluations, registrations and answers, forms,
  the activity's own certificate text, signatory slots, category and resource person links, notifications, the
  History rows and the activity. Resource persons, signatories, startups and members stay.
- An ACTIVITY_DELETION row (no foreign key to the activity) keeps who, when, what and how many; one log line on
  `activities.audit`. The stored files are removed from media only after the commit (a failed delete loses no file).
- Deleted certificate numbers are never given again (CERTIFICATE_NUMBER_SEQUENCE, certificates.services).
"""

import logging
from dataclasses import dataclass, field

from django.core.exceptions import ValidationError
from django.db import transaction

from accounts.models import TBIMember
from accounts.permissions import Permission, require_permission
from assistant.models import AIMessageSource
from certificates.models import Certificate, CertificateText
from documents.models import Document, DocumentStatus
from notifications.models import Notification
from registrations.models import EvaluationAttachment, EvaluationSubmission, FormTemplate, ParticipantSignature, Registration

from .models import Activity, ActivityAuditLog, ActivityDeletion

audit_log = logging.getLogger("activities.audit")
logger = logging.getLogger("activities")

TITLE_MISMATCH = "Type the activity's title exactly as shown to confirm."


@dataclass
class DeletionSummary:
    """What deleting an activity would remove (the confirm dialog) and what blocks it."""

    registrations: int = 0
    attended: int = 0
    evaluations: int = 0
    certificates_waiting: int = 0  # Generated (waiting for approval)
    certificates_approved: int = 0  # Approved, not issued yet (a resource person's copy may already be printed)
    certificates_issued: int = 0  # blocks the delete
    certificate_numbers: list[str] = field(default_factory=list)  # of the certificates that would be deleted
    documents: int = 0  # activity documents (deleted with the activity, also approved copies: ASSUMPTIONS 432)
    documents_approved: int = 0  # of which Approved (approved copies or reviewed)
    document_files: int = 0  # stored files of those documents
    evidence: int = 0  # certificate evidence (documents with related_certificate)
    approved_evidence: int = 0  # blocks the delete (certificate evidence keeps its own approval)
    cited_documents: int = 0  # blocks the delete (AI_MESSAGE_SOURCE is PROTECT)
    participant_photos: int = 0  # photos / PDFs attendees added to their evaluation (ASSUMPTIONS 435)
    report_drafts: int = 0  # report generator drafts about this activity, with their versions and files (ASSUMPTIONS 442)
    paper_sheets: int = 0  # paper sheets read by the AI for this activity (ASSUMPTIONS 443)
    files: int = 0  # stored files (documents, evidence, certificate PDFs, participant photos)
    forms: int = 0
    history: int = 0
    notifications: int = 0

    def counts(self) -> dict:
        """What ACTIVITY_DELETION.counts stores."""
        return {
            "registrations": self.registrations, "attended": self.attended, "evaluations": self.evaluations,
            "certificates_waiting": self.certificates_waiting, "certificates_approved": self.certificates_approved,
            "documents": self.documents, "documents_approved": self.documents_approved,
            "document_files": self.document_files, "evidence": self.evidence,
            "participant_photos": self.participant_photos, "report_drafts": self.report_drafts,
            "paper_sheets": self.paper_sheets, "files": self.files, "forms": self.forms,
            "history": self.history, "notifications": self.notifications,
        }


def _plural(count: int, one: str, many: str | None = None) -> str:
    return f"{count} {one if count == 1 else (many or one + 's')}"


def deletion_summary(activity: Activity) -> DeletionSummary:
    """Counts of everything that belongs to the activity (a few small queries)."""
    certificates = Certificate.objects.filter(activity=activity)
    documents = Document.objects.filter(activity=activity)
    registrations = Registration.objects.filter(activity=activity)
    statuses = list(certificates.values_list("status", "certificate_number", "file_path"))
    deleted_numbers = sorted(number for status, number, _ in statuses if status != Certificate.Status.ISSUED)
    document_files = [name for name in documents.values_list("file_path", flat=True) if name]
    photos = EvaluationAttachment.objects.filter(submission__registration__activity=activity).count()
    from reports.models import PaperSheetRead, ReportAttachment, ReportDraft  # reports imports activities

    draft_files = ReportAttachment.objects.filter(draft__activity=activity).count()
    activity_documents = documents.filter(related_certificate__isnull=True)
    approved = {"status__status_name": DocumentStatus.APPROVED}
    return DeletionSummary(
        registrations=registrations.count(),
        attended=registrations.filter(attendance_confirmed=True).count(),
        evaluations=EvaluationSubmission.objects.filter(registration__activity=activity).count(),
        certificates_waiting=sum(status == Certificate.Status.GENERATED for status, _, _ in statuses),
        certificates_approved=sum(status == Certificate.Status.APPROVED for status, _, _ in statuses),
        certificates_issued=sum(status == Certificate.Status.ISSUED for status, _, _ in statuses),
        certificate_numbers=deleted_numbers,
        documents=activity_documents.count(),
        documents_approved=activity_documents.filter(**approved).count(),
        document_files=sum(1 for name in activity_documents.values_list("file_path", flat=True) if name),
        evidence=documents.filter(related_certificate__isnull=False).count(),
        approved_evidence=documents.filter(related_certificate__isnull=False, **approved).count(),
        cited_documents=AIMessageSource.objects.filter(document__activity=activity).values("document").distinct().count(),
        participant_photos=photos,
        report_drafts=ReportDraft.objects.filter(activity=activity).count(),
        paper_sheets=PaperSheetRead.objects.filter(activity=activity).count(),
        files=len(document_files) + sum(1 for _, _, name in statuses if name) + photos + draft_files,
        forms=FormTemplate.objects.filter(activity=activity).count(),
        history=ActivityAuditLog.objects.filter(activity=activity).count(),
        notifications=Notification.objects.filter(activity=activity).count(),
    )


def deletion_refused_reason(activity: Activity, summary: DeletionSummary | None = None) -> str | None:
    """None when the activity may be deleted; otherwise why not, in plain words. Real records are kept: an Issued
    certificate, Approved certificate evidence, or a document an AI answer cites. Approved activity documents (approved
    copies, ASSUMPTIONS 432) never block it: they are deleted with the activity."""
    summary = summary or deletion_summary(activity)
    blockers = []
    if summary.certificates_issued:
        blockers.append(_plural(summary.certificates_issued, "issued certificate"))
    if summary.approved_evidence:
        blockers.append(_plural(summary.approved_evidence, "approved certificate evidence file"))
    if summary.cited_documents:
        blockers.append(_plural(summary.cited_documents, "document cited by the AI Assistant",
                                "documents cited by the AI Assistant"))
    if not blockers:
        return None
    listed = blockers[0] if len(blockers) == 1 else ", ".join(blockers[:-1]) + " and " + blockers[-1]
    return f"This activity can't be deleted: it has {listed}. Real records are kept."


def what_will_be_deleted(summary: DeletionSummary) -> list[str]:
    """The confirm dialog's list, one line per kind (only kinds with something to delete)."""
    lines = []
    if summary.registrations:
        lines.append(f"{_plural(summary.registrations, 'registration')} ({summary.attended} attended)")
    if summary.evaluations:
        lines.append(_plural(summary.evaluations, "evaluation"))
    if summary.certificates_waiting:
        lines.append(_plural(summary.certificates_waiting, "certificate waiting for approval",
                             "certificates waiting for approval"))
    if summary.certificates_approved:
        lines.append(_plural(summary.certificates_approved, "approved certificate not yet issued",
                             "approved certificates not yet issued")
                     + " (resource persons' copies may already be printed)")
    if summary.documents:
        line = _plural(summary.documents, "document")
        if summary.documents_approved:
            approved = "approved" if summary.documents_approved == summary.documents else f"{summary.documents_approved} approved"
            line += f" ({approved})"
        if summary.document_files:
            line += f" and {'its' if summary.document_files == 1 else 'their'} {_plural(summary.document_files, 'file')}"
        lines.append(line)
    if summary.evidence:
        lines.append(_plural(summary.evidence, "certificate evidence file"))
    if summary.participant_photos:
        lines.append(_plural(summary.participant_photos, "participant photo or file", "participant photos and files")
                     + " added to evaluations")
    if summary.report_drafts:
        lines.append(_plural(summary.report_drafts, "report draft") + " (with their versions and files)")
    if summary.paper_sheets:
        lines.append(_plural(summary.paper_sheets, "paper sheet read by the AI", "paper sheets read by the AI"))
    if summary.files:
        lines.append(_plural(summary.files, "stored file") + " removed from the server")
    if summary.forms:
        lines.append(_plural(summary.forms, "registration or evaluation form"))
    if summary.history:
        lines.append(_plural(summary.history, "History entry", "History entries"))
    if summary.notifications:
        lines.append(_plural(summary.notifications, "notification"))
    return lines


def titles_match(activity: Activity, typed_title: str) -> bool:
    return (typed_title or "").strip() == activity.title.strip()


def delete_activity(member: TBIMember, activity: Activity, typed_title: str) -> ActivityDeletion:
    """Delete `activity` and everything that belongs to it (module docstring). Raises PermissionDenied without
    can_delete_activities, ValidationError when refused (deletion_refused_reason) or when the typed title differs."""
    require_permission(member, Permission.DELETE_ACTIVITIES)
    with transaction.atomic():
        activity = Activity.objects.select_for_update().select_related("activity_type").get(pk=activity.pk)
        if not titles_match(activity, typed_title):
            raise ValidationError({"title": [TITLE_MISMATCH]})
        summary = deletion_summary(activity)
        if reason := deletion_refused_reason(activity, summary):
            raise ValidationError(reason)

        certificates = Certificate.objects.filter(activity=activity)
        documents = Document.objects.filter(activity=activity)
        files = [(Document, name) for name in documents.values_list("file_path", flat=True) if name]
        files += [(Certificate, name) for name in certificates.values_list("file_path", flat=True) if name]
        # Participant signature images (ASSUMPTIONS 434); their rows and the attendance days cascade with the registrations.
        # Participant photos (ASSUMPTIONS 435) cascade with the evaluations; their files are removed after the commit by
        # the post_delete receiver in registrations.evaluation_photos.
        files += [(ParticipantSignature, name) for name in ParticipantSignature.objects.filter(
            registration__activity=activity).values_list("file_path", flat=True) if name]

        record = ActivityDeletion.objects.create(
            deleted_activity_id=activity.pk,
            activity_title=activity.title,
            start_date=activity.start_date,
            end_date=activity.end_date,
            activity_type_name=activity.activity_type.name,
            counts=summary.counts(),
            certificate_numbers=", ".join(summary.certificate_numbers),
            deleted_by=member,
        )
        # Children before parents (PROTECT foreign keys): evidence -> certificates -> documents -> registrations
        # (evaluations and answers cascade) -> the activity's certificate text -> History -> the activity (forms,
        # signatory slots, category and resource person links and notifications cascade).
        documents.filter(related_certificate__isnull=False).delete()
        certificates.delete()
        documents.delete()  # their DOCUMENT_EMBEDDING rows cascade
        Registration.objects.filter(activity=activity).delete()
        CertificateText.objects.filter(activity=activity).delete()
        ActivityAuditLog.objects.delete_for_deleted_activity(activity.pk)
        activity_id, title = activity.pk, activity.title
        activity.delete()
        transaction.on_commit(lambda: _remove_files(files, activity_id))

    audit_log.info(
        "Activity deleted: activity_id=%s %r by member_id=%s; deletion_id=%s counts=%s", activity_id, title,
        member.pk, record.pk, record.counts,
    )
    return record


def _remove_files(files: list[tuple[type, str]], activity_id: int) -> None:
    """After the commit: remove the stored files. A file already missing is skipped (logged, never an error)."""
    for model, name in files:
        storage = model._meta.get_field("file_path").storage
        try:
            if storage.exists(name):
                storage.delete(name)
            else:
                logger.warning("Deleted activity %s: file %s was already missing.", activity_id, name)
        except OSError as error:
            logger.warning("Deleted activity %s: file %s could not be removed: %s", activity_id, name, type(error).__name__)

