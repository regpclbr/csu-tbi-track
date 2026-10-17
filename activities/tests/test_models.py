"""Database constraints on activities."""

import pytest
from django.db import IntegrityError

from activities.models import Activity, ActivityResourcePerson, ResourcePerson


@pytest.mark.django_db
def test_resource_person_once_per_activity(activity: Activity) -> None:
    person = ResourcePerson.objects.create(name="Dr. Reyes")
    ActivityResourcePerson.objects.create(activity=activity, resource_person=person, topic="Pitching")
    with pytest.raises(IntegrityError):
        ActivityResourcePerson.objects.create(activity=activity, resource_person=person, topic="Again")


@pytest.mark.django_db
def test_lookup_rows_are_protected(activity: Activity) -> None:
    from django.db.models import ProtectedError

    with pytest.raises(ProtectedError):
        activity.status.delete()
