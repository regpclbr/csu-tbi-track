from django.contrib import admin
from django.urls import reverse
from django.utils.html import format_html

from . import services
from .models import GeneratedReport


@admin.register(GeneratedReport)
class GeneratedReportAdmin(admin.ModelAdmin):
    """View only: reports are made through reports.services (API or `generate_report`) and never deleted."""

    list_display = ("report_id", "report_type", "format", "date_range_start", "date_range_end", "activity_type",
                    "generated_by", "generated_at", "state", "download")
    list_filter = ("format", "report_type")
    readonly_fields = ("report_id", "generated_by", "report_type", "included_sections", "date_range_start",
                       "date_range_end", "activity_type", "format", "file_path", "ai_prompt", "generated_at")

    def has_add_permission(self, request) -> bool:
        return False

    def has_change_permission(self, request, obj=None) -> bool:
        return False

    def has_delete_permission(self, request, obj=None) -> bool:
        return False

    @admin.display(description="State")
    def state(self, report: GeneratedReport) -> str:
        return services.report_state(report)

    @admin.display(description="File")
    def download(self, report: GeneratedReport) -> str:
        if not report.file_path:
            return "-"
        return format_html('<a href="{}">Download</a>', reverse("reports:download", args=[report.pk]))
