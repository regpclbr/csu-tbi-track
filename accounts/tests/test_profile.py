"""My profile and positions (ASSUMPTIONS 390): own account only, name and position (a job title, never a permission),
email and role read only, the signatory note, the password change (current password, the new one twice, Django's rules,
stays signed in, the confirmation email, the rate limit, never a password in a log), the Admin Console's Positions tab
and the sidebar name card."""

import re
from html import unescape

import pytest
from django.contrib.auth import SESSION_KEY
from django.core.exceptions import PermissionDenied, ValidationError
from django.core import mail
from django.test import Client
from django.urls import reverse

from accounts import services
from accounts.models import Position, Role, TBIMember
from accounts.permissions import Permission, has_permission
from conftest import STRONG_PASSWORD, VIEW_ONLY_ROLE

pytestmark = pytest.mark.django_db

URL = reverse("accounts:profile")
NEW_PASSWORD = "Another-Strong-Pass-77!"


def client_for(member) -> Client:
    client = Client()
    client.force_login(member)
    return client


def messages_of(response) -> list[str]:
    return [str(message) for message in response.context["messages"]]


@pytest.fixture
def member(make_member):
    return make_member("Admin Staff")


@pytest.fixture
def director(make_member):
    return make_member("Director")


@pytest.fixture
def sysadmin(make_member):
    return make_member(Role.SYSTEM_ADMINISTRATOR)


@pytest.fixture
def manager(sysadmin):
    return Position.objects.create(name="Incubation Manager")


# --- Name and position -------------------------------------------------------------------------------------------


def test_the_page_shows_your_own_account_with_email_and_role_read_only(member) -> None:
    html = client_for(member).get(URL).content.decode()
    assert member.name in html and member.email in html and "Admin Staff" in html
    assert "Your role is set by the System Administrator." in html and "Only the System Administrator changes it." in html
    assert 'name="email"' not in html and 'name="role"' not in html  # never editable here
    assert client_for(member).get(URL + "?member=1").status_code == 200  # no other member can be named


def test_changing_name_and_position(member, manager, caplog) -> None:
    with caplog.at_level("INFO", logger="accounts.audit"):
        response = client_for(member).post(URL, {"form": "profile", "name": "  Ana   Reyes ", "position": manager.pk},
                                           follow=True)
    assert "Your profile was saved." in messages_of(response)
    member.refresh_from_db()
    assert (member.name, member.position) == ("Ana Reyes", manager)
    assert f"Own profile changed: member_id={member.pk}" in caplog.text
    html = response.content.decode()
    assert re.search(r'data-position>Incubation Manager<', html)


def test_only_your_own_account_changes(member, make_member, manager) -> None:
    other = make_member("Director")
    client_for(member).post(URL, {"form": "profile", "name": "Changed", "member_id": other.pk, "email": "x@example.com",
                                  "role": other.role_id, "position": manager.pk})
    other.refresh_from_db()
    member.refresh_from_db()
    assert other.name == "Test Director" and other.position is None
    assert member.name == "Changed" and member.email == "admin.staff@example.com" and member.role.role_name == "Admin Staff"


def test_bad_name_or_position_is_refused(member, manager) -> None:
    client = client_for(member)
    response = client.post(URL, {"form": "profile", "name": "   ", "position": ""})
    assert response.status_code == 400 and "Type your name." in response.content.decode()
    response = client.post(URL, {"form": "profile", "name": "Ana", "position": "999999"})
    assert response.status_code == 400 and "Choose a position from the list." in response.content.decode()
    response = client.post(URL, {"form": "profile", "name": "x" * 256, "position": ""})
    assert response.status_code == 400 and "at most 255 characters" in response.content.decode()
    member.refresh_from_db()
    assert member.name == "Test Admin Staff"


