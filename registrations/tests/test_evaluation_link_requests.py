"""Requests on the general evaluation link, as staff see them (ASSUMPTIONS 354-355): REGISTRATION keeps the latest
request (evaluation_link_requested_at, evaluation_link_request_result) and how the last link email went out
(evaluation_link_delivery); the Evaluation tab shows "requested … · Sent / Not sent: reason"; the public answer is
always the same; an email that matches nobody is stored nowhere (not even in plain text in the rate-limit cache)."""

from datetime import timedelta

import pytest
from django.core.cache import cache
from django.test import Client
from django.urls import reverse
from django.utils import timezone

from config import ratelimit
from registrations import pages, services
from registrations.models import Registration

from .test_evaluation_request import IP, ask, evaluation_emails, ongoing  # noqa: F401 (the `ongoing` fixture)

pytestmark = pytest.mark.django_db

Result, Delivery = Registration.LinkRequestResult, Registration.LinkDelivery


@pytest.fixture(autouse=True)
def only_demo_addresses_blocked(settings):
    """These tests tell "sent" (person0@example.com) from "printed_demo" (ana@demo.example.com); since ASSUMPTIONS 464 all
    of example.com is blocked, so they keep the old list."""
    settings.EMAIL_BLOCKED_DOMAINS = ["demo.example.com"]


@pytest.fixture
def real_mailer(monkeypatch):
    """The mailer really sends (not the console one), whatever DEBUG / EMAIL_REAL_SENDING the test run has."""
    monkeypatch.setattr(services, "prints_emails", lambda alias="default": False)


@pytest.fixture
def console_mailer(monkeypatch):
    monkeypatch.setattr(services, "prints_emails", lambda alias="default": True)
    monkeypatch.setattr(pages, "prints_emails", lambda alias="default": True)


def reload(registration) -> Registration:
    registration.refresh_from_db()
    return registration


def request(activity, email: str, capture) -> None:
    with capture(execute=True):
        services.request_evaluation_link(activity, email)


# --- What is recorded ----------------------------------------------------------------------------------------


def test_an_attendee_request_is_queued_then_sent(ongoing, real_mailer, django_capture_on_commit_callbacks) -> None:
    request(ongoing["activity"], "PERSON0@example.com ", django_capture_on_commit_callbacks)
    attendee = reload(ongoing["attendee"])
    assert attendee.evaluation_link_request_result == Result.QUEUED and attendee.evaluation_link_requested_at
    assert attendee.evaluation_link_delivery == Delivery.SENT and attendee.evaluation_link_sent_at >= attendee.evaluation_link_requested_at
    assert len(evaluation_emails()) == 1
    assert pages.link_request_outcome(attendee) == {"sent": True, "pending": False, "text": "Sent"}


@pytest.mark.parametrize(("who", "result", "text"), [
    ("absent", Result.NOT_ATTENDED, "Not sent: not marked as attended"),
    ("done", Result.ALREADY_EVALUATED, "Not sent: already evaluated"),
])
def test_a_request_that_sends_nothing_says_why(ongoing, who, result, text, django_capture_on_commit_callbacks) -> None:
    registration = ongoing[who]
    request(ongoing["activity"], registration.email, django_capture_on_commit_callbacks)
    registration = reload(registration)
    assert registration.evaluation_link_request_result == result and evaluation_emails() == []
    assert pages.link_request_outcome(registration)["text"] == text


def test_an_unknown_email_is_stored_nowhere(ongoing, django_capture_on_commit_callbacks) -> None:
    request(ongoing["activity"], "nobody@example.com", django_capture_on_commit_callbacks)
    assert not Registration.objects.filter(evaluation_link_requested_at__isnull=False).exists()
    assert "nobody@example.com" not in str(list(Registration.objects.values_list("email", flat=True)))


def test_a_request_after_the_window_closed_is_recorded(client, ongoing, clock) -> None:
    activity, attendee = ongoing["activity"], ongoing["attendee"]
    clock(activity.evaluation_deadline + timedelta(minutes=5))
    closed_get = client.get(reverse("evaluation_request", args=[activity.pk]))
    response = ask(client, activity, attendee.email)
    assert response.status_code == 200 and response.content == closed_get.content  # the same card as a GET
    attendee = reload(attendee)
    assert attendee.evaluation_link_request_result == Result.WINDOW_CLOSED and evaluation_emails() == []
    assert pages.link_request_outcome(attendee)["text"] == "Not sent: the evaluation was not open"


