from django.contrib import admin

from accounts.admin import NoDeleteAdminMixin

from .models import Certificate, CertificateSignatory, CertificateTemplate


@admin.register(CertificateTemplate)
class CertificateTemplateAdmin(NoDeleteAdminMixin, admin.ModelAdmin):
    list_display = ("name", "is_active", "uploaded_by", "created_at")


@admin.register(CertificateSignatory)
class CertificateSignatoryAdmin(NoDeleteAdminMixin, admin.ModelAdmin):
    list_display = ("name", "position", "is_active", "created_at")
    list_filter = ("position", "is_active")


@admin.register(Certificate)
class CertificateAdmin(admin.ModelAdmin):
    list_display = ("certificate_number", "recipient_type", "activity", "status", "delivery_method", "sent_at")
    list_filter = ("status", "recipient_type", "delivery_method")
    search_fields = ("certificate_number",)