def test_an_inactive_position_is_not_offered_but_kept(member, manager, sysadmin) -> None:
    services.update_own_profile(member, name=member.name, position_id=manager.pk)
    old = Position.objects.create(name="Old Title", is_active=False)
    html = client_for(member).get(URL).content.decode()
    assert "Old Title" not in html  # not offered
    with pytest.raises(ValidationError):
        services.update_own_profile(member, name=member.name, position_id=old.pk)
    services.set_position_active(sysadmin, manager, False)
    html = client_for(member).get(URL).content.decode()
    assert "Incubation Manager (no longer offered)" in html  # your own stays selectable
    services.update_own_profile(member, name="Kept", position_id=manager.pk)
    member.refresh_from_db()
    assert member.position == manager


def test_a_position_gives_no_permission(make_member) -> None:
    viewer = make_member(VIEW_ONLY_ROLE)
    director_title = Position.objects.create(name="Director")
    services.update_own_profile(viewer, name=viewer.name, position_id=director_title.pk)
    viewer.refresh_from_db()
    assert not any(has_permission(viewer, permission) for permission in Permission)
    assert client_for(viewer).get(reverse("accounts:console")).status_code == 403


def test_the_signatory_note(member, director) -> None:
    from certificates.models import CertificateSignatory

    assert "data-signatory-note" not in client_for(member).get(URL).content.decode()
    CertificateSignatory.objects.create(name="  test ADMIN staff ", position="Staff")
    html = client_for(member).get(URL).content.decode()
    assert "data-signatory-note" in html and "does not change the name printed on certificates" in html


# --- Password --------------------------------------------------------------------------------------------------------


def change(client: Client, current: str, new1: str, new2: str | None = None):
    return client.post(URL, {"form": "password", "current_password": current, "new_password1": new1,
                             "new_password2": new1 if new2 is None else new2}, follow=True)


def test_password_change_keeps_you_signed_in_and_emails_you(member, caplog) -> None:
    client = client_for(member)
    with caplog.at_level("INFO"):
        response = change(client, STRONG_PASSWORD, NEW_PASSWORD)
    assert response.redirect_chain[-1][0] == URL
    assert any(m.startswith("Your password was changed.") for m in messages_of(response))
    member.refresh_from_db()
    assert member.check_password(NEW_PASSWORD)
    assert client.session.get(SESSION_KEY) == str(member.pk)  # still signed in
    assert client.get(URL).status_code == 200
    sent = mail.outbox[-1]
    assert sent.to == [member.email] and sent.subject == "Your CSU-TBI Track password was changed"
    assert NEW_PASSWORD not in sent.body and STRONG_PASSWORD not in sent.body
    assert NEW_PASSWORD not in caplog.text and STRONG_PASSWORD not in caplog.text
    assert f"Own password changed: member_id={member.pk}" in caplog.text


def test_the_service_also_clears_must_change_password(member) -> None:
    """(With must_change_password the first-sign-in page is the only page anyway; the service clears it either way.)"""
    TBIMember.objects.filter(pk=member.pk).update(must_change_password=True)
    member.refresh_from_db()
    assert services.change_own_password(member, STRONG_PASSWORD, NEW_PASSWORD, NEW_PASSWORD) is True
    member.refresh_from_db()
    assert member.check_password(NEW_PASSWORD) and not member.must_change_password


def test_wrong_current_password(member) -> None:
    response = change(client_for(member), "not-my-password", NEW_PASSWORD)
    assert response.status_code == 400 and "Your current password is not correct." in response.content.decode()
    member.refresh_from_db()
    assert member.check_password(STRONG_PASSWORD) and not mail.outbox


def test_the_two_new_passwords_must_match(member) -> None:
    response = change(client_for(member), STRONG_PASSWORD, NEW_PASSWORD, NEW_PASSWORD + "x")
    assert response.status_code == 400 and "The two new passwords do not match." in response.content.decode()


