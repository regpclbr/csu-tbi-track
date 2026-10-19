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
    participant_category = models.ForeignKey(
        ParticipantCategory, on_delete=models.PROTECT, related_name="activities"
    )
    title = models.CharField(max_length=255)
    description = models.TextField(null=True, blank=True)
    start_date = models.DateField()
    end_date = models.DateField()
    time_schedule = models.CharField(max_length=100, help_text="For example: 8:00 AM - 5:00 PM")
    venue = models.CharField(max_length=255)
    max_slots = models.PositiveIntegerField()
    registration_deadline = models.DateTimeField()
    evaluation_deadline = models.DateTimeField()
    budget_amount = models.DecimalField(max_digits=12, decimal_places=2)
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
        ]

    def __str__(self) -> str:
        return self.title


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
    changed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="activity_changes"
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
