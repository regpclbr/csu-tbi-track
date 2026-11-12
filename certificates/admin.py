"""Django admin for certificates.

- Certificates: view only, with a download link through the permission-checked view.
- Templates and signatories: added through certificates.services (image checks, activation),
  read-only afterwards (files never change), never deleted; (de)activated with list actions.
"""

from django import forms
from django.contrib import admin, messages
from django.core.exceptions import ValidationError
from django.urls import reverse
from django.utils.html import format_html

from accounts.admin import NoDeleteAdminMixin, ReadOnlyAdminMixin

from . import services
from .models import Certificate, CertificateSignatory, CertificateTemplate


class TemplateAddForm(forms.ModelForm):
    activate = forms.BooleanField(required=False, initial=True, label="Make this the active design")

    class Meta:
        model = CertificateTemplate
        fields = ("name", "file_path")
        labels = {"file_path": "Background image (PNG or JPG, A4 landscape)"}

    def clean_file_path(self):
        file = self.cleaned_data.get("file_path")
        try:
            services.validate_template_image(file)
        except ValidationError as error:
            raise ValidationError(error.message_dict["file"]) from error
        return file


class SignatoryAddForm(forms.ModelForm):
    class Meta:
        model = CertificateSignatory
        fields = ("name", "position", "signature_image_path")
        labels = {"signature_image_path": "Signature image (PNG or JPG, optional: it can be added later on an activity's Certificates tab)"}

    def clean_signature_image_path(self):
        file = self.cleaned_data.get("signature_image_path")
        if not file:
            return None
        try:
            services.validate_signature_image(file)
        except ValidationError as error:
            raise ValidationError(error.message_dict["file"]) from error
        return file

    def clean(self):
        # The service deactivates the current signatory of that position; skip the model's
        # "one active per position" check on this unsaved form instance.
        self.instance.is_active = False
        return super().clean()


def _copy_saved(target, saved) -> None:
    """The admin keeps using `obj` after save_model; give it the row the service created."""
    for field in saved._meta.concrete_fields:
        setattr(target, field.attname, getattr(saved, field.attname))
    target._state.adding = False


@admin.register(CertificateTemplate)
class CertificateTemplateAdmin(NoDeleteAdminMixin, admin.ModelAdmin):
    list_display = ("name", "is_active", "uploaded_by", "created_at", "image")
    list_filter = ("is_active",)
    actions = ["activate_selected", "deactivate_selected"]

    def get_form(self, request, obj=None, **kwargs):
        if obj is None:
            kwargs["form"] = TemplateAddForm
        return super().get_form(request, obj, **kwargs)

    def get_fields(self, request, obj=None):
        return ("name", "file_path", "activate") if obj is None else ("name", "image", "is_active", "uploaded_by", "created_at")

    def get_readonly_fields(self, request, obj=None):
        return () if obj is None else ("name", "image", "is_active", "uploaded_by", "created_at")

    def save_model(self, request, obj, form, change) -> None:
        template = services.upload_template(
            request.user, form.cleaned_data["name"], form.cleaned_data["file_path"], activate=form.cleaned_data["activate"]
        )
        _copy_saved(obj, template)

    @admin.display(description="Image")
    def image(self, obj: CertificateTemplate) -> str:
        return format_html('<a href="{}">Download</a>', reverse("certificates:template_image", args=[obj.pk])) if obj.pk else "-"

    @admin.action(description="Make the selected template the active design (select one)")
    def activate_selected(self, request, queryset) -> None:
        if queryset.count() != 1:
            self.message_user(request, "Select exactly one template to activate.", messages.ERROR)
            return
        template = services.activate_template(request.user, queryset.get())
        self.message_user(request, f'"{template.name}" is now the active design.', messages.SUCCESS)

    @admin.action(description="Deactivate selected templates")
    def deactivate_selected(self, request, queryset) -> None:
        for template in queryset:
            services.deactivate_template(request.user, template)
        self.message_user(request, f"Deactivated {queryset.count()} template(s).", messages.SUCCESS)


@admin.register(CertificateSignatory)
class CertificateSignatoryAdmin(NoDeleteAdminMixin, admin.ModelAdmin):
    list_display = ("name", "position", "is_active", "created_at", "signature")
    list_filter = ("position", "is_active")
    actions = ["deactivate_selected"]

    def get_form(self, request, obj=None, **kwargs):
        if obj is None:
            kwargs["form"] = SignatoryAddForm
        return super().get_form(request, obj, **kwargs)

    def get_fields(self, request, obj=None):
        return ("name", "position", "signature_image_path") if obj is None else ("name", "position", "signature", "is_active", "created_at")

    def get_readonly_fields(self, request, obj=None):
        return () if obj is None else ("name", "position", "signature", "is_active", "created_at")

    def save_model(self, request, obj, form, change) -> None:
        signatory = services.add_signatory(
            request.user, form.cleaned_data["name"], form.cleaned_data["position"], form.cleaned_data["signature_image_path"]
        )
        _copy_saved(obj, signatory)

    @admin.display(description="Signature")
    def signature(self, obj: CertificateSignatory) -> str:
        if not obj.pk or not obj.signature_image_path:
            return "Not added yet"
        return format_html('<a href="{}">Download</a>', reverse("certificates:signature_image", args=[obj.pk]))

    @admin.action(description="Deactivate selected signatories")
    def deactivate_selected(self, request, queryset) -> None:
        for signatory in queryset:
            services.deactivate_signatory(request.user, signatory)
        self.message_user(request, f"Deactivated {queryset.count()} signatory(ies).", messages.SUCCESS)


@admin.register(Certificate)
class CertificateAdmin(ReadOnlyAdminMixin, admin.ModelAdmin):
    """View only: every change goes through certificates.services (status rules, audit by log)."""

    list_display = ("certificate_number", "recipient_type", "activity", "status", "delivery_method", "issued_at", "sent_at", "pdf")
    list_filter = ("status", "recipient_type", "delivery_method")
    search_fields = ("certificate_number",)
    exclude = ("file_path",)
    readonly_fields = ("pdf",)

    @admin.display(description="PDF")
    def pdf(self, obj: Certificate) -> str:
        if not obj.file_path:
            return "-"
        return format_html('<a href="{}">Download</a>', reverse("certificates:download", args=[obj.pk]))
