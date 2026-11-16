"""ROLE.can_delete_activities (ASSUMPTIONS 389): migration data step, seed_data (create-only), the Admin Console roles
switch (toast, audit line, warning), allowed together with can_manage_users, the Django admin, and who may switch it."""

import importlib

import pytest
from django.apps import apps as django_apps
from django.test import Client
from django.urls import reverse

from accounts import services
from accounts.models import Role, TBIMember
from accounts.permissions import Permission, has_permission
from accounts.seed import load_lookups
from conftest import VIEW_ONLY_ROLE

pytestmark = pytest.mark.django_db

SYSADMIN = Role.SYSTEM_ADMINISTRATOR
NO_DELETER = "No active member can delete activities."


def client_for(member) -> Client:
    client = Client()
    client.force_login(member)
    return client


@pytest.fixture
def sysadmin(make_member):
    return make_member(SYSADMIN)


def toggle(client: Client, role: Role, enabled: str):
    return client.post(reverse("accounts:console_role_activity_deletion", args=[role.pk]), {"enabled": enabled},
                       follow=True)


def messages_of(response) -> list[str]:
    return [str(message) for message in response.context["messages"]]


def test_migration_gives_it_to_the_system_administrator_only() -> None:
    migration = importlib.import_module("accounts.migrations.0008_delete_activities_flag_data")
    for name in ("Director", "Admin Staff"):
        Role.objects.create(role_name=name, can_create_activity=True)
    Role.objects.create(role_name=SYSADMIN, can_manage_users=True)
    migration.forwards(django_apps, None)
    assert {name for name, flag in Role.objects.values_list("role_name", "can_delete_activities") if flag} == {SYSADMIN}
    migration.backwards(django_apps, None)
    assert not Role.objects.filter(can_delete_activities=True).exists()


def test_seed_gives_it_to_the_system_administrator_and_never_reapplies_it(roles) -> None:
    assert {name for name, role in roles.items() if role.can_delete_activities} == {SYSADMIN}
    Role.objects.filter(role_name=SYSADMIN).update(can_delete_activities=False)
    Role.objects.filter(role_name="Director").update(can_delete_activities=True)
    load_lookups()
    flags = dict(Role.objects.values_list("role_name", "can_delete_activities"))
    assert flags[SYSADMIN] is False and flags["Director"] is True


def test_roles_screen_switches_it_logs_it_and_warns(sysadmin, make_member, roles, caplog) -> None:
    client = client_for(sysadmin)
    html = client.get(reverse("accounts:console_roles")).content.decode()
    assert "Delete activities" in html and NO_DELETER not in html
    assert f'aria-label="Turn off deleting activities for {SYSADMIN}"' in html
    response = toggle(client, roles[SYSADMIN], "false")  # no last-member guard, only the warning
    assert f"{SYSADMIN} can no longer delete activities." in messages_of(response)
    assert NO_DELETER in client.get(reverse("accounts:console_roles")).content.decode()

    view_role = roles[VIEW_ONLY_ROLE]
    with caplog.at_level("INFO", logger="accounts.audit"):
        response = toggle(client, view_role, "true")
    assert f"{VIEW_ONLY_ROLE} can now delete activities." in messages_of(response)
    assert f"Role flag changed: role='{VIEW_ONLY_ROLE}' can_delete_activities False -> True" in caplog.text
    member = TBIMember.objects.select_related("role").get(pk=make_member(VIEW_ONLY_ROLE).pk)  # the role as saved now
    assert has_permission(member, Permission.DELETE_ACTIVITIES)
    view_role.refresh_from_db()
    assert not (view_role.can_create_activity or view_role.can_approve_documents or view_role.can_approve_certificates)
    assert NO_DELETER not in client.get(reverse("accounts:console_roles")).content.decode()


def test_it_may_go_with_managing_users(sysadmin, roles) -> None:
    """The System Administrator cleans up mistakes and test activities; the other separation rules are unchanged."""
    role = roles[SYSADMIN]
    assert role.can_manage_users and role.can_delete_activities
    services.set_role_activity_deletion(sysadmin, role, False)
    services.set_role_activity_deletion(sysadmin, role, True)
    role.refresh_from_db()
    assert role.can_manage_users and role.can_delete_activities
    with pytest.raises(services.MemberManagementError, match="create activities"):
        services.set_role_activity_creation(sysadmin, role, True)
    with pytest.raises(services.MemberManagementError, match="approve documents"):
        services.set_role_document_approval(sysadmin, role, True)


def test_other_flag_changes_keep_it(sysadmin, roles) -> None:
    services.set_role_certificate_approval(sysadmin, roles[SYSADMIN], False)
    services.update_role_permissions(sysadmin, roles["Director"], can_create_activity=True, can_manage_users=False)
    assert Role.objects.get(role_name=SYSADMIN).can_delete_activities is True
    assert Role.objects.get(role_name="Director").can_delete_activities is False


@pytest.mark.parametrize("role_name", ["Director", "Admin Staff", VIEW_ONLY_ROLE])
def test_only_user_managers_may_switch_it(make_member, roles, role_name) -> None:
    url = reverse("accounts:console_role_activity_deletion", args=[roles[VIEW_ONLY_ROLE].pk])
    assert client_for(make_member(role_name)).post(url, {"enabled": "true"}).status_code == 403
    roles[VIEW_ONLY_ROLE].refresh_from_db()
    assert roles[VIEW_ONLY_ROLE].can_delete_activities is False


def test_switch_is_post_only_and_needs_true_or_false(sysadmin, roles) -> None:
    url = reverse("accounts:console_role_activity_deletion", args=[roles["Director"].pk])
    assert client_for(sysadmin).get(url).status_code == 405
    assert "enabled must be true or false." in messages_of(toggle(client_for(sysadmin), roles["Director"], "maybe"))


def test_django_admin_role_form_edits_it(sysadmin, roles) -> None:
    role = roles[VIEW_ONLY_ROLE]
    response = client_for(sysadmin).post(f"/admin/accounts/role/{role.pk}/change/", {"can_delete_activities": "on"})
    assert response.status_code == 302
    role.refresh_from_db()
    assert role.can_delete_activities is True and not role.can_create_activity
