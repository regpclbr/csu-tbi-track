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
    # ASSUMPTIONS 409: when staff marked this REGISTRATION form ready ("Open registration"). On activities created with
    # their documents (Activity.creation_documents_required) registration stays closed until it is set, so it never opens
    # with only the default placeholder fields. Empty for evaluation forms and for older activities' forms (not needed).
    # Set only by registrations.services.mark_registration_form_ready.
    ready_at = models.DateTimeField(null=True, blank=True)
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
    """A person registered for an activity (online, through the startup portal, or as a walk-in at the QR check-in,
    ASSUMPTIONS 434). No approval step."""

    class Source(models.TextChoices):
        """How the person registered (ASSUMPTIONS 434). Walk-ins never signified beforehand, so they are not on the
        Confirmation of Attendance."""

        ONLINE = "online", "Online"
        PORTAL = "portal", "Startup portal"
        WALK_IN = "walk_in", "Walk-in"

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
    # ASSUMPTIONS 426: fixed fields of every registration, required for NEW registrations by services.register().
    # Empty on older rows (designation) or copied from their "Affiliation / Organization" answer (office_agency).
    designation = models.CharField(max_length=255, blank=True, default="")
    office_agency = models.CharField(max_length=255, blank=True, default="")
    registration_date = models.DateTimeField(auto_now_add=True)
    registration_source = models.CharField(max_length=20, choices=Source.choices, default=Source.ONLINE)
    # "Will not attend" (ASSUMPTIONS 434): set by services.cancel_registration ("I can no longer attend"), cleared by
    # services.restore_registration. The row is kept; a cancelled registration does not take a slot.
    cancelled_at = models.DateTimeField(null=True, blank=True)
    # Attended AT LEAST ONE DAY (ASSUMPTIONS 434): true exactly when the registration has an ATTENDANCE_DAY row. Written
    # ONLY by registrations.attendance (_sync_attended), in the same transaction as the day rows; every other module
    # reads this flag (reports, certificates, evaluations, Needs attention). A parity test keeps the two in step.
    attendance_confirmed = models.BooleanField(default=False)

    class LinkRequestResult(models.TextChoices):
        """What happened to the latest request on the general evaluation page (/evaluation/<id>/) for this
        registration's email (services.request_evaluation_link)."""

        QUEUED = "queued", "Queued"
        NOT_ATTENDED = "not_attended", "Not marked as attended"
        ALREADY_EVALUATED = "already_evaluated", "Already evaluated"
        WINDOW_CLOSED = "window_closed", "Evaluation not open"

    class LinkDelivery(models.TextChoices):
        """How the last evaluation link email went out (services.deliver_evaluation_link): really sent, or only
        printed in the terminal (development mode: the console mailer; or a demo address, config.mail)."""

        SENT = "sent", "Sent"
        PRINTED_DEV = "printed_dev", "Printed in the terminal (development mode)"
        PRINTED_DEMO = "printed_demo", "Printed in the terminal (demo address)"

    # The last evaluation link email: sent, or failed (setting one clears the other). Both empty = never sent.
    evaluation_link_sent_at = models.DateTimeField(null=True, blank=True)
    evaluation_link_failed_at = models.DateTimeField(null=True, blank=True)
    # With evaluation_link_sent_at: really sent or only printed. Empty when nothing was sent (or before 2026-11-08).
    evaluation_link_delivery = models.CharField(max_length=20, choices=LinkDelivery.choices, null=True, blank=True)
    # The latest request on the general evaluation page for this email (ASSUMPTIONS 354). An email that matches no
    # registration is never stored anywhere.
    evaluation_link_requested_at = models.DateTimeField(null=True, blank=True)
    evaluation_link_request_result = models.CharField(
        max_length=20, choices=LinkRequestResult.choices, null=True, blank=True
    )
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

    @property
    def will_attend(self) -> bool:
        """Pre-registration answer for the Confirmation of Attendance: not cancelled."""
        return self.cancelled_at is None


def signature_upload_to(instance: "ParticipantSignature", filename: str) -> str:
    """signatures/activity-<id>/<random>.png: never the uploaded name (registrations.signatures picks the name)."""
    return filename


class ParticipantSignature(models.Model):
    """The participant's signature for one registration (ASSUMPTIONS 434): given once (pre-registration, QR check-in,
    walk-in, or on a staff device), reused on every generated Attendance and Confirmation of Attendance form.

    kind draw / upload: a PNG re-encoded by Pillow (registrations.signatures), file_path set. kind typed: the typed full
    name (printed as the signature), file_path empty. Deleted by the retention job (registrations.signatures
    .purge_signatures): the file is removed, typed_name cleared and deleted_at set; the row stays as a record."""

    class Kind(models.TextChoices):
        DRAW = "draw", "Drawn on the screen"
        UPLOAD = "upload", "Uploaded image"
        TYPED = "typed", "Typed name"

    signature_id = models.BigAutoField(primary_key=True)
    registration = models.OneToOneField(Registration, on_delete=models.CASCADE, related_name="signature")
    kind = models.CharField(max_length=10, choices=Kind.choices)
    file_path = models.FileField(upload_to=signature_upload_to, max_length=255, blank=True, default="")
    typed_name = models.CharField(max_length=150, null=True, blank=True)
    signed_at = models.DateTimeField()
    deleted_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        db_table = "participant_signature"
        constraints = [
            models.CheckConstraint(
                condition=Q(deleted_at__isnull=False) | Q(kind="typed", typed_name__isnull=False) | (
                    Q(kind__in=["draw", "upload"]) & ~Q(file_path="")),
                name="participant_signature_has_content",
            ),
        ]

    def __str__(self) -> str:
        return f"Signature of {self.registration}"

    @property
    def is_deleted(self) -> bool:
        return self.deleted_at is not None


class AttendanceDay(models.Model):
    """One registration's attendance on one day of the activity (ASSUMPTIONS 434). Rows are created and removed ONLY by
    registrations.attendance (QR check-in, walk-ins, staff marking); REGISTRATION.attendance_confirmed follows them."""

    class Method(models.TextChoices):
        QR = "qr", "QR check-in"
        STAFF = "staff", "Marked by staff"
        # Only on rows copied by migration registrations/0012 from the old REGISTRATION.confirmation_method.
        EMAIL = "email", "Email (before November 2026)"
        WET_SIGNATURE = "wet_signature", "Wet signature (before November 2026)"

    attendance_day_id = models.BigAutoField(primary_key=True)
    registration = models.ForeignKey(Registration, on_delete=models.CASCADE, related_name="attendance_days")
    day = models.DateField()
    checked_in_at = models.DateTimeField()
    method = models.CharField(max_length=20, choices=Method.choices)
    marked_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, null=True, blank=True, related_name="attendance_days_marked",
        help_text="The staff member who marked it (method staff); empty for a QR check-in.",
    )
    created_at = models.DateTimeField()

    class Meta:
        db_table = "attendance_day"
        ordering = ["day", "checked_in_at"]
        constraints = [
            models.UniqueConstraint(fields=["registration", "day"], name="one_attendance_per_registration_day"),
        ]

    def __str__(self) -> str:
        return f"{self.registration} on {self.day}"


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
