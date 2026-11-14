"""ACTIVITY, its lookup tables, ACTIVITY_AUDIT_LOG and resource persons."""

from django.conf import settings
from django.db import models
from django.db.models import F, Q
from django.db.models.functions import Lower, Trim


class ActivityStatus(models.Model):
    """Lookup: Draft, Scheduled, Ongoing, Conducted. Never deleted.

    The code relies on these exact names (see activities/services.py for allowed moves).
    """

    DRAFT = "Draft"
    SCHEDULED = "Scheduled"
    ONGOING = "Ongoing"
    CONDUCTED = "Conducted"

    activity_status_id = models.BigAutoField(primary_key=True)
    name = models.CharField(max_length=50, unique=True)
    description = models.CharField(max_length=255, blank=True)

    class Meta:
        db_table = "activity_status"
        verbose_name_plural = "activity statuses"

    def __str__(self) -> str:
        return self.name


class ActivityType(models.Model):
    """Lookup: Workshop, Training, Seminar, ... Deactivate instead of deleting."""

    activity_type_id = models.BigAutoField(primary_key=True)
    name = models.CharField(max_length=100, unique=True)
    description = models.CharField(max_length=255, blank=True)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "activity_type"
        ordering = ["name"]

    def __str__(self) -> str:
        return self.name


class ParticipantCategory(models.Model):
    """Who an activity is for. Seeded: Faculty, Incubatees, MSMEs; the Admin may add more.

    Rules (activities/services.py): seeded categories are never renamed or deleted; an
    Admin-added category can be renamed, and deleted only while no activity uses it.
    """

    participant_category_id = models.BigAutoField(primary_key=True)
    name = models.CharField(max_length=100, unique=True)
    description = models.CharField(max_length=255, blank=True)

    class Meta:
        db_table = "participant_category"
        ordering = ["name"]
        verbose_name_plural = "participant categories"
        constraints = [
            # "MSMEs", "msmes" and " MSMEs " count as the same name.
            models.UniqueConstraint(
                Lower(Trim("name")),
                name="unique_participant_category_name_ci",
                violation_error_message="A participant category with this name already exists.",
            ),
        ]

    def __str__(self) -> str:
        return self.name


class Activity(models.Model):
    """A TBI activity. Deadlines live here, not on registrations or evaluations."""

    activity_id = models.BigAutoField(primary_key=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="created_activities"
    )
    status = models.ForeignKey(ActivityStatus, on_delete=models.PROTECT, related_name="activities")
    activity_type = models.ForeignKey(ActivityType, on_delete=models.PROTECT, related_name="activities")
    # Who the activity is for: one or more categories, stored in ACTIVITY_PARTICIPANT_CATEGORY.
    # (A many-to-many through table adds no column to ACTIVITY.)
    participant_categories = models.ManyToManyField(
        ParticipantCategory, through="ActivityParticipantCategory", related_name="activities"
    )
    title = models.CharField(max_length=255)
    description = models.TextField(null=True, blank=True)
    start_date = models.DateField()
    end_date = models.DateField()
    # Manila time of day. Nullable only for activities whose old free-text time could not be read
    # (migration 0005); the form and services.validate_activity_values() require both.
    start_time = models.TimeField(null=True, blank=True)
    end_time = models.TimeField(null=True, blank=True)
    venue = models.CharField(max_length=255)
    max_slots = models.PositiveIntegerField()
    registration_deadline = models.DateTimeField()
    evaluation_deadline = models.DateTimeField()
    budget_amount = models.DecimalField(max_digits=12, decimal_places=2)
    # The approval of the activity's certificates (ASSUMPTIONS 369): who (can_approve_certificates) and when. Empty =
    # "Needs approval". Set and cleared ONLY by certificates.services (approve_activity_certificates,
    # cancel_certificate_approval); never by the activity form or update_activity.
    certificates_approved_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, null=True, blank=True,
        related_name="approved_activity_certificates",
    )
    certificates_approved_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "activity"
        ordering = ["-start_date"]
        verbose_name_plural = "activities"
        constraints = [
            models.CheckConstraint(
                condition=Q(end_date__gte=F("start_date")), name="activity_end_date_after_start_date"
            ),
            models.CheckConstraint(condition=Q(budget_amount__gte=0), name="activity_budget_not_negative"),
            # Both times are set, or neither is.
            models.CheckConstraint(
                condition=Q(start_time__isnull=True, end_time__isnull=True)
                | Q(start_time__isnull=False, end_time__isnull=False),
                name="activity_times_both_or_neither",
            ),
            # A one-day activity ends after it starts (several days: 8:00 AM to 5:00 PM each day, any order is fine).
            models.CheckConstraint(
                condition=Q(end_date__gt=F("start_date")) | Q(start_time__isnull=True) | Q(end_time__gt=F("start_time")),
                name="activity_one_day_end_time_after_start_time",
            ),
        ]

    def __str__(self) -> str:
        return self.title


