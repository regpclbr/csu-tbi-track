from django.contrib import admin

from .models import Startup


@admin.register(Startup)
class StartupAdmin(admin.ModelAdmin):
    list_display = ("name", "email", "navigatu_id", "maturity_stage", "is_active", "last_synced_at")
    list_filter = ("is_active", "maturity_stage")
    search_fields = ("name", "email", "navigatu_id")
    exclude = ("password",)  # the hash is never edited by hand