@pytest.mark.parametrize("weak, message", [
    ("Ab1!", "too short"),
    ("password123", "too common"),
    ("8423917560", "entirely numeric"),
    ("admin.staff@example.com", "too similar"),
])
def test_django_password_rules(member, weak, message) -> None:
    response = change(client_for(member), STRONG_PASSWORD, weak)
    assert response.status_code == 400 and message in unescape(response.content.decode())
    member.refresh_from_db()
    assert member.check_password(STRONG_PASSWORD)


def test_the_new_password_must_differ(member) -> None:
    response = change(client_for(member), STRONG_PASSWORD, STRONG_PASSWORD)
    assert response.status_code == 400 and "must be different" in response.content.decode()


def test_wrong_current_passwords_are_limited(member) -> None:
    client = client_for(member)
    for _ in range(services.WRONG_PASSWORD_LIMIT):
        change(client, "wrong", NEW_PASSWORD)
    response = change(client, "wrong", NEW_PASSWORD)
    assert services.PASSWORD_LIMIT_REACHED in response.content.decode()


def test_a_failed_email_keeps_the_new_password(member, monkeypatch) -> None:
    def broken(member):
        raise OSError("SMTP down")

    monkeypatch.setattr(services, "send_password_changed_email", broken)
    response = change(client_for(member), STRONG_PASSWORD, NEW_PASSWORD)
    assert "Your password was changed, but the confirmation email could not be sent." in messages_of(response)
    member.refresh_from_db()
    assert member.check_password(NEW_PASSWORD)


def test_signed_out_goes_to_the_login() -> None:
    response = Client().get(URL)
    assert response.status_code == 302 and "/accounts/login/" in response["Location"]


# --- The sidebar name card ---------------------------------------------------------------------------------------


def test_the_name_card_opens_my_profile(member) -> None:
    html = client_for(member).get(reverse("home")).content.decode()
    card = re.search(r'<a href="([^"]+)" class="sidebar-glass[^"]*"[^>]*data-profile-card', html)
    assert card and card.group(1) == URL
    html = client_for(member).get(URL).content.decode()
    assert re.search(r'class="sidebar-glass[^"]*"[^>]*data-profile-card aria-current="page"', html)


# --- Positions in the Admin Console --------------------------------------------------------------------------------


def test_positions_are_added_renamed_and_deactivated(sysadmin, member, caplog) -> None:
    client = client_for(sysadmin)
    with caplog.at_level("INFO", logger="accounts.audit"):
        response = client.post(reverse("accounts:console_position_add"), {"name": "  Project   Officer "}, follow=True)
    assert "“Project Officer” was added." in messages_of(response)
    position = Position.objects.get()
    assert position.name == "Project Officer" and position.is_active
    assert "Position added" in caplog.text
    response = client.post(reverse("accounts:console_position_add"), {"name": "project officer"}, follow=True)
    assert "A position named “project officer” already exists." in messages_of(response)
    response = client.post(reverse("accounts:console_position_rename", args=[position.pk]), {"name": "Program Officer"},
                           follow=True)
    assert "“Project Officer” is now “Program Officer”." in messages_of(response)
    services.update_own_profile(member, name=member.name, position_id=position.pk)
    response = client.post(reverse("accounts:console_position_active", args=[position.pk]), {"active": "false"},
                           follow=True)
    assert "no longer offered. Members who have it keep it." in " ".join(messages_of(response))
    position.refresh_from_db()
    member.refresh_from_db()
    assert not position.is_active and member.position == position
    html = client.get(reverse("accounts:console_positions")).content.decode()
    assert "Program Officer" in html and "No longer offered" in html and "1 active member" in html
    members_html = client.get(reverse("accounts:console")).content.decode()
    assert re.search(r"data-position>Program Officer<", members_html)


def test_positions_need_can_manage_users(member, director) -> None:
    for who in (member, director):
        client = client_for(who)
        assert client.get(reverse("accounts:console_positions")).status_code == 403
        assert client.post(reverse("accounts:console_position_add"), {"name": "X"}).status_code == 403
    assert not Position.objects.exists()
    with pytest.raises(PermissionDenied):
        services.add_position(member, "X")
