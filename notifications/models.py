"""NOTIFICATION: in-app messages for TBI members and startups."""

from django.conf import settings
from django.db import models
from django.db.models import Q


class Notification(models.Model):
    """An in-app notification for exactly one recipient: a TBI member or a startup."""

    # Values of `type`. A reminder is sent at most once per (member, activity, document type, type).
    DOCUMENT_DUE_SOON = "document_due_soon"
    DOCUMENT_OVERDUE = "document_overdue"
    DOCUMENT_RETURNED = "document_returned"
    DOCUMENT_WAITING_FOR_REVIEW = "document_waiting_for_review"  # to members who can approve documents, not the uploader
    ACTIVITY_CREATED = "activity_created"  # to every active Director when someone else creates an activity (ASSUMPTIONS 361)
    # to every certificate approver (can_approve_certificates, ASSUMPTIONS 421) when someone else prepares an
    # activity's certificates before Conducted (ASSUMPTIONS 420)
    CERTIFICATES_PREPARED = "certificates_prepared"
    # to every certificate approver, once per activity, when the System first creates certificates for an activity that
    # is not approved (ASSUMPTIONS 421)
    CERTIFICATES_WAITING = "certificates_waiting"
    CERTIFICATE_TYPES = (CERTIFICATES_PREPARED, CERTIFICATES_WAITING)  # both open the Certificates tab
    REMINDER_TYPES = (DOCUMENT_DUE_SOON, DOCUMENT_OVERDUE)

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
        "documents.DocumentType", on_delete=models.PROTECT, null=True, blank=True, related_name="notifications"
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
            # Never the same reminder twice, even when two runs happen at the same moment.
            # Returned-document notifications are not limited (a document can be returned again).
            models.UniqueConstraint(
                fields=["member", "activity", "document_type", "type"],
                condition=Q(type__in=["document_due_soon", "document_overdue"]),
                name="unique_document_reminder",
            ),
        ]

    def __str__(self) -> str:
        return self.message[:50]
