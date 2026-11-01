"""Admin Console user management: create, change role, deactivate, reactivate, guardrails."""

import json
import logging
import re

import pytest
from django.core import mail
from django.core.exceptions import PermissionDenied

from accounts import services
from accounts.admin import TBIMemberAdmin
from accounts.models import TBIMember
from conftest import STRONG_PASSWORD


def temporary_password_from_email() -> str:
    return re.search(r"Temporary password: (\S+)", mail.outbox[-1].body).group(1)


# --- Create member ------------------------------------------------------------------


@pytest.mark.django_db
def test_create_member_with_temporary_password(admin_member, roles) -> None:
    member = services.create_member(admin_member, name="Ana Cruz", email="Ana@Example.com", role=roles["Director"])

    assert member.email == "ana@example.com"
    assert member.must_change_password is True
    assert member.is_active is True
    assert len(mail.outbox) == 1 and mail.outbox[0].to == ["ana@example.com"]
    temporary = temporary_password_from_email()
    assert member.check_password(temporary)
    assert temporary not in member.password  # stored only as a hash


@pytest.mark.django_db
def test_temporary_password_never_in_logs(admin_member, roles, caplog) -> None:
    with caplog.at_level(logging.DEBUG):  # capture every logger, every level
        services.create_member(admin_member, name="Ana Cruz", email="ana@example.com", role=roles["Director"])
    temporary = temporary_password_from_email()
    assert "Member created" in caplog.text  # the action is logged...
    assert temporary not in caplog.text  # ...but never the password


@pytest.mark.django_db
def test_create_member_duplicate_email(admin_member, roles) -> None:
    with pytest.raises(services.MemberManagementError, match="already exists"):
        services.create_member(admin_member, name="Again", email="SYSTEM.Administrator@example.com", role=roles["Director"])


@pytest.mark.django_db
@pytest.mark.parametrize("role_name", ["Director", "Incubation Project Head", "Community Development Head", "Admin Staff"])
def test_only_admin_can_manage_members(make_member, roles, admin_member, role_name) -> None:
    acting = make_member(role_name)
    with pytest.raises(PermissionDenied):
        services.create_member(acting, name="X", email="x@example.com", role=roles["Director"])
    with pytest.raises(PermissionDenied):
        services.change_member_role(acting, admin_member, roles["Director"])
    with pytest.raises(PermissionDenied):
        services.deactivate_member(acting, admin_member)
    with pytest.raises(PermissionDenied):
        services.reactivate_member(acting, admin_member)


# --- Change role ------------------------------------------------------------------------


@pytest.mark.django_db
def test_change_member_role(admin_member, make_member, roles) -> None:
    member = make_member("Admin Staff")
    services.change_member_role(admin_member, member, roles["Director"])
    member.refresh_from_db()
    assert member.role.role_name == "Director"


@pytest.mark.django_db
def test_last_active_admin_cannot_lose_admin_role(admin_member, roles) -> None:
    with pytest.raises(services.MemberManagementError, match="At least one active System Administrator"):
        services.change_member_role(admin_member, admin_member, roles["Director"])
    admin_member.refresh_from_db()
    assert admin_member.role.role_name == "System Administrator"


@pytest.mark.django_db
def test_admin_role_can_move_when_another_admin_exists(admin_member, make_member, roles) -> None:
    other_admin = make_member("System Administrator", email="admin2@example.com")
    services.change_member_role(admin_member, other_admin, roles["Director"])
    assert other_admin.role.role_name == "Director"


# --- Deactivate and reactivate --------------------------------------------------------


@pytest.mark.django_db
def test_admin_cannot_deactivate_own_account(admin_member, make_member) -> None:
    make_member("System Administrator", email="admin2@example.com")  # even with another Admin around
    with pytest.raises(services.MemberManagementError, match="your own account"):
        services.deactivate_member(admin_member, admin_member)
    admin_member.refresh_from_db()
    assert admin_member.is_active is True


@pytest.mark.django_db
def test_deactivate_and_reactivate(admin_member, make_member) -> None:
    member = make_member("Director")
    services.deactivate_member(admin_member, member)
    member.refresh_from_db()
    assert member.is_active is False

    services.reactivate_member(admin_member, member)
    member.refresh_from_db()
    assert member.is_active is True


@pytest.mark.django_db
def test_deactivated_admin_does_not_count_as_active_admin(admin_member, make_member, roles) -> None:
    other_admin = make_member("System Administrator", email="admin2@example.com")
    services.deactivate_member(admin_member, other_admin)
    # Now admin_member is the only active Admin again.
    with pytest.raises(services.MemberManagementError, match="At least one active System Administrator"):
        services.change_member_role(admin_member, admin_member, roles["Director"])


@pytest.mark.django_db
def test_last_active_admin_guard(admin_member, make_member) -> None:
    """The single check behind both guardrail paths (role change and deactivation)."""
    with pytest.raises(services.MemberManagementError):
        services.ensure_another_admin_remains(admin_member)
    services.ensure_another_admin_remains(make_member("Director"))  # not an Admin: fine
    make_member("System Administrator", email="admin2@example.com")
    services.ensure_another_admin_remains(admin_member)  # another Admin exists: fine


