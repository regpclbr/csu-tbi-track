"""SUBMISSION (work a member sends to the Director) and SUBMISSION_EVENT (its append-only history) (ASSUMPTIONS 437).

Every change goes through approvals/services.py. The original file is never changed: a signed copy is a second file
(signed_file_path), and a resubmission after a return replaces the file of the same row (the event log keeps each round).
"""

from django.conf import settings
from django.db import models
from django.db.models import Q


class SubmissionEventIsAppendOnly(Exception):
    """A SUBMISSION_EVENT row was changed or deleted: the history is append-only."""


def submission_upload_to(instance: "Submission", filename: str) -> str:
    """The path is built by approvals.services (submissions/<year>/submission-<id>-<random>.<ext>); never the uploaded
    file's own name."""
    return filename


class Submission(models.Model):
    """A letter, report or other file submitted to the Director (members with can_approve_documents) for approval."""

    class Status(models.TextChoices):
        SUBMITTED = "submitted", "Waiting for approval"
        APPROVED = "approved", "Approved"
        RETURNED = "returned", "Returned"

    submission_id = models.BigAutoField(primary_key=True)
    title = models.CharField(max_length=255)
    note = models.TextField(blank=True, default="", help_text="The member's optional note to the Director.")
    # SET_NULL: the System Administrator may delete an activity (ASSUMPTIONS 389); the submission stays.
    activity = models.ForeignKey("activities.Activity", on_delete=models.SET_NULL, null=True, blank=True,
                                 related_name="submissions")
    document_type = models.ForeignKey("documents.DocumentType", on_delete=models.PROTECT, null=True, blank=True,
                                      related_name="submissions")
    submitted_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="submissions")
    file_path = models.FileField(upload_to=submission_upload_to, max_length=255)
    file_name = models.CharField(max_length=255, help_text="The uploaded name, for display only (never in a path).")
    file_extension = models.CharField(max_length=10)
    file_size_kb = models.PositiveIntegerField()
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.SUBMITTED)
    reviewed_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, null=True, blank=True,
                                    related_name="reviewed_submissions")
    reviewed_at = models.DateTimeField(null=True, blank=True)
    review_reason = models.TextField(blank=True, default="", help_text="The latest return or unapprove reason.")
    signed_file_path = models.FileField(upload_to=submission_upload_to, max_length=255, null=True, blank=True)
    signed_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, null=True, blank=True,
                                  related_name="signed_submissions")
    signed_at = models.DateTimeField(null=True, blank=True)
    # The exact version of the signature that was stamped (CERTIFICATE_SIGNATORY rows never change, ASSUMPTIONS 363).
    signatory = models.ForeignKey("certificates.CertificateSignatory", on_delete=models.PROTECT, null=True, blank=True,
                                  related_name="signed_submissions")
    # The activity document made from it ("Save to the activity's documents"). SET_NULL: deleting an activity deletes
    # its documents.
    saved_document = models.OneToOneField("documents.Document", on_delete=models.SET_NULL, null=True, blank=True,
                                          related_name="from_submission")
    submitted_at = models.DateTimeField(help_text="The latest submission (a resubmission moves it).")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "submission"
        ordering = ["-submitted_at", "-submission_id"]
        constraints = [
            models.CheckConstraint(condition=Q(status__in=["submitted", "approved", "returned"]),
                                   name="submission_status_valid"),
            # Approved or returned = somebody reviewed it.
            models.CheckConstraint(
                condition=Q(status="submitted") | Q(reviewed_by__isnull=False, reviewed_at__isnull=False),
                name="submission_reviewed_when_decided"),
            # A signed copy only on an approved submission, with who, when and which signature.
            models.CheckConstraint(
                condition=(Q(signed_file_path__isnull=True) | Q(signed_file_path=""))
                & Q(signed_by__isnull=True, signed_at__isnull=True, signatory__isnull=True)
                | Q(status="approved", signed_by__isnull=False, signed_at__isnull=False, signatory__isnull=False)
                & ~Q(signed_file_path="") & Q(signed_file_path__isnull=False),
                name="submission_signed_copy_complete"),
        ]

    def __str__(self) -> str:
        return self.title


class SubmissionEventQuerySet(models.QuerySet):
    def update(self, **kwargs):
        raise SubmissionEventIsAppendOnly("Submission history rows cannot be changed.")

    def delete(self):
        raise SubmissionEventIsAppendOnly("Submission history rows cannot be deleted.")


class SubmissionEvent(models.Model):
    """One step in a submission's history: submitted, resubmitted, approved, signed, returned, unapproved, saved to the
    activity's documents. Append-only (like ACTIVITY_AUDIT_LOG). on_behalf_of: the member whose work an assignee did
    (WORK_ASSIGNMENT, Part B of ASSUMPTIONS 437)."""

    class Action(models.TextChoices):
        SUBMITTED = "submitted", "Submitted"
        RESUBMITTED = "resubmitted", "Resubmitted"
        APPROVED = "approved", "Approved"
        SIGNED = "signed", "Signed (stamped signature)"
        RETURNED = "returned", "Returned"
        UNAPPROVED = "unapproved", "Unapproved"
        SAVED = "saved_to_documents", "Saved to the activity's documents"

    event_id = models.BigAutoField(primary_key=True)
    submission = models.ForeignKey(Submission, on_delete=models.CASCADE, related_name="events")
    action = models.CharField(max_length=30, choices=Action.choices)
    member = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="submission_events")
    on_behalf_of = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, null=True, blank=True,
                                     related_name="submission_events_on_behalf")
    reason = models.TextField(blank=True, default="")
    created_at = models.DateTimeField(auto_now_add=True)

    objects = SubmissionEventQuerySet.as_manager()

    class Meta:
        db_table = "submission_event"
        ordering = ["created_at", "event_id"]

    def __str__(self) -> str:
        return f"{self.submission} - {self.action}"

    def save(self, *args, **kwargs) -> None:
        if not self._state.adding:
            raise SubmissionEventIsAppendOnly("Submission history rows cannot be changed.")
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise SubmissionEventIsAppendOnly("Submission history rows cannot be deleted.")


