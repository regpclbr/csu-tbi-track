"""Role permissions: can_create_activity and can_manage_users, for every role."""

import json

import pytest
from django.contrib.auth.models import AnonymousUser
from django.core.exceptions import PermissionDenied
from django.http import HttpResponse
from django.test import RequestFactory

from accounts.permissions import Permission, has_permission, permission_required, require_permission
from config.views import permission_denied
from startups.models import Startup
from conftest import VIEW_ONLY_ROLE

# role: (can_create_activity, can_manage_users), as seeded in Step 2
ROLE_PERMISSIONS = {
    "Director": (True, False),
    "Incubation Project Head": (True, False),
    "Community Development Head": (True, False),
    "Admin Staff": (True, False),  # creates and edits activities since ASSUMPTIONS 388
    VIEW_ONLY_ROLE: (False, False),  # test-only role without flags
    "System Administrator": (False, True),  # separation of duties (ASSUMPTIONS 85, 339)
}


@pytest.mark.django_db
@pytest.mark.parametrize("role_name, expected", ROLE_PERMISSIONS.items())
def test_has_permission_for_each_role(make_member, role_name, expected) -> None:
    member = make_member(role_name)
    assert has_permission(member, Permission.CREATE_ACTIVITY) is expected[0]
    assert has_permission(member, Permission.MANAGE_USERS) is expected[1]


@pytest.mark.django_db
def test_inactive_anonymous_and_startup_have_no_permissions(make_member) -> None:
    inactive_admin = make_member("System Administrator", is_active=False)
    startup = Startup(name="Acme", email="acme@example.com")
    for user in (inactive_admin, AnonymousUser(), startup, None):
        assert not has_permission(user, Permission.CREATE_ACTIVITY)
        assert not has_permission(user, Permission.MANAGE_USERS)


@pytest.mark.django_db
def test_require_permission_raises_with_a_clear_message(make_member) -> None:
    with pytest.raises(PermissionDenied, match="create or edit activities"):
        require_permission(make_member(VIEW_ONLY_ROLE), Permission.CREATE_ACTIVITY)


@pytest.mark.django_db
@pytest.mark.parametrize("role_name, expected", ROLE_PERMISSIONS.items())
def test_create_activity_decorator_for_each_role(make_member, role_name, expected) -> None:
    """The decorator that activity views (Step 4) will use."""

    @permission_required(Permission.CREATE_ACTIVITY)
    def create_activity_view(request):
        return HttpResponse("ok")

    request = RequestFactory().post("/activities/create/")
    request.user = make_member(role_name)
    if expected[0]:
        assert create_activity_view(request).status_code == 200
    else:
        with pytest.raises(PermissionDenied):
            create_activity_view(request)


@pytest.mark.django_db
@pytest.mark.parametrize("role_name, expected", ROLE_PERMISSIONS.items())
def test_admin_console_api_for_each_role(client, make_member, role_name, expected) -> None:
    client.force_login(make_member(role_name))
    response = client.get("/accounts/api/members/")
    if expected[1]:
        assert response.status_code == 200
    else:
        assert response.status_code == 403
        assert json.loads(response.content) == {"error": "You do not have permission to use the Admin Console."}


@pytest.mark.django_db
@pytest.mark.parametrize("role_name", [r for r, (_, manage) in ROLE_PERMISSIONS.items() if not manage])
def test_django_admin_site_only_for_admin(client, make_member, role_name) -> None:
    client.force_login(make_member(role_name))
    response = client.get("/admin/")
    assert response.status_code == 302  # sent to login, never shown the admin


@pytest.mark.django_db
def test_admin_can_open_django_admin_site(client, admin_member) -> None:
    client.force_login(admin_member)
    assert client.get("/admin/").status_code == 200


@pytest.mark.django_db
def test_not_logged_in_is_sent_to_login(client) -> None:
    response = client.get("/accounts/api/members/")
    assert response.status_code == 401 and response.json() == {"error": "Login required."}  # API: JSON
    response = client.get("/")
    assert response.status_code == 302 and "/accounts/login/" in response.url  # pages: the login page


def test_403_page_shows_the_reason() -> None:
    request = RequestFactory().get("/activities/5/edit/")
    response = permission_denied(request, PermissionDenied("You do not have permission to create or edit activities."))
    assert response.status_code == 403
    assert "create or edit activities" in response.content.decode()
