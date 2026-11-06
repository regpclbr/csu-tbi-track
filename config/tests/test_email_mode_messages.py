"""Success messages about emails (ASSUMPTIONS 342): while the mailer only prints emails (console, development),
they say so instead of "… was emailed". The mode check is config.mail.prints_emails(), the same as System status."""

from datetime import datetime, time, timedelta
from decimal import Decimal

import pytest
from django.core.management import call_command
from django.test import Client
from django.urls import reverse
from django.utils import timezone

from accounts import system_status
from accounts.models import TBIMember
from activities import services as activity_services
from activities.models import ActivityType, ParticipantCategory
from config.mail import CONSOLE_MODE_NOTICE, email_sent_message, prints_emails
from registrations import services as registration_services
from startups.models import Startup

pytestmark = pytest.mark.django_db

CONSOLE, REAL = "console", "real"


@pytest.fixture(params=[CONSOLE, REAL])
def mode(request, settings) -> str:
    """Console: the development mailer that prints emails. Real: any other mailer (tests use in-memory mail)."""
    if request.param == CONSOLE:
        settings.DEBUG = True
        settings.MAILERS = {**settings.MAILERS, "default": settings.CONSOLE_MAILER}
    return request.param


def expected(mode: str, sent: str, before: str = "") -> str:
    return before + (CONSOLE_MODE_NOTICE if mode == CONSOLE else sent)


def logged_in(member) -> Client:
    client = Client()
    client.force_login(member)
    return client


def messages_of(response) -> list[str]:
    return [str(message) for message in response.context["messages"]]


def test_the_check_and_the_wording(mode) -> None:
    assert prints_emails() is (mode == CONSOLE)
    assert email_sent_message("Sent.", before="Ana was added. ") == expected(mode, "Sent.", "Ana was added. ")
    assert CONSOLE_MODE_NOTICE == (
        "Email printed in the terminal (development mode). Set EMAIL_REAL_SENDING=True to send real emails."
    )


def test_system_status_uses_the_same_check(mode) -> None:
    item = system_status.email_status()
    assert (item.summary == "Development mode: emails are printed in the terminal.") is (mode == CONSOLE)


def test_member_invitation(mode, admin_member, roles) -> None:
    response = logged_in(admin_member).post(
        reverse("accounts:console_invite"), {"name": "Ana Cruz", "email": "ana@example.com", "role": roles["Director"].pk}, follow=True
    )
    assert TBIMember.objects.filter(email="ana@example.com").exists()
    assert messages_of(response) == [expected(
        mode, "The invitation with a temporary password was emailed to ana@example.com.", before="Ana Cruz was added. "
    )]


def test_password_reset_by_the_admin(mode, admin_member, make_member) -> None:
    other = make_member("Director")
    client = logged_in(admin_member)
    response = client.post(reverse("accounts:console_member_reset", args=[other.pk]), follow=True)
    assert messages_of(response) == [expected(mode, f"A new temporary password was emailed to {other.email}.")]
    api = client.post(reverse("accounts:api_member_reset_password", args=[other.pk]))
    assert api.json()["message"] == expected(mode, f"A new temporary password was emailed to {other.email}.")


def test_startup_set_password_link(mode, admin_member) -> None:
    startup = Startup.objects.create(name="Kalinaw Agritech", email="kalinaw@example.com", contact_number="09171234567")
    response = logged_in(admin_member).post(reverse("startups:send_password_link", args=[startup.pk]), follow=True)
    assert messages_of(response) == [expected(mode, f"A set-password link was emailed to {startup.email}.")]


def test_evaluation_links(mode, make_member, monkeypatch) -> None:
    director = make_member("Director")
    start = timezone.localdate() + timedelta(days=30)
    activity = activity_services.create_activity(director, {
        "title": "Pitching Workshop", "description": "Learn to pitch.", "activity_type": ActivityType.objects.get(name="Workshop"),
        "participant_categories": [ParticipantCategory.objects.get(name="Incubatees")], "start_date": start, "end_date": start,
        "time_schedule": "8:00 AM", "venue": "CSU Hall", "max_slots": 30,
        "registration_deadline": timezone.make_aware(datetime.combine(start - timedelta(days=2), time(17))),
        "evaluation_deadline": timezone.make_aware(datetime.combine(start + timedelta(days=7), time(17))),
        "budget_amount": Decimal("0"),
    })
    monkeypatch.setattr(registration_services, "send_evaluation_links", lambda member, activity: 2)
    response = logged_in(director).post(reverse("registrations:send_links", args=[activity.pk]), follow=True)
    assert messages_of(response) == [expected(
        mode, "They go out while qcluster is running.", before="Queued 2 evaluation link emails. "
    )]


def test_test_email_command(mode, capsys) -> None:
    call_command("send_test_email", "me@example.com")
    assert expected(mode, "Sent to me@example.com.") in capsys.readouterr().out