class WorkAssignment(models.Model):
    """One piece of work the Director (can_assign_work) gives to a member (ASSUMPTIONS 437): the member gets a
    notification and permission for THIS task only (accounts.permissions.has_permission_for), until it is done or the
    assigner removes it. Never a role change. Changed only through approvals.assignments."""

    class Kind(models.TextChoices):
        ACTIVITY = "activity", "Work on the activity"
        CERTIFICATE_APPROVAL = "certificate_approval", "Approve the certificates"
        SIGNATORY_CHOICE = "signatory_choice", "Choose who signs the certificates"
        DOCUMENT_REVIEW = "document_review", "Review the document"
        SUBMISSION_REVIEW = "submission_review", "Review the submission"
        REPORT = "report", "Prepare a report"

    class Status(models.TextChoices):
        OPEN = "open", "Open"
        DONE = "done", "Done"
        REMOVED = "removed", "Removed"

    ACTIVITY_KINDS = ("activity", "certificate_approval", "signatory_choice")

    assignment_id = models.BigAutoField(primary_key=True)
    kind = models.CharField(max_length=30, choices=Kind.choices)
    # The work (exactly the one the kind needs; CASCADE: the assignment goes with its work, e.g. a deleted activity).
    activity = models.ForeignKey("activities.Activity", on_delete=models.CASCADE, null=True, blank=True,
                                 related_name="work_assignments")
    document = models.ForeignKey("documents.Document", on_delete=models.CASCADE, null=True, blank=True,
                                 related_name="work_assignments")
    submission = models.ForeignKey(Submission, on_delete=models.CASCADE, null=True, blank=True,
                                   related_name="work_assignments")
    report_title = models.CharField(max_length=255, blank=True, default="", help_text="Only for a report to prepare.")
    assigned_to = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="work_assigned_to_me")
    assigned_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="work_assigned_by_me")
    note = models.TextField(blank=True, default="")
    due_date = models.DateField(null=True, blank=True)
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.OPEN)
    created_at = models.DateTimeField(auto_now_add=True)
    completed_at = models.DateTimeField(null=True, blank=True)
    completed_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, null=True, blank=True,
                                     related_name="work_assignments_completed")
    removed_at = models.DateTimeField(null=True, blank=True)
    removed_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, null=True, blank=True,
                                   related_name="work_assignments_removed")
    # A report to prepare: the report the assignee generated for it (optional, set when marked done).
    generated_report = models.ForeignKey("reports.GeneratedReport", on_delete=models.PROTECT, null=True, blank=True,
                                         related_name="work_assignments")

    class Meta:
        db_table = "work_assignment"
        ordering = ["-created_at", "-assignment_id"]
        constraints = [
            models.CheckConstraint(condition=Q(kind__in=["activity", "certificate_approval", "signatory_choice",
                                                         "document_review", "submission_review", "report"]),
                                   name="work_assignment_kind_valid"),
            models.CheckConstraint(condition=Q(status__in=["open", "done", "removed"]), name="work_assignment_status_valid"),
            # Exactly the target the kind needs.
            models.CheckConstraint(
                condition=(Q(kind__in=["activity", "certificate_approval", "signatory_choice"], activity__isnull=False,
                             document__isnull=True, submission__isnull=True)
                           | Q(kind="document_review", document__isnull=False, activity__isnull=True,
                               submission__isnull=True)
                           | Q(kind="submission_review", submission__isnull=False, activity__isnull=True,
                               document__isnull=True)
                           | Q(kind="report", activity__isnull=True, document__isnull=True, submission__isnull=True)
                           & ~Q(report_title="")),
                name="work_assignment_target_matches_kind"),
            models.CheckConstraint(condition=~Q(assigned_to=models.F("assigned_by")), name="work_assignment_not_to_self"),
            models.CheckConstraint(
                condition=Q(status="open", completed_at__isnull=True, removed_at__isnull=True)
                | Q(status="done", completed_at__isnull=False, removed_at__isnull=True)
                | Q(status="removed", removed_at__isnull=False, removed_by__isnull=False),
                name="work_assignment_status_dates"),
            # One open assignment per piece of work.
            models.UniqueConstraint(fields=["kind", "activity"], condition=Q(status="open", activity__isnull=False),
                                    name="one_open_assignment_per_activity_task"),
            models.UniqueConstraint(fields=["document"], condition=Q(status="open", document__isnull=False),
                                    name="one_open_assignment_per_document"),
            models.UniqueConstraint(fields=["submission"], condition=Q(status="open", submission__isnull=False),
                                    name="one_open_assignment_per_submission"),
        ]

    def __str__(self) -> str:
        return f"{self.get_kind_display()} → {self.assigned_to}"
