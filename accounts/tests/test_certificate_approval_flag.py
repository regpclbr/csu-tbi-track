"""ROLE.can_approve_certificates (ASSUMPTIONS 357, 361): the migrations' data steps, seed_data, the Admin Console roles
switch, separation of duties (allowed with can_manage_users), the Django admin and the audit log."""

import importlib

import pytest
from django.apps import apps as django_apps
from django.test import Client
from django.urls import reverse

from accounts import services
from accounts.models import Role
from accounts.permissions import Permission, has_permission
from accounts.seed import load_lookups
from conftest import VIEW_ONLY_ROLE

pytestmark = pytest.mark.django_db

SYSADMIN = Role.SYSTEM_ADMINISTRATOR
APPROVERS = {"Director", "Incubation Project Head", "Community Development Head", SYSADMIN}  # migration 0006
SEEDED_APPROVERS = APPROVERS | {"Admin Staff"}  # seed_data since ASSUMPTIONS 388


def client_for(member) -> Client:
    client = Client()
    client.force_login(member)
    return client


@pytest.fixture
def sysadmin(make_member):
    return make_member(SYSADMIN)


def toggle(client: Client, role: Role, enabled: str):
    return client.post(reverse("accounts:console_role_certificate_approval", args=[role.pk]), {"enabled": enabled}, follow=True)


def messages_of(response) -> list[str]:
    return [str(message) for message in response.context["messages"]]


def test_migration_gives_the_flag_to_the_director_only() -> None:
    migration = importlib.import_module("accounts.migrations.0005_role_can_approve_certificates")
    for name in ("Director", "Incubation Project Head", "Community Development Head", "Admin Staff"):
        Role.objects.create(role_name=name, can_create_activity=True)
    Role.objects.create(role_name=SYSADMIN, can_manage_users=True)
    Role.objects.filter(role_name="Admin Staff").update(can_approve_certificates=True)  # a wrong value is reset too
    migration.forwards(django_apps, None)
    assert dict(Role.objects.values_list("role_name", "can_approve_certificates")) == {
        "Director": True, "Incubation Project Head": False, "Community Development Head": False,
        "Admin Staff": False, SYSADMIN: False,
    }


def test_migration_0006_gives_it_to_the_four_approver_roles() -> None:
    migration = importlib.import_module("accounts.migrations.0006_certificate_approvers")
    for name in ("Director", "Incubation Project Head", "Community Development Head", "Admin Staff"):
        Role.objects.create(role_name=name, can_create_activity=True)
    Role.objects.create(role_name=SYSADMIN, can_manage_users=True)
    Role.objects.filter(role_name="Admin Staff").update(can_approve_certificates=True)  # a wrong value is reset too
    migration.forwards(django_apps, None)
    assert {name for name, flag in Role.objects.values_list("role_name", "can_approve_certificates") if flag} == APPROVERS
    migration.backwards(django_apps, None)
    assert {name for name, flag in Role.objects.values_list("role_name", "can_approve_certificates") if flag} == {"Director"}


def test_seed_gives_it_to_the_approver_roles_and_never_reapplies_it(roles) -> None:
    assert {name for name, role in roles.items() if role.can_approve_certificates} == SEEDED_APPROVERS
    Role.objects.filter(role_name__in=["Director", "Admin Staff"]).update(can_approve_certificates=False)
    load_lookups()
    flags = dict(Role.objects.values_list("role_name", "can_approve_certificates"))
    assert flags["Director"] is False and flags["Admin Staff"] is False


def test_permission_comes_from_the_flag(make_member) -> None:
    for name in SEEDED_APPROVERS:
        assert has_permission(make_member(name), Permission.APPROVE_CERTIFICATES)
    assert not has_permission(make_member(VIEW_ONLY_ROLE), Permission.APPROVE_CERTIFICATES)


