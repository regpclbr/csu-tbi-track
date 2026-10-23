"""Notifications JSON API (/notifications/api/)."""

import pytest

from notifications.models import Notification

pytestmark = pytest.mark.django_db


@pytest.fixture
def me(make_member):
    return make_member("Director")


@pytest.fixture
def other(make_member):
    return make_member("Admin Staff")


def test_requires_login(client) -> None:
    assert client.get("/notifications/api/").status_code == 401
    assert client.post("/notifications/api/read-all/").status_code == 401


def test_list_count_and_mark_read(client, me, other) -> None:
    mine = Notification.objects.create(member=me, message="Upload the invitation letter", type="document_due_soon")
    theirs = Notification.objects.create(member=other, message="Not mine", type="x")
    client.force_login(me)

    data = client.get("/notifications/api/").json()
    assert [n["notification_id"] for n in data["notifications"]] == [mine.pk]
    assert data["unread_count"] == 1
    assert data["notifications"][0]["created_at"].endswith("+08:00")  # Manila time
    assert client.get("/notifications/api/unread-count/").json() == {"unread_count": 1}

    assert client.post(f"/notifications/api/{theirs.pk}/read/").status_code == 404
    response = client.post(f"/notifications/api/{mine.pk}/read/")
    assert response.status_code == 200 and response.json()["notification"]["is_read"] is True
    assert client.get("/notifications/api/unread-count/").json() == {"unread_count": 0}


def test_mark_all_read(client, me, other) -> None:
    Notification.objects.create(member=me, message="a", type="x")
    Notification.objects.create(member=me, message="b", type="x")
    Notification.objects.create(member=other, message="c", type="x")
    client.force_login(me)
    assert client.post("/notifications/api/read-all/").json() == {"marked_read": 2}
    assert Notification.objects.filter(member=other, is_read=False).count() == 1


def test_get_is_not_allowed_for_changes(client, me) -> None:
    client.force_login(me)
    assert client.get("/notifications/api/read-all/").status_code == 405
