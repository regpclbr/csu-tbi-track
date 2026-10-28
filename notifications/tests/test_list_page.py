"""Phase F5: the notifications page (/notifications/): my notifications only, All / Unread, mark one or all as read,
links to the page each is about, and the dropdown's "View all" link."""

import pytest
from django.test import Client
from django.urls import reverse

from activities.tests.conftest import director, new_activity, valid_data  # noqa: F401  (re-exported fixtures)
from documents.models import DocumentType
from notifications.models import Notification
from notifications.services import DEFAULT_PAGE_SIZE

pytestmark = pytest.mark.django_db


@pytest.fixture
def login():
    def log_in(member) -> Client:
        client = Client()
        client.force_login(member)
        return client

    return log_in


def notify(member, message="Hello", activity=None, document_type=None, is_read=False) -> Notification:
    return Notification.objects.create(member=member, activity=activity, document_type=document_type,
                                       type=Notification.DOCUMENT_OVERDUE, message=message, is_read=is_read)


def test_list_shows_only_my_notifications_with_their_links(director, make_member, new_activity, login) -> None:
    other = make_member("Admin Staff")
    notify(director, "Invitation letter overdue", new_activity, DocumentType.objects.get(name="Invitation Letter"))
    notify(director, "Activity news", new_activity, is_read=True)
    notify(director, "General note")
    notify(other, "Someone else's note")
    html = login(director).get(reverse("notifications:list")).content.decode()
    assert "Invitation letter overdue" in html and "Activity news" in html and "General note" in html
    assert "Someone else" not in html
    assert "Open documents" in html and "Open activity" in html
    assert html.count("Unread: ") == 2 and "Mark all as read" in html
    assert html.index("General note") < html.index("Activity news") < html.index("Invitation letter overdue")  # newest first


def test_unread_filter(director, login) -> None:
    notify(director, "Old news", is_read=True)
    notify(director, "Fresh news")
    html = login(director).get(reverse("notifications:list") + "?show=unread").content.decode()
    assert "Fresh news" in html and "Old news" not in html and 'aria-current="page"' in html


def test_empty_states(director, login) -> None:
    client = login(director)
    assert "No notifications yet" in client.get(reverse("notifications:list")).content.decode()
    notify(director, "Read", is_read=True)
    html = client.get(reverse("notifications:list") + "?show=unread").content.decode()
    assert "You're all caught up" in html and "Mark all as read" not in html


def test_mark_one_as_read_goes_back_to_the_list(director, login) -> None:
    notification = notify(director)
    response = login(director).post(reverse("notifications:read", args=[notification.pk]), {"next": "/notifications/?show=unread"})
    assert response.status_code == 302 and response["Location"] == "/notifications/?show=unread"
    notification.refresh_from_db()
    assert notification.is_read


def test_next_must_be_a_page_of_this_site(director, login) -> None:
    notification = notify(director)
    response = login(director).post(reverse("notifications:read", args=[notification.pk]), {"next": "https://evil.example/"})
    assert response["Location"] == reverse("notifications:list")


def test_someone_elses_notification_is_404(director, make_member, login) -> None:
    theirs = notify(make_member("Admin Staff"))
    assert login(director).post(reverse("notifications:read", args=[theirs.pk])).status_code == 404
    theirs.refresh_from_db()
    assert not theirs.is_read


def test_mark_all_as_read_only_changes_mine(director, make_member, login) -> None:
    other = make_member("Admin Staff")
    notify(director), notify(director), notify(other)
    response = login(director).post(reverse("notifications:read_all"), follow=True)
    assert [str(m) for m in response.context["messages"]] == ["2 notifications marked as read."]
    assert Notification.objects.filter(is_read=False).get().member == other


def test_list_is_paginated(director, login) -> None:
    for number in range(DEFAULT_PAGE_SIZE + 1):
        notify(director, f"Note {number}")
    html = login(director).get(reverse("notifications:list") + "?page=2").content.decode()
    assert "Page 2 of 2" in html and "Note 0" in html


def test_needs_a_staff_login(client) -> None:
    assert client.get(reverse("notifications:list")).status_code == 302
    assert client.post(reverse("notifications:read_all")).status_code == 302


def test_the_dropdown_links_to_the_full_list(director, login) -> None:
    html = login(director).get(reverse("notifications:dropdown")).content.decode()
    assert f'href="{reverse("notifications:list")}"' in html and "View all notifications" in html
