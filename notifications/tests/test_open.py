"""Phase F4: a notification in the bell's dropdown opens the page it is about (and is marked as read)."""

import pytest
from django.test import Client
from django.urls import reverse

from activities.tests.conftest import director, new_activity, valid_data  # noqa: F401  (re-exported fixtures)
from documents.models import DocumentType
from notifications.models import Notification

pytestmark = pytest.mark.django_db


@pytest.fixture
def member(director):
    return director


@pytest.fixture
def activity(new_activity):
    return new_activity


@pytest.fixture
def login():
    def log_in(member) -> Client:
        client = Client()
        client.force_login(member)
        return client

    return log_in


def notify(member, activity=None, document_type=None, kind=Notification.DOCUMENT_OVERDUE) -> Notification:
    return Notification.objects.create(member=member, activity=activity, document_type=document_type, type=kind, message="Hello")


def test_document_reminder_opens_the_documents_tab_with_the_type(member, activity, login) -> None:
    invitation = DocumentType.objects.get(name="Invitation Letter")
    notification = notify(member, activity, invitation)
    response = login(member).post(reverse("notifications:open", args=[notification.pk]))
    assert response["Location"] == f"{reverse('activities:detail', args=[activity.pk])}?tab=documents&type={invitation.pk}"
    notification.refresh_from_db()
    assert notification.is_read


def test_other_notifications_open_the_activity_or_the_dashboard(member, activity, login) -> None:
    client = login(member)
    assert client.post(reverse("notifications:open", args=[notify(member, activity).pk]))["Location"] == reverse("activities:detail", args=[activity.pk])
    assert client.post(reverse("notifications:open", args=[notify(member).pk]))["Location"] == reverse("home")


def test_someone_elses_notification_is_404(member, make_member, activity, login) -> None:
    other = make_member("Admin Staff")
    notification = notify(other, activity)
    assert login(member).post(reverse("notifications:open", args=[notification.pk])).status_code == 404
    assert login(member).get(reverse("notifications:open", args=[notification.pk])).status_code == 405
    notification.refresh_from_db()
    assert not notification.is_read


def test_dropdown_items_are_forms_that_open_them(member, activity, login) -> None:
    invitation = DocumentType.objects.get(name="Invitation Letter")
    notification = notify(member, activity, invitation)
    html = login(member).get(reverse("notifications:dropdown"), HTTP_HX_REQUEST="true").content.decode()
    assert f'action="{reverse("notifications:open", args=[notification.pk])}"' in html and "Open documents" in html