def test_roles_screen_switches_it_and_logs_it(sysadmin, make_member, roles, caplog) -> None:
    client = client_for(sysadmin)
    html = client.get(reverse("accounts:console_roles")).content.decode()
    assert "Approve certificates" in html and "No active member can approve certificates" not in html  # the sysadmin
    response = toggle(client, roles[SYSADMIN], "false")  # no last-approver guard, only the warning
    assert f"{SYSADMIN} can no longer approve certificates." in messages_of(response)
    assert "No active member can approve certificates" in client.get(reverse("accounts:console_roles")).content.decode()

    view_role = roles[VIEW_ONLY_ROLE]
    with caplog.at_level("INFO", logger="accounts.audit"):
        response = toggle(client, view_role, "true")
    assert f"{VIEW_ONLY_ROLE} can now approve certificates." in messages_of(response)
    assert f"Role flag changed: role='{VIEW_ONLY_ROLE}' can_approve_certificates False -> True" in caplog.text
    view_role.refresh_from_db()
    assert view_role.can_approve_certificates and not view_role.can_create_activity and not view_role.can_approve_documents
    make_member(VIEW_ONLY_ROLE)
    assert "No active member can approve certificates" not in client.get(reverse("accounts:console_roles")).content.decode()


def test_it_may_go_with_managing_users(sysadmin, roles) -> None:
    """Separation of duties no longer covers this flag (ASSUMPTIONS 361); it still covers the other two."""
    client = client_for(sysadmin)
    toggle(client, roles[SYSADMIN], "false")
    response = toggle(client, roles[SYSADMIN], "true")
    assert f"{SYSADMIN} can now approve certificates." in messages_of(response)
    roles[SYSADMIN].refresh_from_db()
    assert roles[SYSADMIN].can_manage_users and roles[SYSADMIN].can_approve_certificates
    with pytest.raises(services.MemberManagementError, match="create activities"):
        services.update_role_permissions(sysadmin, roles[SYSADMIN], can_create_activity=True, can_manage_users=True)
    with pytest.raises(services.MemberManagementError, match="approve documents"):
        services.set_role_document_approval(sysadmin, roles[SYSADMIN], True)


@pytest.mark.parametrize("role_name", ["Director", "Incubation Project Head", "Admin Staff", VIEW_ONLY_ROLE])
def test_only_user_managers_may_switch_it(make_member, roles, role_name) -> None:
    url = reverse("accounts:console_role_certificate_approval", args=[roles[VIEW_ONLY_ROLE].pk])
    assert client_for(make_member(role_name)).post(url, {"enabled": "true"}).status_code == 403
    roles[VIEW_ONLY_ROLE].refresh_from_db()
    assert roles[VIEW_ONLY_ROLE].can_approve_certificates is False


def test_switch_is_post_only_and_needs_true_or_false(sysadmin, roles) -> None:
    url = reverse("accounts:console_role_certificate_approval", args=[roles["Director"].pk])
    assert client_for(sysadmin).get(url).status_code == 405
    assert "enabled must be true or false." in messages_of(toggle(client_for(sysadmin), roles["Admin Staff"], "yes"))


def test_other_flag_changes_keep_it(sysadmin, roles) -> None:
    services.set_role_document_approval(sysadmin, roles["Director"], False)
    services.update_role_permissions(sysadmin, roles["Director"], can_create_activity=True, can_manage_users=False)
    roles["Director"].refresh_from_db()
    assert roles["Director"].can_approve_certificates is True


def test_django_admin_role_form_edits_it(sysadmin, roles) -> None:
    role = roles["Community Development Head"]
    response = client_for(sysadmin).post(
        f"/admin/accounts/role/{role.pk}/change/", {"can_create_activity": "on", "can_approve_certificates": "on"}
    )
    assert response.status_code == 302
    role.refresh_from_db()
    assert role.can_approve_certificates is True
    sysadmin_role = roles[SYSADMIN]
    response = client_for(sysadmin).post(
        f"/admin/accounts/role/{sysadmin_role.pk}/change/", {"can_manage_users": "on", "can_approve_certificates": "on"}
    )
    assert response.status_code == 302  # allowed together (ASSUMPTIONS 361)
    response = client_for(sysadmin).post(
        f"/admin/accounts/role/{sysadmin_role.pk}/change/", {"can_manage_users": "on", "can_approve_documents": "on"}
    )
    assert response.status_code == 200 and services.APPROVE_AND_MANAGE_REFUSED in response.content.decode()


def test_roles_api_returns_it(sysadmin, roles) -> None:
    data = client_for(sysadmin).get(reverse("accounts:api_roles")).json()["roles"]
    assert {row["role_name"]: row["can_approve_certificates"] for row in data}["Director"] is True
