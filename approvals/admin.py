from django.contrib import admin

from accounts.admin import ReadOnlyAdminMixin

from .models import Submission, SubmissionEvent


@admin.register(Submission)
class SubmissionAdmin(ReadOnlyAdminMixin, admin.ModelAdmin):
    """View only: submissions change only through approvals.services (ASSUMPTIONS 437)."""

    list_display = ("title", "status", "submitted_by", "activity", "submitted_at", "reviewed_by")
    list_filter = ("status",)
    list_select_related = ("submitted_by", "activity", "reviewed_by")


@admin.register(SubmissionEvent)
class SubmissionEventAdmin(ReadOnlyAdminMixin, admin.ModelAdmin):
    """View only: the append-only history of each submission."""

    list_display = ("submission", "action", "member", "on_behalf_of", "created_at")
    list_filter = ("action",)
    list_select_related = ("submission", "member", "on_behalf_of")
