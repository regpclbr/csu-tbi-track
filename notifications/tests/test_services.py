"""My notifications: newest first, unread count, mark read. A member only sees and changes their own."""

from datetime import timedelta

import pytest
from django.core.exceptions import PermissionDenied
from django.http import Http404

from notifications import services
from notifications.models import Notification

pytestmark = pytest.mark.django_db


@pytest.fixture
def me(make_member):
    return make_member("Director")


@pytest.fixture
def other(make_member):
    return make_member("Admin Staff")


def notify(member, message: str, minutes_ago: int = 0) -> Notification:
    notification = Notification.objects.create(member=member, message=message, type="x")
    Notification.objects.filter(pk=notification.pk).update(created_at=notification.created_at - timedelta(minutes=minutes_ago))
    return notification


def test_list_is_newest_first_and_only_mine(me, other) -> None:
    notify(me, "older", minutes_ago=10)
    notify(me, "newer")
    notify(other, "not mine")
    assert [n.message for n in services.list_notifications(me).items] == ["newer", "older"]


def test_unread_only_and_count(me, other) -> None:
    first = notify(me, "a")
    notify(me, "b")
    notify(other, "c")
    services.mark_read(me, first.pk)
    assert services.unread_count(me) == 1
    assert [n.message for n in services.list_notifications(me, unread_only=True).items] == ["b"]


def test_cannot_mark_someone_elses(me, other) -> None:
    theirs = notify(other, "theirs")
    with pytest.raises(Http404):
        services.mark_read(me, theirs.pk)
    theirs.refresh_from_db()
    assert theirs.is_read is False


def test_mark_all_read_only_touches_mine(me, other) -> None:
    notify(me, "a")
    notify(me, "b")
    theirs = notify(other, "c")
    assert services.mark_all_read(me) == 2
    assert services.unread_count(me) == 0
    theirs.refresh_from_db()
    assert theirs.is_read is False


def test_deactivated_member_is_refused(me) -> None:
    me.is_active = False
    with pytest.raises(PermissionDenied):
        services.unread_count(me)
