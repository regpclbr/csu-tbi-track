"""Lookup tables in the Django admin: statuses are read-only, types can be edited but not
deleted, and roles can only have their two permission flags changed.
(Participant categories have their own rules: activities/tests/test_participant_categories.py.)"""

import pytest
from django.contrib.admin.sites import site

from accounts import services
from accounts.models import Role
from activities.models import ActivityStatus, ActivityType
from documents.models import DocumentStatus, DocumentType
from conftest import VIEW_ONLY_ROLE


@pytest.fixture
def admin_request(rf, admin_member):
    request = rf.get("/admin/")
    request.user = admin_member
    return request


def permissions(model, request, obj) -> tuple[bool, bool, bool]:
    model_admin = site._registry[model]
    return (
        model_admin.has_add_permission(request),
        model_admin.has_change_permission(request, obj),
        model_admin.has_delete_permission(request, obj),
    )


@pytest.mark.django_db
@pytest.mark.parametrize("model", [ActivityStatus, DocumentStatus])
def test_fixed_name_lookups_are_read_only(admin_request, model) -> None:
    obj = model.objects.first()
    assert permissions(model, admin_request, obj) == (False, False, False)
    assert site._registry[model].has_view_permission(admin_request, obj) is True


@pytest.mark.django_db
@pytest.mark.parametrize("model", [ActivityType, DocumentType])
def test_types_can_be_edited_but_not_deleted(admin_request, model) -> None:
    assert permissions(model, admin_request, model.objects.first()) == (True, True, False)


@pytest.mark.django_db
def test_saving_a_status_row_from_the_admin_is_refused(client, admin_member) -> None:
    """The bug that was reported: editing a status lookup row (e.g. renaming Draft) is no longer possible."""
    draft = ActivityStatus.objects.get(name="Draft")
    client.force_login(admin_member)
    response = client.post(f"/admin/activities/activitystatus/{draft.pk}/change/", {"name": "Conducted"})
    assert response.status_code == 403
    draft.refresh_from_db()
    assert draft.name == "Draft"


# --- Roles ------------------------------------------------------------------------------


@pytest.mark.django_db
def test_roles_cannot_be_added_renamed_or_deleted(admin_request) -> None:
    role = Role.objects.get(role_name="Director")
    add, change, delete = permissions(Role, admin_request, role)
    assert (add, change, delete) == (False, True, False)
    readonly = site._registry[Role].get_readonly_fields(admin_request, role)
    assert "role_name" in readonly


@pytest.mark.django_db
def test_role_flags_can_be_edited(client, admin_member) -> None:
    staff = Role.objects.get(role_name=VIEW_ONLY_ROLE)  # every flag off (Admin Staff already creates activities, 388)
    client.force_login(admin_member)
    response = client.post(
        f"/admin/accounts/role/{staff.pk}/change/",
        {"can_create_activity": "on", "role_name": "Renamed!"},  # role_name is ignored (read-only)
    )
    assert response.status_code == 302
    staff.refresh_from_db()
    assert (staff.role_name, staff.can_create_activity, staff.can_manage_users) == (VIEW_ONLY_ROLE, True, False)


@pytest.mark.django_db
def test_admin_role_cannot_lose_manage_users_if_no_other_admin(client, admin_member) -> None:
    admin_role = Role.objects.get(role_name="System Administrator")
    client.force_login(admin_member)
    response = client.post(f"/admin/accounts/role/{admin_role.pk}/change/", {"can_create_activity": "on"})
    assert response.status_code == 200
    assert "At least one active System Administrator must always exist" in response.content.decode()
    admin_role.refresh_from_db()
    assert admin_role.can_manage_users is True


@pytest.mark.django_db
def test_update_role_permissions_service(admin_member, make_member) -> None:
    admin_role = Role.objects.get(role_name="System Administrator")
    with pytest.raises(services.MemberManagementError):
        services.update_role_permissions(admin_member, admin_role, can_create_activity=True, can_manage_users=False)

    # Allowed once a member of another role can manage users (that role gives up can_create_activity and both
    # approval flags: never together with can_manage_users).
    director_role = Role.objects.get(role_name="Director")
    services.update_role_permissions(
        admin_member, director_role, can_create_activity=False, can_manage_users=True, can_approve_documents=False,
        can_approve_certificates=False,
    )
    make_member("Director")
    services.update_role_permissions(admin_member, admin_role, can_create_activity=True, can_manage_users=False)
    admin_role.refresh_from_db()
    assert admin_role.can_manage_users is False


@pytest.mark.django_db
def test_only_admins_can_change_role_flags(make_member) -> None:
    from django.core.exceptions import PermissionDenied

    with pytest.raises(PermissionDenied):
        services.update_role_permissions(
            make_member("Director"), Role.objects.get(role_name="Admin Staff"), can_create_activity=True, can_manage_users=False
        )


@pytest.mark.django_db
def test_system_administrator_does_not_create_activities(roles) -> None:
    """Decided (ASSUMPTIONS 85, 339): the System Administrator manages users and does not create activities."""
    assert roles["System Administrator"].can_create_activity is False
    assert roles["System Administrator"].can_manage_users is True
    assert roles["Admin Staff"].can_create_activity is True  # since ASSUMPTIONS 388
