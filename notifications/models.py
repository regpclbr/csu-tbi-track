"""NOTIFICATION: in-app messages for TBI members and startups."""

from django.conf import settings
from django.db import models
from django.db.models import Q


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
        ]

    def __str__(self) -> str:
        return self.message[:50]
