"""CERTIFICATE_TEMPLATE, CERTIFICATE_SIGNATORY and CERTIFICATE."""

from django.conf import settings
from django.db import models
from django.db.models import Q


class CertificateTemplate(models.Model):
    """The certificate design. Only one can be active. Deactivate instead of deleting."""

    certificate_template_id = models.BigAutoField(primary_key=True)
    name = models.CharField(max_length=255)
    file_path = models.FileField(upload_to="certificate_templates/", max_length=255)
    uploaded_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="uploaded_certificate_templates"
    )
    is_active = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "certificate_template"
        constraints = [
            models.UniqueConstraint(
                fields=["is_active"], condition=Q(is_active=True), name="one_active_certificate_template"
            ),
        ]

    def __str__(self) -> str:
        return self.name


class CertificateSignatory(models.Model):
    """The VP or Director who signs certificates. One active per position."""

    class Position(models.TextChoices):
        VP = "vp", "Vice President"
        DIRECTOR = "director", "Director"

    signatory_id = models.BigAutoField(primary_key=True)
    name = models.CharField(max_length=255)
    position = models.CharField(max_length=20, choices=Position.choices)
    signature_image_path = models.ImageField(upload_to="signatures/", max_length=255)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "certificate_signatory"
        verbose_name_plural = "certificate signatories"
        constraints = [
            models.UniqueConstraint(
                fields=["position"], condition=Q(is_active=True), name="one_active_signatory_per_position"
            ),
        ]

    def __str__(self) -> str:
        return f"{self.name} ({self.get_position_display()})"


class Certificate(models.Model):
    """A Certificate of Participation (registration, by email) or of Appreciation
    (resource person, hard copy)."""

    class RecipientType(models.TextChoices):
        REGISTRATION = "registration", "Participant (registration)"
        RESOURCE_PERSON = "resource_person", "Resource person"

    class Status(models.TextChoices):
        """Rules for moving between statuses live in certificates/services.py."""

        GENERATED = "generated", "Generated"
        PENDING_SIGNOFF = "pending_signoff", "Pending Sign-off"
        APPROVED = "approved", "Approved"
        ISSUED = "issued", "Issued"

    class DeliveryMethod(models.TextChoices):
        EMAIL = "email", "Email (participants)"
        HARD_COPY = "hard_copy", "Hard copy (resource persons)"

    certificate_id = models.BigAutoField(primary_key=True)
    # A certificate row is only created when it is generated, so template and number are required.
    certificate_template = models.ForeignKey(
        CertificateTemplate, on_delete=models.PROTECT, related_name="certificates"
    )
    certificate_number = models.CharField(max_length=50, unique=True)
    recipient_type = models.CharField(max_length=20, choices=RecipientType.choices)
    registration = models.ForeignKey(
        "registrations.Registration",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="certificates",
    )
    resource_person = models.ForeignKey(
        "activities.ResourcePerson",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="certificates",
    )
    evaluation_submission = models.ForeignKey(
        "registrations.EvaluationSubmission",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="certificates",
    )
    activity = models.ForeignKey("activities.Activity", on_delete=models.PROTECT, related_name="certificates")
    reviewed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="reviewed_certificates",
    )
    vp_signatory = models.ForeignKey(
        CertificateSignatory, on_delete=models.PROTECT, related_name="vp_signed_certificates"
    )
    director_signatory = models.ForeignKey(
        CertificateSignatory, on_delete=models.PROTECT, related_name="director_signed_certificates"
    )
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.GENERATED)
    # Null until the background job renders the PDF.
    file_path = models.FileField(upload_to="certificates/", max_length=255, null=True, blank=True)
    reviewed_at = models.DateTimeField(null=True, blank=True)
    director_signed_at = models.DateTimeField(null=True, blank=True)
    vp_signed_at = models.DateTimeField(null=True, blank=True)
    delivery_method = models.CharField(max_length=20, choices=DeliveryMethod.choices)
    issued_at = models.DateTimeField(null=True, blank=True)
    sent_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        db_table = "certificate"
        constraints = [
            # One Certificate of Participation per registration (NULLs are not compared).
            models.UniqueConstraint(fields=["registration"], name="one_certificate_per_registration"),
            # The recipient column that is filled must match recipient_type.
            models.CheckConstraint(
                condition=(
                    Q(recipient_type="registration", registration__isnull=False, resource_person__isnull=True)
                    | Q(recipient_type="resource_person", registration__isnull=True, resource_person__isnull=False)
                ),
                name="certificate_recipient_matches_type",
            ),
        ]

    def __str__(self) -> str:
        return self.certificate_number
