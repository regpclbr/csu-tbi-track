"""Registration and evaluation forms, REGISTRATION, EVALUATION_SUBMISSION and FORM_ANSWER."""

from django.conf import settings
from django.db import models
from django.db.models import Q
from django.db.models.functions import Lower, Trim


class FormType(models.TextChoices):
    REGISTRATION = "registration", "Registration"
    EVALUATION = "evaluation", "Evaluation"


class FieldType(models.TextChoices):
    SHORT_TEXT = "short_text", "Short text"
    LONG_TEXT = "long_text", "Long text"
    RATING = "rating", "Rating"
    MULTIPLE_CHOICE = "multiple_choice", "Multiple choice"
    CHECKBOX = "checkbox", "Checkbox"


class DefaultFormField(models.Model):
    """A default question for an activity type. Copied into a new FORM_TEMPLATE."""

    default_field_id = models.BigAutoField(primary_key=True)
    activity_type = models.ForeignKey(
        "activities.ActivityType", on_delete=models.PROTECT, related_name="default_form_fields"
    )
    form_type = models.CharField(max_length=20, choices=FormType.choices)
    field_label = models.CharField(max_length=255)
    field_type = models.CharField(max_length=20, choices=FieldType.choices)
    options = models.TextField(
        blank=True, help_text='Choices for multiple choice/checkbox as a JSON list, e.g. ["Yes", "No"].'
    )
    is_required = models.BooleanField(default=True)
    sort_order = models.PositiveIntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "default_form_field"
        ordering = ["activity_type", "form_type", "sort_order"]

    def __str__(self) -> str:
        return self.field_label


class FormTemplate(models.Model):
    """The registration or evaluation form of one activity."""

    template_id = models.BigAutoField(primary_key=True)
    activity = models.ForeignKey("activities.Activity", on_delete=models.CASCADE, related_name="form_templates")
    form_type = models.CharField(max_length=20, choices=FormType.choices)
    title = models.CharField(max_length=255)
    generated_from_default = models.BooleanField(
        default=False, help_text="True if started from DEFAULT_FORM_FIELD."
    )
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="created_form_templates"
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "form_template"
        constraints = [
            # One registration form and one evaluation form per activity.
            models.UniqueConstraint(fields=["activity", "form_type"], name="one_form_per_type_per_activity"),
        ]

    def __str__(self) -> str:
        return self.title


class FormField(models.Model):
    """One question on a FORM_TEMPLATE."""

    field_id = models.BigAutoField(primary_key=True)
    template = models.ForeignKey(FormTemplate, on_delete=models.CASCADE, related_name="fields")
    field_label = models.CharField(max_length=255)
    field_type = models.CharField(max_length=20, choices=FieldType.choices)
    options = models.TextField(
        blank=True, help_text='Choices for multiple choice/checkbox as a JSON list, e.g. ["Yes", "No"].'
    )
    is_required = models.BooleanField(default=True)
    sort_order = models.PositiveIntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "form_field"
        ordering = ["template", "sort_order"]

    def __str__(self) -> str:
        return self.field_label


class Registration(models.Model):
    """A person registered for an activity. No approval step; no walk-ins."""

    class ConfirmationMethod(models.TextChoices):
        EMAIL = "email", "Email"
        WET_SIGNATURE = "wet_signature", "Wet signature"

    registration_id = models.BigAutoField(primary_key=True)
    activity = models.ForeignKey("activities.Activity", on_delete=models.PROTECT, related_name="registrations")
    template = models.ForeignKey(FormTemplate, on_delete=models.PROTECT, related_name="registrations")
    # The category the person attends as: one of the activity's categories (checked in
    # registrations.services.register). Required (NOT NULL), as in the ERD.
    participant_category = models.ForeignKey(
        "activities.ParticipantCategory", on_delete=models.PROTECT, related_name="registrations"
    )
    startup = models.ForeignKey(
        "startups.Startup",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="registrations",
        help_text="Set only if the registrant was logged in as a startup.",
    )
    name = models.CharField(max_length=255)
    email = models.EmailField()
    contact_number = models.CharField(max_length=30, blank=True)
    registration_date = models.DateTimeField(auto_now_add=True)
    attendance_confirmed = models.BooleanField(default=False)
    confirmation_method = models.CharField(
        max_length=20, choices=ConfirmationMethod.choices, null=True, blank=True
    )
    # Who marked the attendance and when (set by services.set_attendance; cleared together with the method on undo).
    attendance_marked_at = models.DateTimeField(null=True, blank=True)
    attendance_marked_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, null=True, blank=True, related_name="attendance_marked"
    )
    # The last evaluation link email: sent, or failed (setting one clears the other). Both empty = never sent.
    evaluation_link_sent_at = models.DateTimeField(null=True, blank=True)
    evaluation_link_failed_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "registration"
        ordering = ["-registration_date"]
        constraints = [
            # "Ana@x.com" and " ana@x.com " count as the same person.
            models.UniqueConstraint(
                Lower(Trim("email")), "activity", name="unique_registration_per_activity_email"
            ),
        ]

    def __str__(self) -> str:
        return f"{self.name} - {self.activity}"


class EvaluationSubmission(models.Model):
    """A registrant's evaluation of an activity. Never anonymous."""

    submission_id = models.BigAutoField(primary_key=True)
    template = models.ForeignKey(FormTemplate, on_delete=models.PROTECT, related_name="evaluation_submissions")
    # One evaluation per registration (OneToOneField adds the unique constraint).
    registration = models.OneToOneField(
        Registration, on_delete=models.CASCADE, related_name="evaluation_submission"
    )
    comments = models.TextField(blank=True)
    # Step 9 (ERD change): the participant types the name printed on their certificate and the
    # address it is emailed to. Checked in registrations.services.submit_evaluation.
    certificate_name = models.CharField(max_length=150)
    certificate_email = models.EmailField(max_length=254)
    submitted_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "evaluation_submission"

    def __str__(self) -> str:
        return f"Evaluation by {self.registration}"


class FormAnswer(models.Model):
    """An answer to one FORM_FIELD, from either a registration or an evaluation."""

    class AnswerSource(models.TextChoices):
        REGISTRATION = "registration", "Registration"
        EVALUATION = "evaluation", "Evaluation"

    answer_id = models.BigAutoField(primary_key=True)
    field = models.ForeignKey(FormField, on_delete=models.PROTECT, related_name="answers")
    answer_source = models.CharField(max_length=20, choices=AnswerSource.choices)
    registration = models.ForeignKey(
        Registration, on_delete=models.CASCADE, null=True, blank=True, related_name="answers"
    )
    evaluation_submission = models.ForeignKey(
        EvaluationSubmission, on_delete=models.CASCADE, null=True, blank=True, related_name="answers"
    )
    answer_value = models.TextField(blank=True)

    class Meta:
        db_table = "form_answer"
        constraints = [
            # Exactly one parent is set, and it matches answer_source.
            models.CheckConstraint(
                condition=(
                    Q(
                        answer_source="registration",
                        registration__isnull=False,
                        evaluation_submission__isnull=True,
                    )
                    | Q(
                        answer_source="evaluation",
                        registration__isnull=True,
                        evaluation_submission__isnull=False,
                    )
                ),
                name="form_answer_source_matches_parent",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.field}: {self.answer_value[:30]}"
