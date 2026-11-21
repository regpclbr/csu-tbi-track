"""ROLE.can_manage_signatories (ASSUMPTIONS 418, NAVIGATU: only the TBI Director manages certificate signatories): the
guarded migration data step, seed_data (Director only, create-only), the Admin Console roles switch (toast, audit line,
amber warning), never together with can_manage_users, the Django admin, and who may switch it."""

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
DIRECTOR = Role.DIRECTOR
NO_MANAGER = "No active member can manage certificate signatories."
URL = "accounts:console_role_signatory_management"


def client_for(member) -> Client:
    client = Client()
    client.force_login(member)
    return client


@pytest.fixture
def sysadmin(make_member):
    return make_member(SYSADMIN)


def toggle(client: Client, role: Role, enabled: str):
    return client.post(reverse(URL, args=[role.pk]), {"enabled": enabled}, follow=True)


def messages_of(response) -> list[str]:
    return [str(message) for message in response.context["messages"]]


def managers() -> set[str]:
    return set(Role.objects.filter(can_manage_signatories=True).values_list("role_name", flat=True))


# --- The migration data step (accounts/0010) ------------------------------------------------------------------------


def test_migration_gives_it_to_the_director_only() -> None:
    migration = importlib.import_module("accounts.migrations.0010_signatory_managers_data")
    for name in (DIRECTOR, "Incubation Project Head", "Admin Staff"):
        Role.objects.create(role_name=name, can_create_activity=True, can_approve_certificates=True)
    Role.objects.create(role_name=SYSADMIN, can_manage_users=True, can_approve_certificates=True)
    migration.forwards(django_apps, None)
    assert managers() == {DIRECTOR}
    migration.backwards(django_apps, None)
    assert managers() == set()


def test_migration_does_nothing_without_a_director_role() -> None:
    migration = importlib.import_module("accounts.migrations.0010_signatory_managers_data")
    Role.objects.create(role_name="Directors", can_create_activity=True)  # a similar name is not the role
    migration.forwards(django_apps, None)
    assert managers() == set()


def test_migration_skips_a_director_role_that_manages_users(capsys) -> None:
    migration = importlib.import_module("accounts.migrations.0010_signatory_managers_data")
    Role.objects.create(role_name=DIRECTOR, can_manage_users=True)
    migration.forwards(django_apps, None)
    assert managers() == set() and "Skipped Director" in capsys.readouterr().out


# --- seed_data -------------------------------------------------------------------------------------------------------


def test_seed_gives_it_to_the_director_and_never_reapplies_it(roles) -> None:
    assert {name for name, role in roles.items() if role.can_manage_signatories} == {DIRECTOR}
    Role.objects.filter(role_name=DIRECTOR).update(can_manage_signatories=False)
    Role.objects.filter(role_name="Admin Staff").update(can_manage_signatories=True)
    load_lookups()
    assert managers() == {"Admin Staff"}  # the Admin Console's choices are kept


# --- The roles screen switch ------------------------------------------------------------------------------------------


def test_roles_screen_switches_it_logs_it_and_warns(sysadmin, make_member, roles, caplog) -> None:
    make_member(DIRECTOR)
    client = client_for(sysadmin)
    html = client.get(reverse("accounts:console_roles")).content.decode()
    assert "Manage signatories" in html and NO_MANAGER not in html
    assert f'aria-label="Turn off managing signatories for {DIRECTOR}"' in html
    with caplog.at_level("INFO", logger="accounts.audit"):
        response = toggle(client, roles[DIRECTOR], "false")  # no last-member guard, only the warning
    assert f"{DIRECTOR} can no longer manage certificate signatories." in messages_of(response)
    assert f"Role flag changed: role='{DIRECTOR}' can_manage_signatories True -> False" in caplog.text
    assert NO_MANAGER in client.get(reverse("accounts:console_roles")).content.decode()

    response = toggle(client, roles[VIEW_ONLY_ROLE], "true")
    assert f"{VIEW_ONLY_ROLE} can now manage certificate signatories." in messages_of(response)
    member = TBIMember.objects.select_related("role").get(pk=make_member(VIEW_ONLY_ROLE).pk)
    assert has_permission(member, Permission.MANAGE_SIGNATORIES)
    view_role = Role.objects.get(pk=roles[VIEW_ONLY_ROLE].pk)
    assert not (view_role.can_create_activity or view_role.can_approve_documents or view_role.can_approve_certificates)
    assert NO_MANAGER not in client.get(reverse("accounts:console_roles")).content.decode()