def test_the_public_answer_is_the_same_in_every_case(client, ongoing, django_capture_on_commit_callbacks) -> None:
    activity = ongoing["activity"]
    answers = set()
    for email in (ongoing["attendee"].email, ongoing["absent"].email, ongoing["done"].email, "nobody@example.com"):
        with django_capture_on_commit_callbacks(execute=True):
            response = ask(client, activity, email)
        answers.add((response.status_code, response.url))
    assert answers == {(302, reverse("evaluation_request_sent", args=[activity.pk]))}


# --- Sent or only printed --------------------------------------------------------------------------------------


def test_development_mode_records_printed_only(ongoing, console_mailer, django_capture_on_commit_callbacks) -> None:
    request(ongoing["activity"], ongoing["attendee"].email, django_capture_on_commit_callbacks)
    attendee = reload(ongoing["attendee"])
    assert attendee.evaluation_link_delivery == Delivery.PRINTED_DEV
    assert pages.link_request_outcome(attendee)["text"] == "Not sent: only printed in the qcluster terminal (development mode)"
    assert pages.link_state(attendee)["label"] == "Printed only"


def test_a_demo_address_is_printed_not_sent(ongoing, real_mailer) -> None:
    attendee = ongoing["attendee"]
    Registration.objects.filter(pk=attendee.pk).update(email="ana@demo.example.com")
    assert services.deliver_evaluation_link(attendee.pk) == "sent"
    attendee = reload(attendee)
    assert attendee.evaluation_link_delivery == Delivery.PRINTED_DEMO


def test_a_failed_email_clears_the_delivery(ongoing, real_mailer, monkeypatch) -> None:
    attendee = ongoing["attendee"]
    services.deliver_evaluation_link(attendee.pk)
    assert reload(attendee).evaluation_link_delivery == Delivery.SENT

    def fail(*args, **kwargs):
        raise ConnectionRefusedError

    monkeypatch.setattr("registrations.emails.send_evaluation_link", fail)
    assert services.deliver_evaluation_link(attendee.pk) == "failed"
    attendee = reload(attendee)
    assert attendee.evaluation_link_delivery is None and attendee.evaluation_link_failed_at


def test_queued_but_not_sent_yet_and_failed_after_the_request() -> None:
    now = timezone.now()
    queued = Registration(evaluation_link_requested_at=now, evaluation_link_request_result=Result.QUEUED,
                          evaluation_link_sent_at=now - timedelta(days=1), evaluation_link_delivery=Delivery.SENT)
    assert pages.link_request_outcome(queued) == {"sent": False, "pending": True, "text": "Queued: waiting for qcluster to send it"}
    failed = Registration(evaluation_link_requested_at=now, evaluation_link_request_result=Result.QUEUED,
                          evaluation_link_failed_at=now + timedelta(seconds=3))
    assert pages.link_request_outcome(failed)["text"] == "Not sent: the email failed (resend it below)"


# --- The Evaluation tab ------------------------------------------------------------------------------------------


def evaluation_tab(member, activity) -> str:
    client = Client()
    client.force_login(member)
    return client.get(reverse("activities:detail", args=[activity.pk]), {"tab": "evaluation"}).content.decode()


def test_evaluation_tab_lists_the_latest_requests(director, ongoing, real_mailer, django_capture_on_commit_callbacks) -> None:
    activity = ongoing["activity"]
    request(activity, ongoing["absent"].email, django_capture_on_commit_callbacks)
    request(activity, ongoing["attendee"].email, django_capture_on_commit_callbacks)
    html = evaluation_tab(director, activity)
    assert "Link requests from the general evaluation link" in html
    assert "Not sent: not marked as attended" in html and ">Sent</span>" in html
    assert html.index(">Sent</span>") < html.index("Not sent: not marked as attended")  # newest first
    assert "nobody@" not in html


def test_evaluation_tab_says_emails_are_only_printed(director, ongoing, console_mailer) -> None:
    assert "Emails are only printed in the qcluster terminal, not sent." in evaluation_tab(director, ongoing["activity"])


def test_evaluation_tab_has_no_printed_note_when_emails_are_sent(director, ongoing, monkeypatch) -> None:
    monkeypatch.setattr(pages, "prints_emails", lambda alias="default": False)
    assert "Emails are only printed in the qcluster terminal" not in evaluation_tab(director, ongoing["activity"])


# --- The rate-limit cache never holds the email ---------------------------------------------------------------


def test_rate_limit_keys_hold_no_email_or_ip(client, ongoing) -> None:
    ask(client, ongoing["activity"], "Person0@Example.com")
    key = ratelimit._key("evaluation_request", "email", "person0@example.com")
    assert "@" not in key and "person0" not in key and cache.get(key) == 1
    assert IP not in ratelimit._key("evaluation_request", "ip", IP)
    assert ratelimit._key("x", "email", "a@example.com") != ratelimit._key("x", "email", "b@example.com")
