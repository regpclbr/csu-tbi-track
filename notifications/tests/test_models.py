"""Database constraints on notifications."""

import pytest
from django.db import IntegrityError

from notifications.models import Notification


@pytest.mark.django_db
def test_notification_for_a_member(member, activity) -> None:
    Notification.objects.create(member=member, activity=activity, message="Upload the invitation letter", type="x")


@pytest.mark.django_db
def test_notification_needs_a_member(member) -> None:
    """Since the startup portal was removed (ASSUMPTIONS 458) the member is required (NOT NULL)."""
    with pytest.raises(IntegrityError):
        Notification.objects.create(member=None, message="Hello", type="x")
