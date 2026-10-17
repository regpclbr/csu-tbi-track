"""GENERATED_REPORT: PDF or Excel reports made by TBI members."""

from django.conf import settings
from django.db import models


class GeneratedReport(models.Model):
    """A report file, with the filters that were used to make it."""

    class Format(models.TextChoices):
        PDF = "pdf", "PDF"
        EXCEL = "excel", "Excel"

    report_id = models.BigAutoField(primary_key=True)
    generated_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="generated_reports"
    )
    report_type = models.CharField(max_length=100, help_text="For example: accomplishment report.")
    ai_prompt = models.TextField(null=True, blank=True)
    included_sections = models.CharField(max_length=500, blank=True)
    date_range_start = models.DateField()
    date_range_end = models.DateField()
    activity_type = models.ForeignKey(
        "activities.ActivityType",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="generated_reports",
        help_text="Optional filter.",
    )
    format = models.CharField(max_length=10, choices=Format.choices)
    file_path = models.FileField(upload_to="reports/%Y/%m/")
    generated_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "generated_report"
        ordering = ["-generated_at"]

    def __str__(self) -> str:
        return f"{self.report_type} ({self.generated_at:%Y-%m-%d})"
