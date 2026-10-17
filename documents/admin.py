from django.contrib import admin

from accounts.admin import NoDeleteAdminMixin

from .models import Document, DocumentStatus, DocumentType


@admin.register(DocumentStatus)
class DocumentStatusAdmin(NoDeleteAdminMixin, admin.ModelAdmin):
    list_display = ("status_name", "description")


@admin.register(DocumentType)
class DocumentTypeAdmin(NoDeleteAdminMixin, admin.ModelAdmin):
    list_display = ("name", "is_required", "due_reference", "due_offset_days", "is_active")
    list_filter = ("is_required", "is_active")


@admin.register(Document)
class DocumentAdmin(admin.ModelAdmin):
    list_display = ("title", "activity", "document_type", "status", "uploaded_by", "uploaded_at", "related_certificate")
    list_filter = ("status", "document_type")
    search_fields = ("title", "file_name")

