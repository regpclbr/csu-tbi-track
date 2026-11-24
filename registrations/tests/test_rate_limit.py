"""Public registration: a rate limit per IP address and per email, and a hidden honeypot field."""

import json

import pytest
from django.core.cache import cache

from activities.models import Activity
from registrations import services
from registrations.models import Registration

from .conftest import FIXED, registration_answers

pytestmark = pytest.mark.django_db


def test_default_limits_allow_a_whole_campus_network() -> None:
    """Many participants register from one campus network (one public IP), so the IP limit is high.
    (Read from the settings module itself: the autouse fixture below lowers them for the other tests.)"""
    from config import settings as project_settings

    assert project_settings.REGISTRATION_MAX_ATTEMPTS_PER_IP == 300
    assert project_settings.REGISTRATION_MAX_ATTEMPTS_PER_EMAIL == 10
    assert project_settings.REGISTRATION_RATE_WINDOW_MINUTES == 60


def test_one_campus_network_can_register_many_people(client, scheduled, settings) -> None:
    """With the real per-IP limit, 40 different people from one IP address all get in (before:
    30 attempts per IP would have blocked the last ones)."""
    settings.REGISTRATION_MAX_ATTEMPTS_PER_IP = 300
    settings.REGISTRATION_MAX_ATTEMPTS_PER_EMAIL = 10
    Activity.objects.filter(pk=scheduled.pk).update(max_slots=50)  # test setup only (not a user edit)
    for number in range(40):
        assert post(client, scheduled, f"student{number}@example.com", ip="203.0.113.9").status_code == 201
    assert Registration.objects.count() == 40


@pytest.fixture(autouse=True)
def small_limits(settings, db):
    """3 attempts per IP and 2 per email in each window; counters start empty."""
    settings.REGISTRATION_MAX_ATTEMPTS_PER_IP = 3
    settings.REGISTRATION_MAX_ATTEMPTS_PER_EMAIL = 2
    settings.REGISTRATION_RATE_WINDOW_MINUTES = 60
    cache.clear()
    yield
    cache.clear()


def post(client, activity, email: str, ip: str = "203.0.113.5", forwarded_for: str | None = None, **extra):
    """POST a registration from `ip` (REMOTE_ADDR); `extra` goes into the JSON body (e.g. website=)."""
    body = {"name": "Ana Cruz", "email": email, **FIXED, "answers": registration_answers(activity), "privacy_consent": True,
            **extra}
    headers = {"HTTP_X_FORWARDED_FOR": forwarded_for} if forwarded_for else {}
    return client.post(f"/api/register/{activity.pk}/", json.dumps(body), content_type="application/json", REMOTE_ADDR=ip, **headers)


def test_one_email_is_limited(client, scheduled) -> None:
    assert post(client, scheduled, "ana@example.com").status_code == 201
    assert post(client, scheduled, "ANA@example.com ").status_code == 400  # already registered (still counts)
    response = post(client, scheduled, "ana@example.com")
    assert response.status_code == 429
    assert response.json() == {"error": "Too many registration attempts. Please try again in 60 minutes."}
    assert post(client, scheduled, "ben@example.com").status_code == 429  # 4th attempt from this IP


def test_one_ip_address_is_limited_but_others_are_not(client, scheduled) -> None:
    for number in range(3):
        assert post(client, scheduled, f"person{number}@example.com").status_code == 201
    assert post(client, scheduled, "person3@example.com").status_code == 429
    assert post(client, scheduled, "person3@example.com", ip="198.51.100.7").status_code == 201  # another IP
    assert Registration.objects.count() == 4


def test_refused_attempts_count_too(client, scheduled, caplog) -> None:
    for number in range(3):
        assert post(client, scheduled, f"not-an-email-{number}").status_code == 400
    with caplog.at_level("WARNING", logger="accounts.security"):
        assert post(client, scheduled, "ana@example.com").status_code == 429
    assert "Public registration rate limit reached (ip=203.0.113.5, by ip)." in caplog.text
    assert "ana@example.com" not in caplog.text  # never the email


def test_the_window_ends(client, scheduled) -> None:
    for number in range(3):
        post(client, scheduled, f"person{number}@example.com")
    assert post(client, scheduled, "late@example.com").status_code == 429
    cache.clear()  # what the cache does when REGISTRATION_RATE_WINDOW_MINUTES have passed
    assert post(client, scheduled, "late@example.com").status_code == 201


def test_honeypot_is_refused_and_nothing_is_saved(client, scheduled, caplog) -> None:
    with caplog.at_level("WARNING", logger="accounts.security"):
        response = post(client, scheduled, "bot@example.com", website="http://spam.example.com")
    assert response.status_code == 400 and response.json() == {"error": services.HONEYPOT_MESSAGE}
    assert Registration.objects.count() == 0
    assert "the hidden field was filled in (ip=203.0.113.5)" in caplog.text
    assert post(client, scheduled, "ana@example.com", website="").status_code == 201  # empty is fine


def test_public_page_data_names_the_honeypot_field(client, scheduled) -> None:
    assert client.get(f"/api/register/{scheduled.pk}/").json()["activity"]["honeypot_field"] == "website"


def test_the_limit_uses_the_trusted_proxy_header(client, scheduled, settings) -> None:
    settings.TRUSTED_PROXY_HEADER = "X-Forwarded-For"
    proxy = "10.0.0.1"  # every request comes from the proxy
    for number in range(3):
        response = post(client, scheduled, f"person{number}@example.com", ip=proxy, forwarded_for="203.0.113.5")
        assert response.status_code == 201
    assert post(client, scheduled, "x@example.com", ip=proxy, forwarded_for="203.0.113.5").status_code == 429
    # Another client behind the same proxy is not blocked.
    assert post(client, scheduled, "y@example.com", ip=proxy, forwarded_for="198.51.100.7").status_code == 201


def test_the_startup_portal_is_not_limited_by_the_public_counter(scheduled, signup) -> None:
    """The limit guards the public API view only; the service itself (portal, seed) never counts."""
    for number in range(5):
        signup(scheduled, f"person{number}@example.com")
    assert Registration.objects.count() == 5
