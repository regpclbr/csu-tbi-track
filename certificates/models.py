"""CERTIFICATE_TEMPLATE, CERTIFICATE_SIGNATORY, ACTIVITY_SIGNATORY and CERTIFICATE."""

import secrets
from pathlib import PurePosixPath

from django.conf import settings
from django.db import models
from django.db.models import Q


def _extension(filename: str) -> str:
    """The extension only (already checked by the service); never the uploaded name itself."""
    return PurePosixPath(filename or "").suffix.lower().lstrip(".")


def template_upload_to(instance: "CertificateTemplate", filename: str) -> str:
    return f"certificate_templates/template-{secrets.token_hex(8)}.{_extension(filename)}"


def signature_upload_to(instance: "CertificateSignatory", filename: str) -> str:
    """PNG or JPG (checked by the service, ASSUMPTIONS 361); a random name, never the uploaded one nor the position
    (free text since ASSUMPTIONS 363)."""
    extension = "jpg" if _extension(filename) in ("jpg", "jpeg") else "png"
    return f"signatures/signature-{secrets.token_hex(8)}.{extension}"


class Slot(models.TextChoices):
    """Where a signature is printed on the certificate (ASSUMPTIONS 363)."""

    LEFT = "left", "Left"
    RIGHT = "right", "Right"


