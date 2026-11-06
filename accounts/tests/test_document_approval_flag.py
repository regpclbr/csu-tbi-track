"""ROLE.can_approve_documents (ASSUMPTIONS 344): the migration's data step, seed_data, the Admin Console roles switch,
separation of duties and the audit log."""

import importlib

import pytest
from django.apps import apps as django_apps
from django.test import Client
from django.urls import reverse

from accounts import services
from accounts.models import Role
from accounts.permissions import Permission, has_permission
from accounts.seed import load_lookups

pytestmark = pytest.mark.django_db

SYSADMIN = Role.SYSTEM_ADMINISTRATOR


def client_for(member) -> Client:
    client = Client()
    client.force_login(member)
    return client


@pytest.fixture
def sysadmin(make_member):
    return make_member(SYSADMIN)


def toggle(client: Client, role: Role, enabled: str):
    return client.post(reverse("accounts:console_role_approval", args=[role.pk]), {"enabled": enabled}, follow=True)


def messages_of(response) -> list[str]:
    return [str(message) for message in response.context["messages"]]


# --- Migration and seed ------------------------------------------------------------------------------------------


def _migration():
    return importlib.import_module("accounts.migrations.0004_role_can_approve_documents")


def test_migration_gives_the_flag_to_the_director_only() -> None:
    for name in ("Director", "Incubation Project Head", "Community Development Head", "Admin Staff"):
        Role.objects.create(role_name=name, can_create_activity=True)
    Role.objects.create(role_name=SYSADMIN, can_manage_users=True)
    Role.objects.filter(role_name="Admin Staff").update(can_approve_documents=True)  # a wrong value is reset too
    _migration().forwards(django_apps, None)
    flags = dict(Role.objects.values_list("role_name", "can_approve_documents"))
    assert flags == {
        "Director": True, "Incubation Project Head": False, "Community Development Head": False,
        "Admin Staff": False, SYSADMIN: False,
    }


def test_migration_without_a_director_role_sets_nobody() -> None:
    Role.objects.create(role_name="Admin Staff", can_approve_documents=True)
    _migration().forwards(django_apps, None)
    assert not Role.objects.filter(can_approve_documents=True).exists()


def test_seed_gives_the_flag_to_the_director_only(roles) -> None:
    assert {name for name, role in roles.items() if role.can_approve_documents} == {"Director"}


def test_rerunning_seed_keeps_the_admins_choice(roles) -> None:
    Role.objects.filter(role_name="Director").update(can_approve_documents=False)
    Role.objects.filter(role_name="Incubation Project Head").update(can_approve_documents=True)
    load_lookups()
    flags = dict(Role.objects.values_list("role_name", "can_approve_documents"))
    assert flags["Director"] is False and flags["Incubation Project Head"] is True


def test_permission_comes_from_the_flag(make_member, roles) -> None:
    director, head = make_member("Director"), make_member("Incubation Project Head")
    assert has_permission(director, Permission.APPROVE_DOCUMENTS)
    assert not has_permission(head, Permission.APPROVE_DOCUMENTS)


# --- Admin Console roles screen ---------------------------------------------------------------------------------


def test_roles_screen_shows_the_flag_and_a_switch(sysadmin, roles) -> None:
    html = client_for(sysadmin).get(reverse("accounts:console_roles")).content.decode()
    assert "Approve and return documents" in html
    for role in roles.values():
        assert reverse("accounts:console_role_approval", args=[role.pk]) in html
    assert "No active member can approve documents" in html  # roles exist, but no Director account yet


def test_turning_the_flag_on_and_off(sysadmin, make_member, roles, caplog) -> None:
    head_role = roles["Incubation Project Head"]
    head = make_member("Incubation Project Head")
    client = client_for(sysadmin)
    with caplog.at_level("INFO", logger="accounts.audit"):
        response = toggle(client, head_role, "true")
    assert "Incubation Project Head can now approve and return documents." in messages_of(response)
    head_role.refresh_from_db()
    assert head_role.can_approve_documents is True and head_role.can_create_activity is True  # other flags kept
    head.refresh_from_db()
    assert has_permission(head, Permission.APPROVE_DOCUMENTS)
    assert "Role flag changed: role='Incubation Project Head' can_approve_documents False -> True" in caplog.text

    response = toggle(client, head_role, "false")
    assert "Incubation Project Head can no longer approve or return documents." in messages_of(response)
    head_role.refresh_from_db()
    assert head_role.can_approve_documents is False


