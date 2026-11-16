"""Startup login (separate from staff), lockout, set-password links, and the staff/startup separation."""

import time
from unittest import mock

import pytest
from django.core import mail
from django.core.exceptions import PermissionDenied

from accounts.services import INVALID_LOGIN_MESSAGE
from startups import services
from startups.models import Startup

from .conftest import STARTUP_PASSWORD

pytestmark = pytest.mark.django_db

LOGIN = "/startups/portal/api/login/"
ME = "/startups/portal/api/me/"
NEW_PASSWORD = "Brand-New-Pass-2026!"


@pytest.fixture(autouse=True)
def fresh_cache():
    """The lockout counters live in the cache: start every test with none."""
    from django.core.cache import cache

    cache.clear()
    yield
    cache.clear()


def login(client, email: str, password: str):
    return client.post(LOGIN, {"email": email, "password": password}, content_type="application/json")


# --- Login ------------------------------------------------------------------------------------------


def test_login_and_logout(client, startup) -> None:
    response = login(client, "  KALINAW@demo.example.com ", STARTUP_PASSWORD)  # email ignores case and spaces
    assert response.status_code == 200 and response.json()["startup"]["email"] == startup.email
    assert client.session[services.STARTUP_SESSION_KEY] == startup.pk
    assert client.get(ME).status_code == 200
    assert client.get(LOGIN).json()["logged_in"] is True

    assert client.get("/startups/portal/api/logout/").status_code == 405  # POST only
    assert client.post("/startups/portal/api/logout/").json() == {"logged_in": False}
    assert client.get(ME).status_code == 401


@pytest.mark.parametrize("case", ["wrong_password", "unknown_email", "inactive", "no_password"])
def test_refused_logins_all_get_the_same_message(client, make_startup, case) -> None:
    email, password = "kalinaw@demo.example.com", STARTUP_PASSWORD
    if case == "wrong_password":
        make_startup(email=email)
        password = "wrong"
    elif case == "unknown_email":
        email = "nobody@demo.example.com"
    elif case == "inactive":
        make_startup(email=email, is_active=False)
    else:
        make_startup(email=email, password=None)
    response = login(client, email, password)
    assert response.status_code == 400 and response.json() == {"error": INVALID_LOGIN_MESSAGE}
    assert services.STARTUP_SESSION_KEY not in client.session


def test_lockout_after_five_failures_even_with_the_right_password(client, startup, settings) -> None:
    for _ in range(settings.LOGIN_MAX_FAILED_ATTEMPTS):
        assert login(client, startup.email, "wrong").json()["error"] == INVALID_LOGIN_MESSAGE
    response = login(client, startup.email, STARTUP_PASSWORD)
    assert response.status_code == 400 and "Too many failed attempts" in response.json()["error"]


def test_success_resets_the_counter(client, startup, settings) -> None:
    for _ in range(settings.LOGIN_MAX_FAILED_ATTEMPTS - 1):
        login(client, startup.email, "wrong")
    assert login(client, startup.email, STARTUP_PASSWORD).status_code == 200
    client.post("/startups/portal/api/logout/")
    for _ in range(settings.LOGIN_MAX_FAILED_ATTEMPTS - 1):
        login(client, startup.email, "wrong")
    assert login(client, startup.email, STARTUP_PASSWORD).status_code == 200


def test_startup_and_staff_lockout_counters_are_separate(client, startup, make_member, settings) -> None:
    """Five failed STARTUP logins for an email do not lock the STAFF login of the same email, and vice versa."""
    member = make_member("Director", email=startup.email)
    for _ in range(settings.LOGIN_MAX_FAILED_ATTEMPTS):
        login(client, startup.email, "wrong")
    staff = client.post("/accounts/login/", {"email": member.email, "password": "Strong-Pass-2026!"})
    assert staff.status_code == 302  # staff login still works


