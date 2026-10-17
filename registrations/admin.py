from django.contrib import admin

from .models import DefaultFormField, EvaluationSubmission, FormAnswer, FormField, FormTemplate, Registration


@admin.register(DefaultFormField)
class DefaultFormFieldAdmin(admin.ModelAdmin):
    list_display = ("field_label", "activity_type", "form_type", "field_type", "is_required", "sort_order")
    list_filter = ("activity_type", "form_type")


class FormFieldInline(admin.TabularInline):
    model = FormField
    extra = 0


@admin.register(FormTemplate)
class FormTemplateAdmin(admin.ModelAdmin):
    list_display = ("title", "activity", "form_type", "generated_from_default", "created_by")
    list_filter = ("form_type",)
    inlines = [FormFieldInline]


@admin.register(FormField)
class FormFieldAdmin(admin.ModelAdmin):
    list_display = ("field_label", "template", "field_type", "is_required", "sort_order")


@admin.register(Registration)
class RegistrationAdmin(admin.ModelAdmin):
    list_display = ("name", "email", "activity", "registration_date", "attendance_confirmed", "confirmation_method")
    list_filter = ("attendance_confirmed", "activity")
    search_fields = ("name", "email")


@admin.register(EvaluationSubmission)
class EvaluationSubmissionAdmin(admin.ModelAdmin):
    list_display = ("registration", "template", "submitted_at")


@admin.register(FormAnswer)
class FormAnswerAdmin(admin.ModelAdmin):
    list_display = ("field", "answer_source", "registration", "evaluation_submission", "answer_value")
    list_filter = ("answer_source",)
