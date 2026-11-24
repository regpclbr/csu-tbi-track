"""The Director is told when someone else creates an activity (ASSUMPTIONS 361): an in-app notification for every
active Director, linked to the activity, in the same transaction; none when a Director creates it; no email."""

import pytest
from django.core import mail
from django.core.exceptions import ValidationError
from django.test import Client
from django.urls import reverse

from activities import services
from activities.formatting import format_date
from activities.models import Activity
from notifications.models import Notification
from notifications.pages import target_url

pytestmark = pytest.mark.django_db


@pytest.fixture
def project_head(make_member):
    return make_member("Incubation Project Head")


def test_every_active_director_is_told(director, make_member, project_head, valid_data) -> None:
    second = make_member("Director", email="second.director@example.com")
    make_member("Director", email="gone.director@example.com", is_active=False)
    make_member("Community Development Head")  # not a Director: nothing

    activity = services.create_activity(project_head, valid_data(title="Investor Day"))

    notes = list(Notification.objects.order_by("member_id"))
    assert [n.member_id for n in notes] == [director.pk, second.pk]
    expected = f"Test Incubation Project Head created a new activity: Investor Day ({format_date(activity.start_date)})"
    for note in notes:
        assert (note.message, note.type, note.activity_id, note.is_read) == (expected, Notification.ACTIVITY_CREATED, activity.pk, False)
        assert target_url(note) == reverse("activities:detail", args=[activity.pk])
    assert mail.outbox == []


def test_no_notification_when_a_director_creates_it(director, make_member, valid_data) -> None:
    make_member("Director", email="second.director@example.com")
    services.create_activity(director, valid_data())
    assert not Notification.objects.filter(type=Notification.ACTIVITY_CREATED).exists()


def test_a_refused_activity_notifies_nobody(director, project_head, valid_data) -> None:
    with pytest.raises(ValidationError):
        services.create_activity(project_head, valid_data(title=""))
    assert not Activity.objects.exists() and not Notification.objects.exists()


def test_the_new_activity_page_notifies_too(director, project_head, valid_data) -> None:
    """Created through the page (create_activity_with_documents -> create_activity): one "activity created"
    notification; the documents are approved copies, so no "waiting for your review" (ASSUMPTIONS 432); no email."""
    from django.core.files.uploadedfile import SimpleUploadedFile

    from conftest import CREATION_PDF
    from documents.services import NewDocument, create_activity_with_documents, creation_required_types

    rows = [NewDocument(t, SimpleUploadedFile("a.pdf", CREATION_PDF), t.name) for t in creation_required_types()]
    activity = create_activity_with_documents(project_head, valid_data(), rows)
    assert Notification.objects.get(type=Notification.ACTIVITY_CREATED).activity_id == activity.pk
    waiting = Notification.objects.filter(type=Notification.DOCUMENT_WAITING_FOR_REVIEW, member=director)
    assert waiting.count() == 0 and mail.outbox == []


def test_the_director_sees_it_in_the_notifications_list(director, project_head, valid_data) -> None:
    services.create_activity(project_head, valid_data(title="Investor Day"))
    client = Client()
    client.force_login(director)
    html = client.get(reverse("notifications:list")).content.decode()
    assert "created a new activity: Investor Day" in html and "Open activity" in html