# --- Never delete -----------------------------------------------------------------------


@pytest.mark.django_db
def test_members_cannot_be_deleted_in_django_admin(admin_member, rf) -> None:
    from django.contrib.admin.sites import site

    request = rf.get("/admin/accounts/tbimember/")
    request.user = admin_member
    model_admin: TBIMemberAdmin = site._registry[TBIMember]
    assert model_admin.has_delete_permission(request, admin_member) is False
    assert "delete_selected" not in model_admin.get_actions(request)


# --- JSON API ----------------------------------------------------------------------------


def post_json(client, url: str, data: dict | None = None):
    return client.post(url, json.dumps(data or {}), content_type="application/json")


@pytest.mark.django_db
def test_api_create_member_then_first_sign_in(client, admin_member, roles) -> None:
    client.force_login(admin_member)
    response = post_json(
        client, "/accounts/api/members/", {"name": "Ana Cruz", "email": "ana@example.com", "role_id": roles["Director"].pk}
    )
    assert response.status_code == 201
    body = json.loads(response.content)["member"]
    assert body["must_change_password"] is True
    assert "password" not in body  # the API never returns a password or hash
    assert temporary_password_from_email() not in response.content.decode()

    # The new member logs in with the emailed temporary password and must change it.
    client.post("/accounts/logout/")
    response = client.post("/accounts/login/", {"email": "ana@example.com", "password": temporary_password_from_email()})
    assert response.url == "/accounts/password/change/"


@pytest.mark.django_db
def test_api_guardrails_return_400(client, admin_member, roles) -> None:
    client.force_login(admin_member)
    response = post_json(client, f"/accounts/api/members/{admin_member.pk}/deactivate/")
    assert response.status_code == 400
    assert json.loads(response.content) == {"error": "You cannot deactivate your own account."}

    response = post_json(client, f"/accounts/api/members/{admin_member.pk}/role/", {"role_id": roles["Director"].pk})
    assert response.status_code == 400
    assert "At least one active System Administrator" in json.loads(response.content)["error"]


@pytest.mark.django_db
def test_api_change_role_deactivate_reactivate(client, admin_member, make_member, roles) -> None:
    member = make_member("Admin Staff")
    client.force_login(admin_member)

    assert post_json(client, f"/accounts/api/members/{member.pk}/role/", {"role_id": roles["Director"].pk}).status_code == 200
    assert post_json(client, f"/accounts/api/members/{member.pk}/deactivate/").status_code == 200
    member.refresh_from_db()
    assert (member.role.role_name, member.is_active) == ("Director", False)

    assert post_json(client, f"/accounts/api/members/{member.pk}/reactivate/").status_code == 200
    member.refresh_from_db()
    assert member.is_active is True


@pytest.mark.django_db
def test_api_invalid_input(client, admin_member) -> None:
    client.force_login(admin_member)
    assert post_json(client, "/accounts/api/members/", {"name": "No email"}).status_code == 400
    assert post_json(client, "/accounts/api/members/", {"name": "A", "email": "a@x.com", "role_id": 9999}).status_code == 400
    response = client.post("/accounts/api/members/", "not json", content_type="application/json")
    assert response.status_code == 400


# --- Django admin uses the same rules -------------------------------------------------


@pytest.mark.django_db
def test_django_admin_add_member_sends_invitation(client, admin_member, roles) -> None:
    client.force_login(admin_member)
    response = client.post(
        "/admin/accounts/tbimember/add/", {"email": "Ben@Example.com", "name": "Ben", "role": roles["Admin Staff"].pk}
    )
    assert response.status_code == 302
    member = TBIMember.objects.get(email="ben@example.com")
    assert member.must_change_password is True
    assert member.check_password(temporary_password_from_email())


@pytest.mark.django_db
def test_django_admin_cannot_demote_last_admin(client, admin_member, roles) -> None:
    client.force_login(admin_member)
    response = client.post(
        f"/admin/accounts/tbimember/{admin_member.pk}/change/",
        {"email": admin_member.email, "name": admin_member.name, "role": roles["Director"].pk},
    )
    assert response.status_code == 200  # form shown again with the error
    assert "At least one active System Administrator" in response.content.decode()
    admin_member.refresh_from_db()
    assert admin_member.role.role_name == "System Administrator"


@pytest.mark.django_db
def test_django_admin_action_cannot_deactivate_self(client, admin_member) -> None:
    client.force_login(admin_member)
    client.post(
        "/admin/accounts/tbimember/",
        {"action": "deactivate_selected", "_selected_action": [admin_member.pk]},
    )
    admin_member.refresh_from_db()
    assert admin_member.is_active is True


@pytest.mark.django_db
def test_strong_password_constant_is_valid() -> None:
    from django.contrib.auth.password_validation import validate_password

    validate_password(STRONG_PASSWORD)
