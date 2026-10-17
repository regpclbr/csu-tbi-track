"""Database constraints on activities."""

import pytest
from django.db import IntegrityError
from django.db.models import ProtectedError

from activities.models import Activity, ActivityAuditLog, ActivityResourcePerson, ResourcePerson


@pytest.mark.django_db
def test_resource_person_once_per_activity(activity: Activity) -> None:
    person = ResourcePerson.objects.create(name="Dr. Reyes")
    ActivityResourcePerson.objects.create(activity=activity, resource_person=person, topic="Pitching")
    with pytest.raises(IntegrityError):
        ActivityResourcePerson.objects.create(activity=activity, resource_person=person, topic="Again")


@pytest.mark.django_db
def test_lookup_rows_are_protected(activity: Activity) -> None:
    with pytest.raises(ProtectedError):
        activity.status.delete()


@pytest.mark.django_db
def test_activity_with_audit_log_cannot_be_deleted(activity: Activity, member) -> None:
    ActivityAuditLog.objects.create(
        activity=activity, changed_by=member, field_changed="venue", old_value="CSU Hall", new_value="Room 101"
    )
    with pytest.raises(ProtectedError):
        activity.delete()
