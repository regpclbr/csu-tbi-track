from django.contrib import admin

from accounts.admin import ReadOnlyAdminMixin

from .models import Notification


@admin.register(Notification)
class NotificationAdmin(ReadOnlyAdminMixin, admin.ModelAdmin):
    """View only: notifications are created by the services (reminders, returned documents)."""

    list_display = ("message", "type", "member", "activity", "is_read", "created_at")
    list_filter = ("type", "is_read")
    list_select_related = ("member", "activity")
