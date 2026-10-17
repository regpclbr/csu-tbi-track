"""Database constraints on notifications."""

import pytest
from django.db import IntegrityError

from notifications.models import Notification
from startups.models import Startup


@pytest.mark.django_db
def test_notification_for_a_member(member, activity) -> None:
    Notification.objects.create(member=member, activity=activity, message="Upload the invitation letter", type="x")


@pytest.mark.django_db
@pytest.mark.parametrize("with_member, with_startup", [(False, False), (True, True)])
def test_notification_needs_exactly_one_recipient(member, with_member, with_startup) -> None:
    startup = Startup.objects.create(name="Acme", email="acme@example.com")
    with pytest.raises(IntegrityError):
        Notification.objects.create(
            member=member if with_member else None,
            startup=startup if with_startup else None,
            message="Hello",
            type="x",
        )
