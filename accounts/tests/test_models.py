"""TBI_MEMBER as the user model, and admin-site access by role."""

import pytest

from accounts.models import TBIMember


@pytest.mark.django_db
def test_email_is_the_login_and_password_is_hashed(member: TBIMember) -> None:
    assert TBIMember.USERNAME_FIELD == "email"
    assert member.password != "pass-12345"
    assert member.check_password("pass-12345")
    assert member.must_change_password is True


@pytest.mark.django_db
def test_only_admin_role_is_staff(member: TBIMember, admin_role) -> None:
    assert member.is_staff is False
    assert member.has_module_perms("activities") is False

    member.role = admin_role
    assert member.is_staff is True
    assert member.has_perm("activities.change_activity") is True


@pytest.mark.django_db
def test_inactive_admin_has_no_permissions(admin_role) -> None:
    admin = TBIMember.objects.create_user("a@example.com", "Admin", admin_role, "pass-12345", is_active=False)
    assert admin.has_perm("activities.change_activity") is False


@pytest.mark.django_db
def test_create_superuser_uses_admin_role(admin_role) -> None:
    admin = TBIMember.objects.create_superuser("root@example.com", "Root", "pass-12345")
    assert admin.role == admin_role
    assert admin.is_superuser and admin.is_staff
    assert admin.must_change_password is False