class CertificateTemplate(models.Model):
    """The certificate design. Only one can be active. Deactivate instead of deleting."""

    certificate_template_id = models.BigAutoField(primary_key=True)
    name = models.CharField(max_length=255)
    file_path = models.FileField(upload_to=template_upload_to, max_length=255)
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
    """A person who signs certificates (ASSUMPTIONS 363): name and position as printed (free text, e.g. "University
    President"), the signature image (may be added later, ASSUMPTIONS 361) and is_active (inactive people are not
    offered in the dropdowns; activities that use them keep them).

    A row that a certificate uses never changes: replacing the image or editing the name or position creates a NEW
    row (a new version of the person) and moves every activity to it, so Issued certificates keep exactly what they
    were printed with ("Issued with an old signature" is computed from that). default_slot: the person a new
    activity starts with on that side (at most one per slot)."""

    signatory_id = models.BigAutoField(primary_key=True)
    name = models.CharField(max_length=255)
    position = models.CharField(max_length=255)
    signature_image_path = models.ImageField(upload_to=signature_upload_to, max_length=255, null=True, blank=True)
    default_slot = models.CharField(max_length=10, choices=Slot.choices, null=True, blank=True)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    # The version of the same person this row replaced (a new image or a name/position edit, ASSUMPTIONS 365). Follow
    # it back to find every older version; follow next_versions forward to find the current one.
    previous_version = models.ForeignKey(
        "self", on_delete=models.PROTECT, null=True, blank=True, related_name="next_versions"
    )

    class Meta:
        db_table = "certificate_signatory"
        verbose_name_plural = "certificate signatories"
        constraints = [
            models.UniqueConstraint(
                fields=["default_slot"], condition=Q(default_slot__isnull=False), name="one_default_signatory_per_slot"
            ),
            models.CheckConstraint(
                condition=Q(default_slot__isnull=True) | Q(default_slot__in=["left", "right"]),
                name="signatory_default_slot_valid",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.name} ({self.position})"


class ActivitySignatory(models.Model):
    """Who signs the certificates of one activity, per slot (ASSUMPTIONS 363). Changed only through
    certificates.services.set_activity_signatory (History row, certificates refreshed)."""

    activity_signatory_id = models.BigAutoField(primary_key=True)
    activity = models.ForeignKey("activities.Activity", on_delete=models.CASCADE, related_name="signatory_slots")
    signatory = models.ForeignKey(CertificateSignatory, on_delete=models.PROTECT, related_name="activity_slots")
    slot = models.CharField(max_length=10, choices=Slot.choices)
    # Printed instead of the person's position on this activity's certificates (ASSUMPTIONS 365); empty = the position.
    position_on_certificate = models.CharField(max_length=255, null=True, blank=True)

    class Meta:
        db_table = "activity_signatory"
        verbose_name_plural = "activity signatories"
        constraints = [
            models.UniqueConstraint(fields=["activity", "slot"], name="one_signatory_per_activity_slot"),
            models.UniqueConstraint(fields=["activity", "signatory"], name="signatory_once_per_activity"),
            models.CheckConstraint(condition=Q(slot__in=["left", "right"]), name="activity_signatory_slot_valid"),
        ]

    def __str__(self) -> str:
        return f"{self.activity_id} {self.slot}: {self.signatory_id}"


class CertificateText(models.Model):
    """The wording printed on a certificate (ASSUMPTIONS 370): the title, the line before the name, the main sentence
    and the closing line, with placeholders ({name}, {activity}, ... : certificates.wording). One per certificate type.
    activity empty = the default wording; set = that activity's custom text.

    Versioned like the signatories: a row a certificate uses never changes. Saving new text creates a NEW row (the
    old one gets is_current = False) and the certificates that are not Issued move to it; Issued certificates keep
    the row they were printed with. Changed only through certificates.services (save_default_text,
    save_activity_text, use_default_text)."""

    class CertificateType(models.TextChoices):
        PARTICIPATION = "participation", "Certificate of Participation"
        APPRECIATION = "appreciation", "Certificate of Appreciation"

    certificate_text_id = models.BigAutoField(primary_key=True)
    certificate_type = models.CharField(max_length=20, choices=CertificateType.choices)
    activity = models.ForeignKey(
        "activities.Activity", on_delete=models.PROTECT, null=True, blank=True, related_name="certificate_texts"
    )
    title = models.CharField(max_length=60)
    line_before_name = models.CharField(max_length=100, blank=True)
    main_sentence = models.CharField(max_length=400)
    closing_line = models.CharField(max_length=200, blank=True)
    is_current = models.BooleanField(default=True)
    # Empty = the System (the seeded default wording).
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, null=True, blank=True, related_name="certificate_texts"
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "certificate_text"
        constraints = [
            # One current default per type (activity empty: a separate partial index, NULLs are never equal).
            models.UniqueConstraint(
                fields=["certificate_type"], condition=Q(is_current=True, activity__isnull=True),
                name="one_current_default_certificate_text",
            ),
            # One current custom text per activity and type.
            models.UniqueConstraint(
                fields=["activity", "certificate_type"], condition=Q(is_current=True, activity__isnull=False),
                name="one_current_activity_certificate_text",
            ),
            models.CheckConstraint(
                condition=Q(certificate_type__in=["participation", "appreciation"]), name="certificate_text_type_valid"
            ),
        ]

    def __str__(self) -> str:
        whose = f"activity {self.activity_id}" if self.activity_id else "default"
        return f"{self.get_certificate_type_display()} ({whose}, #{self.pk})"


class Certificate(models.Model):
    """A Certificate of Participation (registration, by email) or of Appreciation
    (resource person, hard copy)."""

    class RecipientType(models.TextChoices):
        REGISTRATION = "registration", "Participant (registration)"
        RESOURCE_PERSON = "resource_person", "Resource person"

    class Status(models.TextChoices):
        """Rules for moving between statuses live in certificates/services.py (ASSUMPTIONS 357).
        Generated is shown as "Waiting for approval": a member with can_approve_certificates approves it."""

        GENERATED = "generated", "Waiting for approval"
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
    # The member with can_approve_certificates who approved it; empty while waiting for approval.
    approved_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="approved_certificates",
    )
    # The two people printed (ASSUMPTIONS 363): the activity's slots when the certificate was created or last
    # refreshed. An Issued certificate keeps the rows it was printed with.
    left_signatory = models.ForeignKey(
        CertificateSignatory, on_delete=models.PROTECT, related_name="left_signed_certificates"
    )
    right_signatory = models.ForeignKey(
        CertificateSignatory, on_delete=models.PROTECT, related_name="right_signed_certificates"
    )
    # The titles printed under the two signatures (ASSUMPTIONS 365): the activity's "Title on this certificate", else
    # the person's position. Kept with the certificate, so an Issued one keeps the title it was printed with.
    left_signatory_title = models.CharField(max_length=255)
    right_signatory_title = models.CharField(max_length=255)
    # The wording it is printed with (ASSUMPTIONS 370): the activity's custom text or the default, for its type. An
    # Issued certificate keeps the row it was printed with.
    certificate_text = models.ForeignKey(CertificateText, on_delete=models.PROTECT, related_name="certificates")
    status =models.CharField(max_length=20, choices=Status.choices, default=Status.GENERATED)
    # Null until the background job renders the final PDF (certificates/<year>/<number>.pdf).
    file_path = models.FileField(upload_to="certificates/", max_length=255, null=True, blank=True)
    approved_at = models.DateTimeField(null=True, blank=True)
    delivery_method = models.CharField(max_length=20, choices=DeliveryMethod.choices)
    issued_at = models.DateTimeField(null=True, blank=True)
    sent_at = models.DateTimeField(null=True, blank=True)
    # The last email attempt failed (cleared when it is sent): the tab shows "Send failed" + Resend.
    send_failed_at = models.DateTimeField(null=True, blank=True)
    # The last time a member reissued it with the current signatures (ASSUMPTIONS 364). Participants: the email says
    # "Updated copy". Resource persons: reissued_at > issued_at = the updated copy is not handed over yet.
    reissued_at = models.DateTimeField(null=True, blank=True)
    # The last PDF render failed (ASSUMPTIONS 368): when, and why in plain words (services.render_error_text; never the
    # raw error, which can contain file paths). Cleared when the PDF is written, on Retry and when refreshed.
    render_failed_at = models.DateTimeField(null=True, blank=True)
    render_error = models.CharField(max_length=255, null=True, blank=True)

    class Meta:
        db_table = "certificate"
        constraints = [
            # One Certificate of Participation per registration (NULLs are not compared).
            models.UniqueConstraint(fields=["registration"], name="one_certificate_per_registration"),
            # One Certificate of Appreciation per resource person per activity (Step 9).
            models.UniqueConstraint(
                fields=["activity", "resource_person"],
                condition=Q(resource_person__isnull=False),
                name="one_certificate_per_resource_person_per_activity",
            ),
            # The recipient column that is filled must match recipient_type.
            models.CheckConstraint(
                condition=(
                    Q(recipient_type="registration", registration__isnull=False, resource_person__isnull=True)
                    | Q(recipient_type="resource_person", registration__isnull=True, resource_person__isnull=False)
                ),
                name="certificate_recipient_matches_type",
            ),
        ]

    def save(self, *args, **kwargs) -> None:
        """A safety net for rows created outside the services (tests, the demo): an empty printed title becomes the
        signatory's position. certificates.services always fills both from the activity's slots."""
        for side in ("left", "right"):
            if not getattr(self, f"{side}_signatory_title") and getattr(self, f"{side}_signatory_id"):
                setattr(self, f"{side}_signatory_title", getattr(self, f"{side}_signatory").position)
                update_fields = kwargs.get("update_fields")
                if update_fields is not None:
                    kwargs["update_fields"] = {*update_fields, f"{side}_signatory_title"}
        if not self.certificate_text_id:  # the same safety net: the current wording of the activity (or the default)
            from .wording import current_text_for

            self.certificate_text = current_text_for(self.activity_id, self.text_type)
            update_fields = kwargs.get("update_fields")
            if update_fields is not None:
                kwargs["update_fields"] = {*update_fields, "certificate_text"}
        super().save(*args, **kwargs)

    @property
    def text_type(self) -> str:
        """The CERTIFICATE_TEXT type that matches the recipient: participation (registration) or appreciation."""
        if self.recipient_type == self.RecipientType.RESOURCE_PERSON:
            return CertificateText.CertificateType.APPRECIATION
        return CertificateText.CertificateType.PARTICIPATION

    def __str__(self) -> str:
        return self.certificate_number


class CertificateNumberSequence(models.Model):
    """The last certificate number given in each Manila year (ASSUMPTIONS 389). A number is never given twice, even
    after its certificate was deleted (an activity deleted, attendance undone): certificates.services._certificate_numbers
    continues from max(last_number, the highest existing number)."""

    year = models.PositiveIntegerField(primary_key=True)
    last_number = models.PositiveIntegerField(default=0)

    class Meta:
        db_table = "certificate_number_sequence"

    def __str__(self) -> str:
        return f"{self.year}: {self.last_number}"