def test_ip_lockout_counts_startup_attempts(client, make_startup, settings) -> None:
    settings.LOGIN_MAX_FAILED_ATTEMPTS_PER_IP = 3
    startup = make_startup()
    for number in range(3):
        login(client, f"guess{number}@demo.example.com", "wrong")
    assert "Too many failed attempts" in login(client, startup.email, STARTUP_PASSWORD).json()["error"]


def test_deactivated_while_logged_in_loses_access(client, startup) -> None:
    login(client, startup.email, STARTUP_PASSWORD)
    Startup.objects.filter(pk=startup.pk).update(is_active=False)
    assert client.get(ME).status_code == 401


def test_new_password_ends_other_sessions(client, startup) -> None:
    login(client, startup.email, STARTUP_PASSWORD)
    startup.set_password(NEW_PASSWORD)
    startup.save()
    assert client.get(ME).status_code == 401


# --- Staff and startups never cross --------------------------------------------------------------


STAFF_URLS = ["/", "/activities/api/", "/documents/api/", "/notifications/api/", "/startups/api/", "/admin/"]
STARTUP_URLS = [
    "/startups/portal/api/me/", "/startups/portal/api/registrations/", "/startups/portal/api/notifications/",
    "/startups/portal/api/notifications/unread-count/",
]


@pytest.mark.parametrize("url", STAFF_URLS)
def test_startup_cannot_reach_staff_urls(client, startup, url) -> None:
    login(client, startup.email, STARTUP_PASSWORD)
    response = client.get(url)
    if "/api/" in url:  # treated as not logged in: the staff JSON API answers 401
        assert response.status_code == 401 and response.json() == {"error": "Login required."}
    else:  # pages: the staff login page
        assert response.status_code == 302 and "/login/" in response.url


@pytest.mark.parametrize("url", STARTUP_URLS)
def test_staff_cannot_reach_startup_urls(client, admin_member, url) -> None:
    client.force_login(admin_member)
    response = client.get(url)
    assert response.status_code == 403 and response.json()["error"] == "Staff accounts cannot use the startup portal."


def test_staff_cannot_log_in_to_the_portal_or_register(client, admin_member, startup) -> None:
    client.force_login(admin_member)
    assert login(client, startup.email, STARTUP_PASSWORD).status_code == 403
    assert client.post("/startups/portal/api/activities/1/register/", {}, content_type="application/json").status_code == 403
    assert client.get("/startups/api/").status_code == 200  # still a normal staff session


def test_startup_session_key_is_ignored_once_staff_logs_in(client, startup, admin_member) -> None:
    login(client, startup.email, STARTUP_PASSWORD)
    client.force_login(admin_member)  # same browser, staff login
    assert client.get(ME).status_code == 403


def test_staff_account_cannot_log_in_as_a_startup(client, admin_member) -> None:
    response = login(client, admin_member.email, "Strong-Pass-2026!")
    assert response.status_code == 400 and response.json()["error"] == INVALID_LOGIN_MESSAGE


def test_not_logged_in_gets_401(client) -> None:
    for url in STARTUP_URLS:
        assert client.get(url).status_code == 401


# --- Set-password link ---------------------------------------------------------------------------------


def link_path(startup: Startup) -> str:
    return f"/api/startups/set-password/{services.password_token(startup)}/"


def set_password(client, path: str, password: str = NEW_PASSWORD, again: str | None = None):
    return client.post(path, {"password": password, "password_again": again or password}, content_type="application/json")


def test_link_sets_the_password_once(client, make_startup) -> None:
    startup = make_startup(password=None)
    path = link_path(startup)
    check = client.get(path)
    assert check.status_code == 200 and check.json() == {"valid": True, "name": startup.name, "email": startup.email}

    assert set_password(client, path).status_code == 200
    startup.refresh_from_db()
    assert startup.check_password(NEW_PASSWORD)
    assert login(client, startup.email, NEW_PASSWORD).status_code == 200


