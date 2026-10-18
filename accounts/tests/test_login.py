"""Staff login, logout, lockout, first-sign-in password change, and startup separation."""

import logging

import pytest
from django.contrib.auth import SESSION_KEY
from django.urls import reverse

from accounts.services import INVALID_LOGIN_MESSAGE
from conftest import STRONG_PASSWORD
from startups.models import Startup
from startups.services import STARTUP_SESSION_KEY

LOGIN = reverse("accounts:login")
LOGOUT = reverse("accounts:logout")
CHANGE = reverse("accounts:password_change")
HOME = reverse("home")


def post_login(client, email: str, password: str):
    return client.post(LOGIN, {"email": email, "password": password})


def is_logged_in(client) -> bool:
    return SESSION_KEY in client.session


# --- Login --------------------------------------------------------------------------


@pytest.mark.django_db
def test_login_with_email_and_password(client, make_member) -> None:
    member = make_member("Director")
    response = post_login(client, member.email, STRONG_PASSWORD)
    assert response.status_code == 302 and response.url == HOME
    assert is_logged_in(client)


@pytest.mark.django_db
def test_email_is_case_insensitive(client, make_member) -> None:
    make_member("Director", email="director@example.com")
    post_login(client, "DIRECTOR@Example.com", STRONG_PASSWORD)
    assert is_logged_in(client)


@pytest.mark.django_db
@pytest.mark.parametrize("case", ["wrong password", "unknown email", "deactivated"])
def test_failed_logins_give_the_same_message(client, make_member, case) -> None:
    make_member("Director", email="director@example.com", is_active=(case != "deactivated"))
    email = "nobody@example.com" if case == "unknown email" else "director@example.com"
    password = "wrong-password" if case == "wrong password" else STRONG_PASSWORD

    response = post_login(client, email, password)

    assert response.status_code == 200
    assert INVALID_LOGIN_MESSAGE in response.content.decode()
    assert not is_logged_in(client)


@pytest.mark.django_db
def test_member_deactivated_while_logged_in_loses_access(client, make_member) -> None:
    member = make_member("Director")
    client.force_login(member)
    member.is_active = False
    member.save()
    response = client.get(HOME)
    assert response.status_code == 302 and response.url.startswith(LOGIN)


@pytest.mark.django_db
def test_admin_site_login_uses_staff_login(client) -> None:
    response = client.get("/admin/login/?next=/admin/")
    assert response.status_code == 302 and response.url.startswith(LOGIN)


# --- Logout -------------------------------------------------------------------------


@pytest.mark.django_db
def test_logout_requires_post(client, make_member) -> None:
    client.force_login(make_member("Director"))
    assert client.get(LOGOUT).status_code == 405
    assert is_logged_in(client)

    response = client.post(LOGOUT)
    assert response.status_code == 302 and response.url == LOGIN
    assert not is_logged_in(client)


# --- Brute-force lockout ------------------------------------------------------------


@pytest.mark.django_db
def test_lockout_after_repeated_failures(client, make_member, settings) -> None:
    member = make_member("Director")
    for _ in range(settings.LOGIN_MAX_FAILED_ATTEMPTS):
        post_login(client, member.email, "wrong-password")

    response = post_login(client, member.email, STRONG_PASSWORD)  # even the right password is refused

    assert "Too many failed attempts" in response.content.decode()
    assert not is_logged_in(client)


@pytest.mark.django_db
def test_lockout_is_logged(client, make_member, settings, caplog) -> None:
    member = make_member("Director")
    with caplog.at_level(logging.INFO):
        for _ in range(settings.LOGIN_MAX_FAILED_ATTEMPTS):
            post_login(client, member.email, "wrong-password")
    lockouts = [r for r in caplog.records if r.name == "accounts.security" and "Login lockout" in r.getMessage()]
    assert len(lockouts) == 1
    assert member.email in lockouts[0].getMessage()
    assert "wrong-password" not in caplog.text


@pytest.mark.django_db
def test_lockout_does_not_reveal_whether_email_exists(client, settings) -> None:
    for _ in range(settings.LOGIN_MAX_FAILED_ATTEMPTS):
        post_login(client, "nobody@example.com", "wrong-password")
    response = post_login(client, "nobody@example.com", "wrong-password")
    assert "Too many failed attempts" in response.content.decode()


