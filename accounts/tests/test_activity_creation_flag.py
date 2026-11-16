"""ROLE.can_create_activity on the Admin Console roles screen (ASSUMPTIONS 388): the switch, its toast and audit log line,
separation of duties (never with can_manage_users), the "no active member can create activities" warning, seed_data
(create-only) and who may use it."""

import pytest
from django.test import Client
from django.urls import reverse

from accounts import services
from accounts.models import Role
from accounts.permissions import Permission, has_permission
from accounts.seed import load_lookups
from conftest import VIEW_ONLY_ROLE

pytestmark = pytest.mark.django_db

SYSADMIN = Role.SYSTEM_ADMINISTRATOR
CREATORS = {"Director", "Incubation Project Head", "Community Development Head", "Admin Staff"}
NO_CREATOR = "No active member can create or edit activities."


def client_for(member) -> Client:
    client = Client()
    client.force_login(member)
    return client


@pytest.fixture
def sysadmin(make_member):
    return make_member(SYSADMIN)


def toggle(client: Client, role: Role, enabled: str):
    return client.post(reverse("accounts:console_role_activity_creation", args=[role.pk]), {"enabled": enabled},
                       follow=True)


def messages_of(response) -> list[str]:
    return [str(message) for message in response.context["messages"]]


def test_seed_gives_it_to_four_roles_and_never_reapplies_it(roles) -> None:
    assert {name for name, role in roles.items() if role.can_create_activity} == CREATORS
    Role.objects.filter(role_name="Admin Staff").update(can_create_activity=False)  # e.g. switched off in the console
    load_lookups()
    assert Role.objects.get(role_name="Admin Staff").can_create_activity is False
    assert Role.objects.get(role_name=SYSADMIN).can_manage_users is True  # still re-applied on every run


def test_roles_screen_switches_it_and_logs_it(sysadmin, make_member, roles, caplog) -> None:
    client = client_for(sysadmin)
    view_role = roles[VIEW_ONLY_ROLE]
    html = client.get(reverse("accounts:console_roles")).content.decode()
    assert reverse("accounts:console_role_activity_creation", args=[view_role.pk]) in html
    assert f'aria-label="Turn on creating and editing activities for {VIEW_ONLY_ROLE}"' in html
    assert 'aria-label="Turn off creating and editing activities for Admin Staff"' in html

    member = make_member(VIEW_ONLY_ROLE)
    assert not has_permission(member, Permission.CREATE_ACTIVITY)
    with caplog.at_level("INFO", logger="accounts.audit"):
        response = toggle(client, view_role, "true")
    assert f"{VIEW_ONLY_ROLE} can now create and edit activities." in messages_of(response)
    assert f"Role flag changed: role='{VIEW_ONLY_ROLE}' can_create_activity False -> True" in caplog.text
    view_role.refresh_from_db()
    assert view_role.can_create_activity and not view_role.can_approve_documents and not view_role.can_approve_certificates
    member.refresh_from_db()
    assert has_permission(member, Permission.CREATE_ACTIVITY)

    response = toggle(client, view_role, "false")
    assert f"{VIEW_ONLY_ROLE} can no longer create or edit activities." in messages_of(response)
    view_role.refresh_from_db()
    assert view_role.can_create_activity is False


def test_other_flags_stay_when_it_changes(sysadmin, roles) -> None:
    services.set_role_activity_creation(sysadmin, roles["Director"], False)
    director = Role.objects.get(role_name="Director")
    assert (director.can_create_activity, director.can_approve_documents, director.can_approve_certificates) == (
        False, True, True)


def test_never_together_with_managing_users(sysadmin, roles) -> None:
    response = toggle(client_for(sysadmin), roles[SYSADMIN], "true")
    assert services.BOTH_FLAGS_REFUSED in messages_of(response)
    roles[SYSADMIN].refresh_from_db()
    assert roles[SYSADMIN].can_create_activity is False and roles[SYSADMIN].can_manage_users is True
    # The other separation rule is unchanged: managing users never goes with approving documents.
    with pytest.raises(services.MemberManagementError, match="approve documents"):
        services.set_role_document_approval(sysadmin, roles[SYSADMIN], True)


def test_warning_when_no_active_member_can_create_activities(sysadmin, make_member, roles) -> None:
    client = client_for(sysadmin)
    make_member("Admin Staff")
    assert NO_CREATOR not in client.get(reverse("accounts:console_roles")).content.decode()
    toggle(client, roles["Admin Staff"], "false")  # no last-member guard, only the warning
    assert NO_CREATOR in client.get(reverse("accounts:console_roles")).content.decode()
    toggle(client, roles["Director"], "false")  # a role without active members never counted
    toggle(client, roles["Admin Staff"], "true")
    assert NO_CREATOR not in client.get(reverse("accounts:console_roles")).content.decode()


@pytest.mark.parametrize("role_name", ["Director", "Admin Staff", VIEW_ONLY_ROLE])
def test_only_user_managers_may_switch_it(make_member, roles, role_name) -> None:
    url = reverse("accounts:console_role_activity_creation", args=[roles[VIEW_ONLY_ROLE].pk])
    assert client_for(make_member(role_name)).post(url, {"enabled": "true"}).status_code == 403
    roles[VIEW_ONLY_ROLE].refresh_from_db()
    assert roles[VIEW_ONLY_ROLE].can_create_activity is False


def test_switch_is_post_only_and_needs_true_or_false(sysadmin, roles) -> None:
    url = reverse("accounts:console_role_activity_creation", args=[roles["Director"].pk])
    assert client_for(sysadmin).get(url).status_code == 405
    assert "enabled must be true or false." in messages_of(toggle(client_for(sysadmin), roles["Director"], "maybe"))
    roles["Director"].refresh_from_db()
    assert roles["Director"].can_create_activity is True
