"""GENERATED_REPORT (PDF or Excel reports made by TBI members) and the report generator's tables (ASSUMPTIONS 442-445)."""

from django.conf import settings
from django.db import models
from django.db.models import Q


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
    file_path = models.FileField(upload_to="reports/%Y/%m/", max_length=255)
    generated_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "generated_report"
        ordering = ["-generated_at"]

    def __str__(self) -> str:
        return f"{self.report_type} ({self.generated_at:%Y-%m-%d})"


# --- The report generator (ASSUMPTIONS 442-445) ---------------------------------------------------------------------
# A member's draft (REPORT_DRAFT) is written by the System in versions (REPORT_DRAFT_VERSION); the conversation that
# asked for them is REPORT_CHAT_MESSAGE; files the member adds are REPORT_ATTACHMENT. Rules: reports/generator.py.
# Paper attendance sheets read by the AI (PAPER_SHEET_READ + PAPER_SHEET_ROW) are used only after staff confirm them:
# reports/paper_sheets.py.


def attachment_upload_to(instance, filename: str) -> str:
    """Never the uploaded name: reports.generator stores attachments under report-drafts/<year>/ with a random name."""
    return f"report-drafts/{filename}"


class ReportDraft(models.Model):
    """A report the member is writing with the generator (Activity Report, Evaluation Summary or a free-form one), for
    one activity or a period."""

    class Kind(models.TextChoices):
        ACTIVITY_REPORT = "activity_report", "Activity Report"
        EVALUATION_SUMMARY = "evaluation_summary", "Evaluation Summary"
        OTHER = "other", "Other (free form)"

    draft_id = models.BigAutoField(primary_key=True)
    kind = models.CharField(max_length=30, choices=Kind.choices)
    title = models.CharField(max_length=255)
    activity = models.ForeignKey("activities.Activity", on_delete=models.CASCADE, null=True, blank=True,
                                 related_name="report_drafts")
    date_range_start = models.DateField(null=True, blank=True)
    date_range_end = models.DateField(null=True, blank=True)
    options = models.JSONField(default=dict, blank=True,
                               help_text="Office line, implementing unit, project leader (typed by the member).")
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="report_drafts")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "report_draft"
        ordering = ["-updated_at", "-draft_id"]
        constraints = [
            models.CheckConstraint(
                condition=Q(activity__isnull=False, date_range_start__isnull=True, date_range_end__isnull=True)
                | Q(activity__isnull=True, date_range_start__isnull=False, date_range_end__isnull=False,
                    date_range_end__gte=models.F("date_range_start")),
                name="report_draft_activity_or_period",
            ),
        ]

    def __str__(self) -> str:
        return self.title


class ReportDraftVersion(models.Model):
    """One version of a draft: what the System wrote (or the member edited). `facts` = every number, date, name list and
    amount, from the database at that moment; `sections` = the narrative parts. Never changed once ready, except
    which document / submission it was saved as."""

    class Source(models.TextChoices):
        GENERATED = "generated", "Written by the AI"
        REVISED = "revised", "Revised by the AI"
        EDITED = "edited", "Edited by the member"

    class State(models.TextChoices):
        PENDING = "pending", "Waiting for the background worker"
        RUNNING = "running", "Being written"
        READY = "ready", "Ready"
        FAILED = "failed", "Failed"

    version_id = models.BigAutoField(primary_key=True)
    draft = models.ForeignKey(ReportDraft, on_delete=models.CASCADE, related_name="versions")
    number = models.PositiveIntegerField()
    source = models.CharField(max_length=20, choices=Source.choices)
    state = models.CharField(max_length=20, choices=State.choices, default=State.PENDING)
    progress = models.CharField(max_length=200, blank=True, default="")
    error = models.CharField(max_length=500, blank=True, default="", help_text="Plain words, never a raw message.")
    sections = models.JSONField(default=dict, blank=True)
    facts = models.JSONField(default=dict, blank=True)
    ai_prompt = models.TextField(null=True, blank=True, help_text="Exactly what was sent to Gemini (None: nothing).")
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="+")
    created_at = models.DateTimeField(auto_now_add=True)
    finished_at = models.DateTimeField(null=True, blank=True)
    saved_document = models.OneToOneField("documents.Document", on_delete=models.SET_NULL, null=True, blank=True,
                                          related_name="report_draft_version")
    submission = models.OneToOneField("approvals.Submission", on_delete=models.SET_NULL, null=True, blank=True,
                                      related_name="report_draft_version")

    class Meta:
        db_table = "report_draft_version"
        ordering = ["draft", "number"]
        constraints = [models.UniqueConstraint(fields=["draft", "number"], name="unique_version_number_per_draft")]

    def __str__(self) -> str:
        return f"{self.draft} v{self.number}"


class ReportChatMessage(models.Model):
    """The conversation of a draft: the member's requests and the System's short replies."""

    class Role(models.TextChoices):
        USER = "user", "Member"
        ASSISTANT = "assistant", "Report generator"

    message_id = models.BigAutoField(primary_key=True)
    draft = models.ForeignKey(ReportDraft, on_delete=models.CASCADE, related_name="messages")
    role = models.CharField(max_length=20, choices=Role.choices)
    content = models.TextField()
    version = models.ForeignKey(ReportDraftVersion, on_delete=models.SET_NULL, null=True, blank=True,
                                related_name="messages")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "report_chat_message"
        ordering = ["created_at", "message_id"]