@pytest.mark.django_db
def test_successful_login_resets_the_counter(client, make_member, settings) -> None:
    member = make_member("Director")
    for _ in range(settings.LOGIN_MAX_FAILED_ATTEMPTS - 1):
        post_login(client, member.email, "wrong-password")
    post_login(client, member.email, STRONG_PASSWORD)
    client.post(LOGOUT)
    for _ in range(settings.LOGIN_MAX_FAILED_ATTEMPTS - 1):
        post_login(client, member.email, "wrong-password")

    post_login(client, member.email, STRONG_PASSWORD)
    assert is_logged_in(client)


@pytest.mark.django_db
def test_lockout_per_ip_address(client, make_member, settings) -> None:
    member = make_member("Director")
    for number in range(settings.LOGIN_MAX_FAILED_ATTEMPTS_PER_IP):
        post_login(client, f"guess{number}@example.com", "wrong-password")  # different emails, same IP
    response = post_login(client, member.email, STRONG_PASSWORD)
    assert "Too many failed attempts" in response.content.decode()


# --- First-sign-in password change ------------------------------------------------


@pytest.fixture
def new_member(make_member):
    return make_member("Director", must_change_password=True)


@pytest.mark.django_db
def test_login_with_must_change_password_goes_to_change_page(client, new_member) -> None:
    response = post_login(client, new_member.email, STRONG_PASSWORD)
    assert response.status_code == 302 and response.url == CHANGE


@pytest.mark.django_db
@pytest.mark.parametrize("path", ["/", "/admin/", "/accounts/api/members/", "/activities/", "/media/demo/x.txt"])
def test_every_page_redirects_until_password_changed(client, new_member, path) -> None:
    client.force_login(new_member)
    response = client.get(path)
    assert response.status_code == 302 and response.url == CHANGE


@pytest.mark.django_db
def test_change_page_and_logout_are_reachable(client, new_member) -> None:
    client.force_login(new_member)
    assert client.get(CHANGE).status_code == 200
    assert client.post(LOGOUT).url == LOGIN


@pytest.mark.django_db
def test_password_change_clears_the_flag(client, new_member) -> None:
    client.force_login(new_member)
    response = client.post(
        CHANGE,
        {"old_password": STRONG_PASSWORD, "new_password1": "Brand-New-Pass-77", "new_password2": "Brand-New-Pass-77"},
    )
    assert response.status_code == 302 and response.url == HOME
    new_member.refresh_from_db()
    assert new_member.must_change_password is False
    assert new_member.check_password("Brand-New-Pass-77")
    assert client.get(HOME).status_code == 200  # still logged in, no more redirect


@pytest.mark.django_db
@pytest.mark.parametrize(
    "old, new, message",
    [
        ("wrong-old-password", "Brand-New-Pass-77", "current password is incorrect"),
        (STRONG_PASSWORD, STRONG_PASSWORD, "must be different"),
        (STRONG_PASSWORD, "12345678", "too common"),  # Django password validators
        (STRONG_PASSWORD, "Ab1!", "too short"),
    ],
)
def test_password_change_rules(client, new_member, old, new, message) -> None:
    client.force_login(new_member)
    response = client.post(CHANGE, {"old_password": old, "new_password1": new, "new_password2": new})
    assert response.status_code == 200
    assert message in response.content.decode()
    new_member.refresh_from_db()
    assert new_member.must_change_password is True


# --- Startups are never staff ------------------------------------------------------


@pytest.mark.django_db
def test_startup_credentials_cannot_log_in_as_staff(client) -> None:
    startup = Startup.objects.create(name="Acme", email="acme@example.com")
    startup.set_password(STRONG_PASSWORD)
    startup.save()
    response = post_login(client, "acme@example.com", STRONG_PASSWORD)
    assert INVALID_LOGIN_MESSAGE in response.content.decode()
    assert not is_logged_in(client)


@pytest.mark.django_db
@pytest.mark.parametrize("path", ["/", "/accounts/api/members/", "/accounts/password/change/"])
def test_startup_session_never_reaches_staff_views(client, path) -> None:
    startup = Startup.objects.create(name="Acme", email="acme@example.com")
    session = client.session
    session[STARTUP_SESSION_KEY] = startup.pk
    session.save()
    response = client.get(path)
    assert response.status_code == 302 and response.url.startswith(LOGIN)
