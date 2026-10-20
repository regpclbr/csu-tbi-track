"""Admin for forms, registrations and evaluations.

Registrations, answers, evaluations and activity forms are view only here (forms are
managed through the staff API, so the "locked once answered" rule always applies).
Attendance is changed with the list actions, which call the same services.
Default form fields are editable by the Admin.
"""

from django import forms
from django.contrib import admin, messages
from django.core.exceptions import ValidationError

from accounts.admin import ReadOnlyAdminMixin

from . import services
from .models import DefaultFormField, EvaluationSubmission, FormAnswer, FormField, FormTemplate, Registration


class DefaultFormFieldForm(forms.ModelForm):
    class Meta:
        model = DefaultFormField
        fields = ("activity_type", "form_type", "field_label", "field_type", "options", "is_required", "sort_order")

    def clean(self) -> dict:
        cleaned = super().clean()
        if "field_type" in cleaned:
            try:
                cleaned["options"] = services.clean_options(cleaned["field_type"], cleaned.get("options", ""))
            except ValidationError as error:
                self.add_error("options", error.message_dict["options"])
        return cleaned


@admin.register(DefaultFormField)
class DefaultFormFieldAdmin(admin.ModelAdmin):
    """Placeholder defaults (to validate with the TBI). Options are a JSON list, e.g. ["Yes", "No"]."""

    form = DefaultFormFieldForm
    list_display = ("field_label", "activity_type", "form_type", "field_type", "is_required", "sort_order")
    list_filter = ("activity_type", "form_type")


class FormFieldInline(ReadOnlyAdminMixin, admin.TabularInline):
    model = FormField
    extra = 0
    fields = ("sort_order", "field_label", "field_type", "options", "is_required")


@admin.register(FormTemplate)
class FormTemplateAdmin(ReadOnlyAdminMixin, admin.ModelAdmin):
    list_display = ("title", "activity", "form_type", "generated_from_default", "created_by")
    list_filter = ("form_type",)
    inlines = [FormFieldInline]


@admin.register(FormField)
class FormFieldAdmin(ReadOnlyAdminMixin, admin.ModelAdmin):
    list_display = ("field_label", "template", "field_type", "is_required", "sort_order")


class FormAnswerInline(ReadOnlyAdminMixin, admin.TabularInline):
    model = FormAnswer
    fk_name = "registration"
    extra = 0
    fields = ("field", "answer_value")


@admin.register(Registration)
class RegistrationAdmin(ReadOnlyAdminMixin, admin.ModelAdmin):
    list_display = (
        "name", "email", "activity", "participant_category", "registration_date",
        "attendance_confirmed", "confirmation_method",
    )
    list_filter = ("attendance_confirmed", "participant_category", "activity")
    search_fields = ("name", "email")
    inlines = [FormAnswerInline]
    actions = ["mark_attended_email", "mark_attended_wet_signature", "unmark_attendance"]

    def _set(self, request, queryset, attended: bool, method: str | None) -> None:
        """Group by activity and call the attendance service (all or nothing per activity)."""
        by_activity: dict = {}
        for registration in queryset.select_related("activity"):
            by_activity.setdefault(registration.activity, []).append(registration.pk)
        for activity, ids in by_activity.items():
            try:
                changed = services.set_attendance(request.user, activity, ids, attended, method)
            except services.RegistrationError as error:
                self.message_user(request, f'"{activity.title}": {error}', level=messages.ERROR)
            else:
                self.message_user(request, f'"{activity.title}": attendance updated for {changed}.', messages.SUCCESS)

    @admin.action(description="Mark attended (confirmed by email)")
    def mark_attended_email(self, request, queryset) -> None:
        self._set(request, queryset, True, Registration.ConfirmationMethod.EMAIL)

    @admin.action(description="Mark attended (wet signature)")
    def mark_attended_wet_signature(self, request, queryset) -> None:
        self._set(request, queryset, True, Registration.ConfirmationMethod.WET_SIGNATURE)

    @admin.action(description="Unmark attendance")
    def unmark_attendance(self, request, queryset) -> None:
        self._set(request, queryset, False, None)


@admin.register(EvaluationSubmission)
class EvaluationSubmissionAdmin(ReadOnlyAdminMixin, admin.ModelAdmin):
    list_display = ("registration", "template", "submitted_at")


@admin.register(FormAnswer)
class FormAnswerAdmin(ReadOnlyAdminMixin, admin.ModelAdmin):
    list_display = ("field", "answer_source", "registration", "evaluation_submission", "answer_value")
    list_filter = ("answer_source",)