class ReportAttachment(models.Model):
    """A file the member adds to a draft: a document the AI reads (program, speaker list, notes) or a photo for the Photo
    Documentation page (never sent to the AI)."""

    class Kind(models.TextChoices):
        DOCUMENT = "document", "Document for the AI to read"
        PHOTO = "photo", "Photo for the report"

    class TextStatus(models.TextChoices):
        PENDING = "pending", "Not read yet"
        READ = "read", "Read"
        NOT_READABLE = "not_readable", "Could not be read"
        NOT_SENT = "not_sent", "Not sent to the AI"

    attachment_id = models.BigAutoField(primary_key=True)
    draft = models.ForeignKey(ReportDraft, on_delete=models.CASCADE, related_name="attachments")
    kind = models.CharField(max_length=20, choices=Kind.choices)
    file_path = models.FileField(upload_to=attachment_upload_to, max_length=255)
    file_name = models.CharField(max_length=255, help_text="The uploaded name, for display only (never in a path).")
    file_extension = models.CharField(max_length=10)
    file_size_kb = models.PositiveIntegerField()
    text = models.TextField(blank=True, default="", help_text="What the System read (documents only).")
    text_status = models.CharField(max_length=20, choices=TextStatus.choices, default=TextStatus.PENDING)
    text_note = models.CharField(max_length=300, blank=True, default="")
    uploaded_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="+")
    uploaded_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "report_attachment"
        ordering = ["uploaded_at", "attachment_id"]

    def __str__(self) -> str:
        return self.file_name


class PaperSheetRead(models.Model):
    """The AI's reading of ONE scanned paper Attendance / Confirmation of Attendance sheet (an activity document or a
    generator attachment). Its rows count nowhere until staff confirm them."""

    class SheetKind(models.TextChoices):
        ATTENDANCE = "attendance", "Attendance"
        CONFIRMATION = "confirmation", "Confirmation of Attendance"

    class State(models.TextChoices):
        READING = "reading", "Being read"
        READ = "read", "Read, waiting for staff to check"
        FAILED = "failed", "Could not be read"
        CONFIRMED = "confirmed", "Checked and confirmed"

    read_id = models.BigAutoField(primary_key=True)
    activity = models.ForeignKey("activities.Activity", on_delete=models.CASCADE, related_name="paper_sheets")
    document = models.ForeignKey("documents.Document", on_delete=models.CASCADE, null=True, blank=True,
                                 related_name="paper_sheet_reads")
    attachment = models.ForeignKey(ReportAttachment, on_delete=models.CASCADE, null=True, blank=True,
                                   related_name="paper_sheet_reads")
    sheet_kind = models.CharField(max_length=20, choices=SheetKind.choices)
    day = models.DateField(null=True, blank=True)
    state = models.CharField(max_length=20, choices=State.choices, default=State.READING)
    error = models.CharField(max_length=500, blank=True, default="")
    read_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="+")
    read_at = models.DateTimeField(auto_now_add=True)
    finished_at = models.DateTimeField(null=True, blank=True)
    confirmed_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, null=True, blank=True,
                                     related_name="+")
    confirmed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        db_table = "paper_sheet_read"
        ordering = ["-read_at", "-read_id"]
        constraints = [
            models.CheckConstraint(condition=Q(document__isnull=False, attachment__isnull=True)
                                   | Q(document__isnull=True, attachment__isnull=False),
                                   name="paper_sheet_one_file"),
            models.CheckConstraint(condition=Q(state="confirmed", confirmed_by__isnull=False, confirmed_at__isnull=False)
                                   | (~Q(state="confirmed") & Q(confirmed_by__isnull=True, confirmed_at__isnull=True)),
                                   name="paper_sheet_confirmed_with_who_and_when"),
        ]


class PaperSheetRow(models.Model):
    """One row the AI read from a paper sheet, as staff corrected it."""

    row_id = models.BigAutoField(primary_key=True)
    sheet = models.ForeignKey(PaperSheetRead, on_delete=models.CASCADE, related_name="rows")
    row_number = models.PositiveIntegerField()
    name = models.CharField(max_length=255, blank=True, default="")
    designation = models.CharField(max_length=255, blank=True, default="")
    office = models.CharField(max_length=255, blank=True, default="")
    participant_category = models.ForeignKey("activities.ParticipantCategory", on_delete=models.PROTECT, null=True,
                                             blank=True, related_name="+")
    category_by_staff = models.BooleanField(default=False, help_text="False: suggested from the office by the rule.")
    is_excluded = models.BooleanField(default=False, help_text="Not a participant (a heading, an empty line, a mistake).")
    edited_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, null=True, blank=True,
                                  related_name="+")
    edited_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        db_table = "paper_sheet_row"
        ordering = ["sheet", "row_number", "row_id"]
