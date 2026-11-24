"""DOCUMENT and its lookup tables."""

from django.conf import settings
from django.db import models
from django.db.models import Q


class DocumentStatus(models.Model):
    """Lookup: For Review, Approved. Never deleted."""

    FOR_REVIEW = "For Review"
    APPROVED = "Approved"

    document_status_id = models.BigAutoField(primary_key=True)
    status_name = models.CharField(max_length=50, unique=True)
    description = models.CharField(max_length=255, blank=True)

    class Meta:
        db_table = "document_status"
        verbose_name_plural = "document statuses"

    def __str__(self) -> str:
        return self.status_name


class DocumentType(models.Model):
    """Kind of document, and when it is due if required. Deactivate instead of deleting.

    Due date = activity.<due_reference> + due_offset_days (e.g. start_date - 7).
    """

    # The catch-all optional type (seed_data): a document of this type needs its own title (ASSUMPTIONS 386).
    OTHER = "Other"

    class DueReference(models.TextChoices):
        START_DATE = "start_date", "Start date"
        END_DATE = "end_date", "End date"

    document_type_id = models.BigAutoField(primary_key=True)
    name = models.CharField(max_length=100, unique=True)
    description = models.CharField(max_length=255, blank=True)
    naming_pattern = models.CharField(max_length=255, blank=True, help_text="Folder/naming convention to follow.")
    is_required = models.BooleanField(default=False)
    due_reference = models.CharField(max_length=20, choices=DueReference.choices, null=True, blank=True)
    due_offset_days = models.IntegerField(null=True, blank=True, help_text="For example -3 or +3.")
    is_active = models.BooleanField(default=True)
    # ASSUMPTIONS 432 (NAVIGATU: only approved paper copies are uploaded): False = an upload is saved as Approved at once
    # (an approved copy, by System). True = the Director reviews uploads of this type (approve / return, item 344).
    needs_review = models.BooleanField(
        default=False,
        help_text="Off: uploads of this type are approved copies (saved as Approved). On: the Director reviews them.")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "document_type"
        ordering = ["name"]
        constraints = [
            # A required document must have a due rule.
            models.CheckConstraint(
                condition=Q(is_required=False) | Q(due_reference__isnull=False, due_offset_days__isnull=False),
                name="required_document_type_has_due_rule",
            ),
        ]

    def __str__(self) -> str:
        return self.name


def document_upload_to(instance: "Document", filename: str) -> str:
    """The full path is built by documents.services.build_file_path() from DOCUMENT_TYPE.naming_pattern.

    It is used as given here, so the uploaded file's own name never ends up in the path.
    """
    return filename


class DocumentQuerySet(models.QuerySet):
    """Documents with related_certificate set belong to the Certificates tab."""

    def activity_documents(self) -> "DocumentQuerySet":
        """Documents for the checklist and the Document Repository (no certificate files)."""
        return self.filter(related_certificate__isnull=True)

    def certificate_documents(self) -> "DocumentQuerySet":
        """Documents shown only in the Certificates tab."""
        return self.filter(related_certificate__isnull=False)

    def approved(self) -> "DocumentQuerySet":
        """Documents whose status is Approved (the only ones that get embeddings)."""
        return self.filter(status__status_name=DocumentStatus.APPROVED)

    def waiting_for_reviewer(self) -> "DocumentQuerySet":
        """For Review and not returned (reviewed_at empty): a reviewer has to act."""
        return self.filter(status__status_name=DocumentStatus.FOR_REVIEW, reviewed_at__isnull=True)

    def returned(self) -> "DocumentQuerySet":
        """For Review but returned by a reviewer (reviewed_at set): the uploader has to replace the file."""
        return self.filter(status__status_name=DocumentStatus.FOR_REVIEW, reviewed_at__isnull=False)


class Document(models.Model):
    """An uploaded file for an activity. Must be reviewed before it counts as Approved."""

    document_id = models.BigAutoField(primary_key=True)
    activity = models.ForeignKey("activities.Activity", on_delete=models.PROTECT, related_name="documents")
    document_type = models.ForeignKey(DocumentType, on_delete=models.PROTECT, related_name="documents")
    uploaded_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="uploaded_documents"
    )
    reviewed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="reviewed_documents",
    )
    reviewed_at = models.DateTimeField(null=True, blank=True)
    status = models.ForeignKey(DocumentStatus, on_delete=models.PROTECT, related_name="documents")
    related_certificate = models.ForeignKey(
        "certificates.Certificate",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="documents",
        help_text="Set for certificate files; these are excluded from the checklist and repository.",
    )
    title = models.CharField(max_length=255)
    file_name = models.CharField(max_length=255)
    file_path = models.FileField(upload_to=document_upload_to, max_length=255)
    file_extension = models.CharField(max_length=10)
    file_size_kb = models.PositiveIntegerField()
    uploaded_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    objects = DocumentQuerySet.as_manager()

    class Meta:
        db_table = "document"
        ordering = ["-uploaded_at"]

    def __str__(self) -> str:
        return self.title

    # Review state, calculated from status + reviewed_at (no extra column):
    WAITING_FOR_REVIEW = "waiting_for_review"  # For Review, reviewed_at empty
    RETURNED = "returned"  # For Review, reviewed_at set: returned to the uploader
    APPROVED = "approved"

    @property
    def review_state(self) -> str:
        if self.status.status_name == DocumentStatus.APPROVED:
            return self.APPROVED
        return self.RETURNED if self.reviewed_at else self.WAITING_FOR_REVIEW

