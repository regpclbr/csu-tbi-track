from django.contrib import admin

from .models import GeneratedReport


@admin.register(GeneratedReport)
class GeneratedReportAdmin(admin.ModelAdmin):
    list_display = ("report_type", "format", "date_range_start", "date_range_end", "generated_by", "generated_at")
    list_filter = ("format", "report_type")