def test_turning_off_the_last_approving_role_is_allowed_with_a_warning(sysadmin, make_member, roles) -> None:
    make_member("Director")
    client = client_for(sysadmin)
    assert "No active member can approve documents" not in client.get(reverse("accounts:console_roles")).content.decode()
    response = toggle(client, roles["Director"], "false")
    assert "No active member can approve documents" in response.content.decode()
    roles["Director"].refresh_from_db()
    assert roles["Director"].can_approve_documents is False


def test_system_administrator_role_cannot_get_the_flag(sysadmin, roles) -> None:
    response = toggle(client_for(sysadmin), roles[SYSADMIN], "true")
    assert services.APPROVE_AND_MANAGE_REFUSED in messages_of(response)
    roles[SYSADMIN].refresh_from_db()
    assert roles[SYSADMIN].can_approve_documents is False


def test_a_bad_value_is_refused(sysadmin, roles) -> None:
    response = toggle(client_for(sysadmin), roles["Admin Staff"], "yes")
    assert "enabled must be true or false." in messages_of(response)
    roles["Admin Staff"].refresh_from_db()
    assert roles["Admin Staff"].can_approve_documents is False


@pytest.mark.parametrize("role_name", ["Director", "Incubation Project Head", "Admin Staff"])
def test_only_user_managers_may_switch_it(make_member, roles, role_name) -> None:
    response = client_for(make_member(role_name)).post(
        reverse("accounts:console_role_approval", args=[roles["Admin Staff"].pk]), {"enabled": "true"}
    )
    assert response.status_code == 403
    roles["Admin Staff"].refresh_from_db()
    assert roles["Admin Staff"].can_approve_documents is False


def test_switch_is_post_only(sysadmin, roles) -> None:
    url = reverse("accounts:console_role_approval", args=[roles["Director"].pk])
    assert client_for(sysadmin).get(url).status_code == 405


# --- Separation of duties and the Django admin -------------------------------------------------------------------


def test_manage_users_and_approve_documents_never_combine(sysadmin, roles) -> None:
    with pytest.raises(services.MemberManagementError, match="approve documents"):
        services.update_role_permissions(
            sysadmin, roles["Director"], can_create_activity=False, can_manage_users=True, can_approve_documents=True
        )
    with pytest.raises(services.MemberManagementError, match="approve documents"):
        services.set_role_document_approval(sysadmin, roles[SYSADMIN], True)
    assert not any(role.can_manage_users and role.can_approve_documents for role in Role.objects.all())


def test_update_role_permissions_keeps_the_flag_when_not_given(sysadmin, roles) -> None:
    services.update_role_permissions(sysadmin, roles["Director"], can_create_activity=True, can_manage_users=False)
    roles["Director"].refresh_from_db()
    assert roles["Director"].can_approve_documents is True


def test_django_admin_role_form_edits_the_flag(sysadmin, roles) -> None:
    role = roles["Community Development Head"]
    client = client_for(sysadmin)
    response = client.post(
        f"/admin/accounts/role/{role.pk}/change/", {"can_create_activity": "on", "can_approve_documents": "on"}
    )
    assert response.status_code == 302
    role.refresh_from_db()
    assert role.can_approve_documents is True and role.can_create_activity is True

    sysadmin_role = roles[SYSADMIN]
    response = client.post(
        f"/admin/accounts/role/{sysadmin_role.pk}/change/", {"can_manage_users": "on", "can_approve_documents": "on"}
    )
    assert response.status_code == 200 and services.APPROVE_AND_MANAGE_REFUSED in response.content.decode()


def test_roles_api_returns_the_flag(sysadmin, roles) -> None:
    data = client_for(sysadmin).get(reverse("accounts:api_roles")).json()["roles"]
    assert {row["role_name"]: row["can_approve_documents"] for row in data}["Director"] is True