def test_the_warning_counts_only_active_members(sysadmin, make_member, roles) -> None:
    director = make_member(DIRECTOR)
    director.is_active = False
    director.save(update_fields=["is_active"])
    assert NO_MANAGER in client_for(sysadmin).get(reverse("accounts:console_roles")).content.decode()


# --- Never together with can_manage_users ---------------------------------------------------------------------------


def test_refused_together_with_managing_users(sysadmin, roles) -> None:
    role = roles[SYSADMIN]
    with pytest.raises(services.MemberManagementError, match="manage certificate signatories"):
        services.set_role_signatory_management(sysadmin, role, True)
    role.refresh_from_db()
    assert role.can_manage_users and not role.can_manage_signatories
    response = toggle(client_for(sysadmin), role, "true")
    assert services.SIGNATORIES_AND_MANAGE_REFUSED in messages_of(response)
    with pytest.raises(services.MemberManagementError):
        services.update_role_permissions(sysadmin, roles[DIRECTOR], can_create_activity=False, can_manage_users=True)
    assert Role.objects.get(role_name=DIRECTOR).can_manage_users is False  # the Director keeps the signatories


def test_the_other_separation_rules_are_unchanged(roles) -> None:
    services.check_role_flags(can_create_activity=False, can_manage_users=True, can_approve_certificates=True)
    services.check_role_flags(can_create_activity=True, can_manage_users=False, can_manage_signatories=True)


def test_other_flag_changes_keep_it(sysadmin, roles) -> None:
    services.set_role_certificate_approval(sysadmin, roles[DIRECTOR], False)
    services.set_role_document_approval(sysadmin, roles[DIRECTOR], False)
    assert managers() == {DIRECTOR}


# --- Who may switch it, the switch's input, the Django admin ---------------------------------------------------------


@pytest.mark.parametrize("role_name", [DIRECTOR, "Admin Staff", VIEW_ONLY_ROLE])
def test_only_user_managers_may_switch_it(make_member, roles, role_name) -> None:
    url = reverse(URL, args=[roles[VIEW_ONLY_ROLE].pk])
    assert client_for(make_member(role_name)).post(url, {"enabled": "true"}).status_code == 403
    assert Role.objects.get(pk=roles[VIEW_ONLY_ROLE].pk).can_manage_signatories is False


def test_switch_is_post_only_and_needs_true_or_false(sysadmin, roles) -> None:
    url = reverse(URL, args=[roles[DIRECTOR].pk])
    assert client_for(sysadmin).get(url).status_code == 405
    assert "enabled must be true or false." in messages_of(toggle(client_for(sysadmin), roles[DIRECTOR], "maybe"))


def test_django_admin_role_form_edits_it_and_refuses_it_with_managing_users(sysadmin, roles) -> None:
    client = client_for(sysadmin)
    role = roles[VIEW_ONLY_ROLE]
    url = reverse("admin:accounts_role_change", args=[role.pk])
    response = client.post(url, {"can_manage_signatories": "on"})
    assert response.status_code == 302 and Role.objects.get(pk=role.pk).can_manage_signatories is True
    admin_role = roles[SYSADMIN]
    page = client.post(reverse("admin:accounts_role_change", args=[admin_role.pk]),
                       {"can_manage_users": "on", "can_approve_certificates": "on", "can_delete_activities": "on",
                        "can_manage_signatories": "on"})
    assert page.status_code == 200 and services.SIGNATORIES_AND_MANAGE_REFUSED in page.content.decode()
    assert Role.objects.get(pk=admin_role.pk).can_manage_signatories is False


def test_roles_api_lists_it(sysadmin, roles) -> None:
    rows = client_for(sysadmin).get(reverse("accounts:api_roles")).json()["roles"]
    assert {row["role_name"] for row in rows if row["can_manage_signatories"]} == {DIRECTOR}
