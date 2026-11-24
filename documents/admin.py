from django import forms
from django.contrib import admin
from django.urls import reverse
from django.utils.html import format_html

from accounts.admin import NoDeleteAdminMixin, ReadOnlyAdminMixin

from . import services
from .models import Document, DocumentStatus, DocumentType


@admin.register(DocumentStatus)
class DocumentStatusAdmin(ReadOnlyAdminMixin, admin.ModelAdmin):
    """View only: the app relies on the fixed names For Review and Approved."""

    list_display = ("status_name", "description")


class DocumentTypeAdminForm(forms.ModelForm):
    class Meta:
        model = DocumentType
        fields = "__all__"
        help_texts = {
            "naming_pattern": (
                "Where uploaded files are stored, under documents/. Placeholders: "
                + ", ".join("{%s} = %s" % item for item in services.NAMING_PLACEHOLDERS.items())
                + f". Leave empty to use {services.FALLBACK_NAMING_PATTERN}. The extension is added automatically."
            ),
        }

    def clean_naming_pattern(self) -> str:
        return services.validate_naming_pattern(self.cleaned_data.get("naming_pattern", ""))


@admin.register(DocumentType)
class DocumentTypeAdmin(NoDeleteAdminMixin, admin.ModelAdmin):
    form = DocumentTypeAdminForm
    list_display = ("name", "is_required", "due_reference", "due_offset_days", "naming_pattern", "needs_review",
                    "is_active")
    list_filter = ("is_required", "needs_review", "is_active")


@admin.register(Document)
class DocumentAdmin(ReadOnlyAdminMixin, admin.ModelAdmin):
    """View only. Uploads and reviews go through documents.services (JSON API); the file is
    downloaded through the permission-checked view, never from /media/."""

    list_display = (
        "title", "activity", "document_type", "status", "review_state_label", "uploaded_by", "uploaded_at", "download_link",
    )
    list_filter = ("status", "document_type")
    search_fields = ("title", "file_name")
    list_select_related = ("activity", "document_type", "status", "uploaded_by")
    exclude = ("file_path",)  # the default widget would link to /media/
    readonly_fields = ("download_link", "review_state_label")

    @admin.display(description="Review state")
    def review_state_label(self, document: Document) -> str:
        return document.review_state.replace("_", " ").capitalize()

    @admin.display(description="File")
    def download_link(self, document: Document) -> str:
        if document.related_certificate_id:
            return "Certificate file (Step 9)"
        return format_html('<a href="{}">Download</a>', reverse("documents:download", args=[document.pk]))
