from django.contrib import admin, messages
from django.core.exceptions import PermissionDenied

from accounts.admin import ReadOnlyAdminMixin

from . import services
from .models import Startup


@admin.register(Startup)
class StartupAdmin(ReadOnlyAdminMixin, admin.ModelAdmin):
    """View only: Navigatu data cannot be edited in this system (it comes from the sync)."""

    list_display = ("name", "email", "navigatu_id", "maturity_stage", "is_active", "last_synced_at")
    list_filter = ("is_active", "maturity_stage")
    search_fields = ("name", "email", "navigatu_id")
    exclude = ("password",)  # the hash is never shown or edited
    actions = ["send_password_links"]

    @admin.action(description="Send set-password link to selected startups")
    def send_password_links(self, request, queryset) -> None:
        sent = 0
        for startup in queryset:
            try:
                services.send_password_link(request.user, startup)
                sent += 1
            except (services.StartupError, PermissionDenied) as error:
                self.message_user(request, f"{startup.name}: {error}", messages.ERROR)
        if sent:
            self.message_user(request, f"Set-password link emailed to {sent} startup(s).", messages.SUCCESS)
