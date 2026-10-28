"""Phase F3: the startup's "set your password" page (/startups/set-password/<token>/). The link rules are tested with
the services and the JSON endpoint (test_login.py); these tests check the page."""

import re
import time
from unittest import mock

import pytest
from django.urls import reverse

from startups import services

from .conftest import STARTUP_PASSWORD

pytestmark = pytest.mark.django_db

NEW_PASSWORD = "Brand-New-Pass-2026!"


def url(startup) -> str:
    return reverse("startup_set_password", args=[services.password_token(startup)])


def html(response) -> str:
    return response.content.decode()


def assert_private(response) -> None:
    assert response["Referrer-Policy"] == "same-origin"  # not no-referrer: browsers would send Origin: null and CSRF fails
    assert response["X-Robots-Tag"] == "noindex, nofollow"
    assert "no-store" in response["Cache-Control"]
    assert '<meta name="referrer" content="same-origin">' in html(response)


def test_valid_link_shows_the_startup_and_the_rules(client, startup) -> None:
    response = client.get(url(startup))
    text = html(response)
    assert response.status_code == 200
    assert_private(response)
    assert startup.name in text and startup.email in text
    assert 'name="password"' in text and 'name="password_again"' in text and 'autocomplete="new-password"' in text
    assert "Your password must contain at least 8 characters." in text  # Django's rules as helper text


def test_errors_are_shown_under_the_fields_and_the_link_still_works(client, startup) -> None:
    path = url(startup)
    response = client.post(path, {"password": NEW_PASSWORD, "password_again": "Something-Else-1"})
    text = html(response)
    assert response.status_code == 400 and "The two passwords do not match." in text
    assert 'id="id_password_again-error"' in text and re.search(r'<input id="id_password_again"[^>]*autofocus', text)

    response = client.post(path, {"password": "short", "password_again": "short"})
    assert response.status_code == 400 and 'id="id_password-error"' in html(response) and "too short" in html(response)
    assert NEW_PASSWORD not in html(response) and "short" not in re.findall(r'value="([^"]*)"', html(response))

    startup.refresh_from_db()
    assert startup.check_password(STARTUP_PASSWORD)  # unchanged; the link still works
    assert client.get(path).status_code == 200


def test_success_sets_the_password_and_the_link_stops_working(client, startup) -> None:
    path = url(startup)
    response = client.post(path, {"password": NEW_PASSWORD, "password_again": NEW_PASSWORD})
    assert response.status_code == 302 and response["Location"] == reverse("startup_set_password_done")
    startup.refresh_from_db()
    assert startup.check_password(NEW_PASSWORD)

    done = client.get(response["Location"])
    assert done.status_code == 200 and "Your password is set." in html(done)
    assert_private(done)
    assert startup.name not in html(done) and startup.email not in html(done)

    used = client.get(path)
    assert used.status_code == 200 and "already been used" in html(used) and "Ask the TBI" in html(used)
    assert startup.name not in html(used) and startup.email not in html(used)


def test_expired_link_shows_a_card(client, startup, settings) -> None:
    path = url(startup)
    later = time.time() + settings.STARTUP_PASSWORD_LINK_HOURS * 3600 + 60
    with mock.patch("django.core.signing.time.time", return_value=later):
        response = client.get(path)
    assert response.status_code == 200 and services.LINK_EXPIRED in html(response)
    assert_private(response)
    assert startup.email not in html(response) and 'name="password"' not in html(response)


def test_invalid_link_shows_a_card(client, startup) -> None:
    response = client.get(reverse("startup_set_password", args=["not-a-real-token"]))
    text = html(response)
    assert response.status_code == 404 and services.LINK_INVALID in text and "Ask the TBI to send you a new one." in text
    assert_private(response)
    assert startup.name not in text and 'name="password"' not in text
    response = client.post(reverse("startup_set_password", args=["not-a-real-token"]), {"password": NEW_PASSWORD, "password_again": NEW_PASSWORD})
    assert response.status_code == 404
    startup.refresh_from_db()
    assert startup.check_password(STARTUP_PASSWORD)


def test_the_json_endpoint_is_unchanged(client, startup) -> None:
    response = client.get(f"/api/startups/set-password/{services.password_token(startup)}/")
    assert response.json() == {"valid": True, "name": startup.name, "email": startup.email}