def test_link_used_twice_is_refused(client, make_startup) -> None:
    startup = make_startup(password=None)
    path = link_path(startup)
    set_password(client, path)
    for response in (client.get(path), set_password(client, path, "Another-Pass-2026!")):
        assert response.status_code == 400 and "already been used" in response.json()["error"]
    startup.refresh_from_db()
    assert startup.check_password(NEW_PASSWORD)


def test_other_unused_links_stop_working_after_one_is_used(client, startup) -> None:
    first, second = link_path(startup), link_path(startup)
    assert set_password(client, first).status_code == 200
    assert "already been used" in set_password(client, second, "Another-Pass-2026!").json()["error"]


def test_expired_link_is_refused(client, startup, settings) -> None:
    path = link_path(startup)
    later = time.time() + settings.STARTUP_PASSWORD_LINK_HOURS * 3600 + 60
    with mock.patch("django.core.signing.time.time", return_value=later):
        response = set_password(client, path)
    assert response.status_code == 400 and "expired" in response.json()["error"]
    startup.refresh_from_db()
    assert startup.check_password(STARTUP_PASSWORD)  # unchanged


def test_link_still_works_just_before_expiry(client, startup, settings) -> None:
    path = link_path(startup)
    almost = time.time() + settings.STARTUP_PASSWORD_LINK_HOURS * 3600 - 60
    with mock.patch("django.core.signing.time.time", return_value=almost):
        assert set_password(client, path).status_code == 200


@pytest.mark.parametrize("tamper", ["swap_last_char", "other_startup_id", "garbage"])
def test_tampered_link_is_refused(client, startup, make_startup, tamper) -> None:
    token = services.password_token(startup)
    if tamper == "swap_last_char":
        token = token[:-1] + ("A" if token[-1] != "A" else "B")
    elif tamper == "other_startup_id":
        other = make_startup(email="other@demo.example.com")
        payload, rest = token.split(":", 1)
        other_payload = services.password_token(other).split(":", 1)[0]
        token = f"{other_payload}:{rest}"  # someone else's data with this signature
    else:
        token = "not-a-real-token"
    response = set_password(client, f"/api/startups/set-password/{token}/")
    assert response.status_code == 400 and response.json()["error"] == services.LINK_INVALID
    startup.refresh_from_db()
    assert startup.check_password(STARTUP_PASSWORD)


def test_link_for_an_inactive_startup_is_refused(client, make_startup) -> None:
    startup = make_startup(is_active=False)
    assert set_password(client, link_path(startup)).json()["error"] == services.LINK_INVALID


def test_password_must_pass_the_validators(client, startup) -> None:
    path = link_path(startup)
    weak = set_password(client, path, "12345678")
    assert weak.status_code == 400 and "password" in weak.json()["errors"]
    mismatch = set_password(client, path, NEW_PASSWORD, "Different-Pass-2026!")
    assert mismatch.status_code == 400 and "password_again" in mismatch.json()["errors"]
    assert set_password(client, path).status_code == 200  # the link was not used up by the refusals


def test_send_link_needs_can_manage_users(startup, admin_member, view_only, settings) -> None:
    with pytest.raises(PermissionDenied):
        services.send_password_link(view_only, startup)
    services.send_password_link(admin_member, startup)
    message = mail.outbox[-1]
    assert message.to == [startup.email]
    assert f"{settings.SITE_URL}/startups/set-password/" in message.body
    assert "72 hours" in message.body


def test_send_link_refused_for_inactive_startup(make_startup, admin_member) -> None:
    with pytest.raises(services.StartupError, match="not active"):
        services.send_password_link(admin_member, make_startup(is_active=False))
    assert not mail.outbox


def test_emailed_link_works(client, startup, admin_member) -> None:
    services.send_password_link(admin_member, startup)
    from urllib.parse import urlsplit

    link = next(line for line in mail.outbox[-1].body.splitlines() if "/set-password/" in line).strip()
    assert urlsplit(link).path.startswith("/startups/set-password/")  # the email opens the page
    assert set_password(client, "/api" + urlsplit(link).path).status_code == 200  # the page posts to the API
