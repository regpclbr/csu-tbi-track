"""DOCUMENT, its lookup tables, and NOTIFICATION."""

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
    file_path = models.FileField(upload_to="documents/%Y/%m/")
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


class Notification(models.Model):
    """An in-app notification for exactly one recipient: a TBI member or a startup."""

    notification_id = models.BigAutoField(primary_key=True)
    member = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, null=True, blank=True, related_name="notifications"
    )
    startup = models.ForeignKey(
        "startups.Startup", on_delete=models.CASCADE, null=True, blank=True, related_name="notifications"
    )
    activity = models.ForeignKey(
        "activities.Activity", on_delete=models.CASCADE, null=True, blank=True, related_name="notifications"
    )
    document_type = models.ForeignKey(
        DocumentType, on_delete=models.PROTECT, null=True, blank=True, related_name="notifications"
    )
    message = models.CharField(max_length=500)
    type = models.CharField(max_length=50, help_text="For example: document_reminder.")
    created_at = models.DateTimeField(auto_now_add=True)
    is_read = models.BooleanField(default=False)

    class Meta:
        db_table = "notification"
        ordering = ["-created_at"]
        constraints = [
            models.CheckConstraint(
                condition=Q(member__isnull=False, startup__isnull=True)
                | Q(member__isnull=True, startup__isnull=False),
                name="notification_has_exactly_one_recipient",
            ),
        ]

    def __str__(self) -> str:
        return self.message[:50]