class ActivityParticipantCategory(models.Model):
    """ACTIVITY_PARTICIPANT_CATEGORY: links an activity to each category it is for."""

    activity_participant_category_id = models.BigAutoField(primary_key=True)
    activity = models.ForeignKey(Activity, on_delete=models.CASCADE, related_name="category_links")
    participant_category = models.ForeignKey(
        ParticipantCategory, on_delete=models.PROTECT, related_name="activity_links"
    )

    class Meta:
        db_table = "activity_participant_category"
        constraints = [
            models.UniqueConstraint(
                fields=["activity", "participant_category"], name="unique_category_per_activity"
            ),
        ]

    def __str__(self) -> str:
        return f"{self.activity} - {self.participant_category}"


class AuditLogIsAppendOnly(Exception):
    """Raised on any attempt to change or delete an audit log row."""


class ActivityAuditLogQuerySet(models.QuerySet):
    """Bulk update/delete are blocked: the audit log is append-only."""

    def update(self, **kwargs):
        raise AuditLogIsAppendOnly("Audit log rows cannot be changed.")

    def delete(self):
        raise AuditLogIsAppendOnly("Audit log rows cannot be deleted.")


class ActivityAuditLog(models.Model):
    """One row per changed field on an ACTIVITY, with readable old and new values.

    Append-only: rows are only ever inserted (by activities/services.py). Saving an
    existing row or deleting rows raises AuditLogIsAppendOnly.
    """

    log_id = models.BigAutoField(primary_key=True)
    # PROTECT: activities are never deleted, and their history must survive.
    activity = models.ForeignKey(Activity, on_delete=models.PROTECT, related_name="audit_logs")
    # Empty = System: an automatic status change (activities.services.apply_automatic_statuses).
    changed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="activity_changes", null=True, blank=True
    )
    field_changed = models.CharField(max_length=100)
    old_value = models.TextField(blank=True)
    new_value = models.TextField(blank=True)
    changed_at = models.DateTimeField(auto_now_add=True)

    objects = ActivityAuditLogQuerySet.as_manager()

    class Meta:
        db_table = "activity_audit_log"
        ordering = ["-changed_at", "-log_id"]

    def __str__(self) -> str:
        return f"{self.activity} - {self.field_changed}"

    def save(self, *args, **kwargs) -> None:
        if not self._state.adding:
            raise AuditLogIsAppendOnly("Audit log rows cannot be changed.")
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise AuditLogIsAppendOnly("Audit log rows cannot be deleted.")


class ResourcePerson(models.Model):
    """A speaker or facilitator. Receives a Certificate of Appreciation in hard copy."""

    resource_person_id = models.BigAutoField(primary_key=True)
    name = models.CharField(max_length=255)
    email = models.EmailField(blank=True)
    contact_number = models.CharField(max_length=30, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "resource_person"
        ordering = ["name"]

    def __str__(self) -> str:
        return self.name


class ActivityResourcePerson(models.Model):
    """Links a resource person to an activity, with the topic they handle."""

    activity_resource_person_id = models.BigAutoField(primary_key=True)
    activity = models.ForeignKey(Activity, on_delete=models.CASCADE, related_name="resource_person_links")
    resource_person = models.ForeignKey(ResourcePerson, on_delete=models.PROTECT, related_name="activity_links")
    topic = models.CharField(max_length=255, blank=True)

    class Meta:
        db_table = "activity_resource_person"
        constraints = [
            models.UniqueConstraint(
                fields=["activity", "resource_person"], name="unique_resource_person_per_activity"
            ),
        ]

    def __str__(self) -> str:
        return f"{self.resource_person} @ {self.activity}"
