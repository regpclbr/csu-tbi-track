"""Password resets for staff: an Admin action (new temporary password by email, like the invitation)
and Django's self-service "forgot password" by email (same password validators)."""

import json
import re
from datetime import timedelta

import pytest
from django.contrib.auth import SESSION_KEY
from django.core import mail
from django.core.exceptions import PermissionDenied

from accounts import services
from accounts.models import TBIMember
from conftest import STRONG_PASSWORD

pytestmark = pytest.mark.django_db

NEW_PASSWORD = "Another-Strong-2026!"


def temporary_password() -> str:
    return re.search(r"Temporary password: (\S+)", mail.outbox[-1].body).group(1)


# --- Admin reset ---------------------------------------------------------------------------------------


def test_admin_reset_emails_a_temporary_password_and_forces_a_change(client, admin_member, make_member, caplog) -> None:
    member = make_member("Director")
    with caplog.at_level("INFO", logger="accounts.security"):
        services.reset_member_password(admin_member, member)
    member.refresh_from_db()
    assert member.must_change_password is True and not member.check_password(STRONG_PASSWORD)
    message = mail.outbox[-1]
    assert message.to == [member.email] and "password was reset" in message.subject
    password = temporary_password()
    assert member.check_password(password)
    assert password not in caplog.text and f"Password reset by an Admin: member_id={member.pk}" in caplog.text

    response = client.post("/accounts/login/", {"email": member.email, "password": password})
    assert response.status_code == 302 and response.url == "/accounts/password/change/"


def test_admin_reset_ends_the_members_open_sessions(client, admin_member, make_member) -> None:
    member = make_member("Director")
    client.force_login(member)
    assert client.get("/activities/api/").status_code == 200
    services.reset_member_password(admin_member, member)
    assert client.get("/activities/api/").status_code == 401  # the old session no longer works


def test_admin_reset_rules(admin_member, make_member) -> None:
    with pytest.raises(PermissionDenied):
        services.reset_member_password(make_member("Director"), admin_member)  # needs can_manage_users
    with pytest.raises(services.MemberManagementError, match="your own account"):
        services.reset_member_password(admin_member, admin_member)
    inactive = make_member("Admin Staff", is_active=False)
    with pytest.raises(services.MemberManagementError, match="Reactivate"):
        services.reset_member_password(admin_member, inactive)
    assert mail.outbox == []


def test_admin_reset_api(client, admin_member, make_member) -> None:
    member = make_member("Director")
    client.force_login(admin_member)
    response = client.post(f"/accounts/api/members/{member.pk}/reset-password/")
    assert response.status_code == 200 and response.json()["member"]["must_change_password"] is True
    assert "Temporary password" not in json.dumps(response.json())  # never returned by the API
    assert client.post(f"/accounts/api/members/{admin_member.pk}/reset-password/").status_code == 400
    member.refresh_from_db()  # the reset changed the password hash
    member.must_change_password = False
    member.save(update_fields=["must_change_password"])
    client.force_login(member)
    assert client.post(f"/accounts/api/members/{admin_member.pk}/reset-password/").status_code == 403


def test_admin_reset_in_the_django_admin(client, admin_member, make_member) -> None:
    member = make_member("Director")
    client.force_login(admin_member)
    response = client.post(
        "/admin/accounts/tbimember/", {"action": "reset_password_selected", "_selected_action": [member.pk]}, follow=True
    )
    assert response.status_code == 200 and f"A new temporary password was emailed to {member.email}." in response.content.decode()
    member.refresh_from_db()
    assert member.must_change_password is True


# --- Forgot password (self-service) -----------------------------------------------------------------------


def reset_link() -> str:
    return re.search(r"http://\S+/accounts/password/reset/\S+/", mail.outbox[-1].body).group(0)


def test_forgot_password_emails_a_single_use_link(client, make_member, settings) -> None:
    member = make_member("Director", email="ana@example.com", must_change_password=True)
    response = client.post("/accounts/password/forgot/", {"email": "ANA@example.com"})
    assert response.status_code == 302 and response.url == "/accounts/password/forgot/sent/"
    link = reset_link()
    assert link.startswith(settings.SITE_URL) and mail.outbox[-1].to == ["ana@example.com"]

    form_page = client.get(link.removeprefix(settings.SITE_URL), follow=True)  # Django moves the token into the session
    assert "Choose a new password" in form_page.content.decode() and form_page.context["validlink"]
    set_url = form_page.redirect_chain[-1][0]
    weak = client.post(set_url, {"new_password1": "12345678", "new_password2": "12345678"})
    assert weak.status_code == 200 and "too common" in weak.content.decode()  # the same validators
    done = client.post(set_url, {"new_password1": NEW_PASSWORD, "new_password2": NEW_PASSWORD})
    assert done.status_code == 302 and done.url == "/accounts/password/reset/done/"

    member.refresh_from_db()
    assert member.check_password(NEW_PASSWORD) and member.must_change_password is False  # chosen by the member
    again = client.get(link.removeprefix(settings.SITE_URL), follow=True)
    assert again.context["validlink"] is False  # single use
    login = client.post("/accounts/login/", {"email": "ana@example.com", "password": NEW_PASSWORD})
    assert login.status_code == 302 and SESSION_KEY in client.session


@pytest.mark.parametrize("case", ["unknown", "deactivated", "startup"])
def test_forgot_password_never_reveals_whether_the_email_exists(client, make_member, case) -> None:
    from startups.models import Startup

    make_member("Director", email="ana@example.com", is_active=case != "deactivated")
    Startup.objects.create(name="Acme", email="acme@example.com")
    email = {"unknown": "nobody@example.com", "deactivated": "ana@example.com", "startup": "acme@example.com"}[case]
    response = client.post("/accounts/password/forgot/", {"email": email})
    assert response.status_code == 302 and response.url == "/accounts/password/forgot/sent/"  # the same answer
    assert mail.outbox == []  # but no email: unknown, deactivated, or not a staff account


def test_login_page_links_to_forgot_password(client) -> None:
    assert "/accounts/password/forgot/" in client.get("/accounts/login/").content.decode()


def test_reset_link_expires(client, make_member, settings, monkeypatch) -> None:
    make_member("Director", email="ana@example.com")
    client.post("/accounts/password/forgot/", {"email": "ana@example.com"})
    link = reset_link().removeprefix(settings.SITE_URL)
    from django.contrib.auth.tokens import default_token_generator

    real_now = default_token_generator._now
    later = timedelta(seconds=settings.PASSWORD_RESET_TIMEOUT + 1)
    monkeypatch.setattr(default_token_generator, "_now", lambda: real_now() + later)
    assert client.get(link, follow=True).context["validlink"] is False
    assert TBIMember.objects.get(email="ana@example.com").check_password(STRONG_PASSWORD)  # unchanged
