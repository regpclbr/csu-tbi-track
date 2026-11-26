"""Separation of duties (ASSUMPTIONS 85, 339-340): the System Administrator manages users and system settings, can view
everything for support and upload documents, but never creates or changes activity data. No role may have both flags.
Account and role changes are logged on accounts.audit."""

import importlib

import pytest
from django.apps import apps as django_apps
from django.core.exceptions import PermissionDenied
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import Client
from django.urls import reverse

from accounts import services
from accounts.models import Role, TBIMember
from activities import services as activity_services
from activities.tests.conftest import director, move_to, new_activity, valid_data  # noqa: F401  (fixtures)
from certificates import services as certificate_services
from documents import services as document_services
from documents.models import DocumentType
from registrations import services as registration_services
from registrations.models import FormType
from registrations.tests.conftest import attended, defaults, scheduled, signup, with_forms  # noqa: F401  (fixtures)
from reports import services as report_services
from conftest import VIEW_ONLY_ROLE

pytestmark = pytest.mark.django_db

PDF = b"%PDF-1.4\n1 0 obj<<>>endobj\ntrailer<<>>\n%%EOF"
SYSADMIN = Role.SYSTEM_ADMINISTRATOR


def client_for(member) -> Client:
    client = Client()
    client.force_login(member)
    return client


def pdf_upload() -> SimpleUploadedFile:
    return SimpleUploadedFile("design.pdf", PDF, content_type="application/pdf")


@pytest.fixture
def sysadmin(make_member) -> TBIMember:
    return make_member(SYSADMIN)


# --- The role -----------------------------------------------------------------------------------------------


def test_seeded_flags_never_combine_the_two_duties(roles) -> None:
    assert (roles[SYSADMIN].can_create_activity, roles[SYSADMIN].can_manage_users) == (False, True)
    assert "Admin" not in roles and roles["Admin Staff"].can_create_activity is True  # since ASSUMPTIONS 388
    assert not any(role.can_create_activity and role.can_manage_users for role in roles.values())


def _migration():
    return importlib.import_module("accounts.migrations.0003_system_administrator")


def test_migration_renames_admin_and_removes_can_create_activity() -> None:
    Role.objects.create(role_name="Admin", can_create_activity=True, can_manage_users=True)
    _migration().forwards(django_apps, None)
    role = Role.objects.get(role_name=SYSADMIN)
    assert (role.can_create_activity, role.can_manage_users) == (False, True)
    assert not Role.objects.filter(role_name="Admin").exists()
    _migration().backwards(django_apps, None)
    assert Role.objects.get(role_name="Admin").can_create_activity is True


def test_migration_stops_when_the_new_name_is_taken() -> None:
    Role.objects.create(role_name="Admin", can_create_activity=True, can_manage_users=True)
    Role.objects.create(role_name="system administrator")
    with pytest.raises(RuntimeError, match="already exists"):
        _migration().forwards(django_apps, None)
    assert Role.objects.get(role_name="Admin").can_create_activity is True  # nothing changed


def test_createsuperuser_uses_the_system_administrator_role(roles) -> None:
    member = TBIMember.objects.create_superuser("root@example.com", "Root", "pass-12345-strong")
    assert member.role.role_name == SYSADMIN and member.is_staff


# --- No role may have both flags ------------------------------------------------------------------------------


def test_a_role_cannot_get_both_flags(sysadmin, roles) -> None:
    director_role = roles["Director"]
    with pytest.raises(services.MemberManagementError, match="separation of duties"):
        services.update_role_permissions(sysadmin, director_role, can_create_activity=True, can_manage_users=True)
    with pytest.raises(services.MemberManagementError, match="separation of duties"):
        services.update_role_permissions(sysadmin, roles[SYSADMIN], can_create_activity=True, can_manage_users=True)
    director_role.refresh_from_db()
    roles[SYSADMIN].refresh_from_db()
    assert (director_role.can_create_activity, director_role.can_manage_users) == (True, False)
    assert roles[SYSADMIN].can_create_activity is False


def test_django_admin_role_form_shows_the_rule(sysadmin, roles) -> None:
    role = roles["Director"]
    response = client_for(sysadmin).post(
        f"/admin/accounts/role/{role.pk}/change/", {"can_create_activity": "on", "can_manage_users": "on"}
    )
    assert response.status_code == 200 and services.BOTH_FLAGS_REFUSED in response.content.decode()
    role.refresh_from_db()
    assert role.can_manage_users is False


def test_last_admin_guard_still_works(sysadmin, roles) -> None:
    with pytest.raises(services.MemberManagementError, match="At least one active System Administrator"):
        services.change_member_role(sysadmin, sysadmin, roles["Director"])
    with pytest.raises(services.MemberManagementError, match="At least one active System Administrator"):
        services.update_role_permissions(sysadmin, roles[SYSADMIN], can_create_activity=False, can_manage_users=False)


# --- Audit log ------------------------------------------------------------------------------------------------


def test_account_and_role_changes_are_logged_on_accounts_audit(sysadmin, make_member, roles, caplog) -> None:
    other = make_member("Director")
    with caplog.at_level("INFO", logger="accounts.audit"):
        services.change_member_role(sysadmin, other, roles["Admin Staff"])
        services.deactivate_member(sysadmin, other)
        services.reactivate_member(sysadmin, other)
        services.reset_member_password(sysadmin, other)
        services.update_role_permissions(sysadmin, roles[VIEW_ONLY_ROLE], can_create_activity=True, can_manage_users=False)
    records = [r for r in caplog.records if r.name == "accounts.audit" and r.levelname == "INFO"]
    text = "\n".join(r.getMessage() for r in records)
    who = f"by member_id={sysadmin.pk} <{sysadmin.email}>"
    target = f"member_id={other.pk} <{other.email}>"
    assert f"Member role changed: {target} role 'Director' -> 'Admin Staff' {who}" in text
    assert f"Member deactivated: {target} is_active True -> False {who}" in text
    assert f"Member reactivated: {target} is_active False -> True {who}" in text
    assert f"Password reset by an administrator: {target}" in text and who in text
    assert f"Role flag changed: role='{VIEW_ONLY_ROLE}' can_create_activity False -> True {who}" in text
    other.refresh_from_db()
    from django.core import mail

    temporary = mail.outbox[-1].body.split("Temporary password: ")[1].split()[0]
    assert temporary not in caplog.text  # never a password


def test_django_admin_role_change_of_a_member_is_logged(sysadmin, make_member, roles, caplog) -> None:
    other = make_member("Director")
    with caplog.at_level("INFO", logger="accounts.audit"):
        response = client_for(sysadmin).post(
            f"/admin/accounts/tbimember/{other.pk}/change/",
            {"email": other.email, "name": other.name, "role": roles["Admin Staff"].pk},
        )
    assert response.status_code == 302
    other.refresh_from_db()
    assert other.role == roles["Admin Staff"]
    assert f"Member role changed: member_id={other.pk} <{other.email}> role 'Director' -> 'Admin Staff'" in caplog.text


# --- Refused: every create / approve / generate action -------------------------------------------


def test_refused_activity_actions(sysadmin, valid_data, new_activity, with_forms) -> None:
    for action in (
        lambda: activity_services.create_activity(sysadmin, valid_data(title="Not allowed")),
        lambda: activity_services.update_activity(sysadmin, new_activity, {"venue": "Elsewhere"}),
        lambda: activity_services.change_status(sysadmin, new_activity, "Scheduled"),
        lambda: registration_services.create_form(sysadmin, new_activity, FormType.REGISTRATION),
    ):
        with pytest.raises(PermissionDenied):
            action()


def test_refused_attendance_and_evaluation_links(sysadmin, attended, scheduled) -> None:
    person = attended(1)[0]
    with pytest.raises(PermissionDenied):
        registration_services.set_attendance(sysadmin, scheduled, [person.pk], False)
    with pytest.raises(PermissionDenied):
        registration_services.send_evaluation_links(sysadmin, scheduled)


def test_upload_allowed_but_review_refused(sysadmin, director, new_activity) -> None:
    design = DocumentType.objects.get(name="Activity Design")
    own = document_services.upload_document(sysadmin, new_activity, design, pdf_upload())  # like any staff member
    assert own.uploaded_by == sysadmin
    document = document_services.upload_document(director, new_activity, design, pdf_upload())
    with pytest.raises(PermissionDenied):
        document_services.approve_document(sysadmin, document)
    with pytest.raises(PermissionDenied):
        document_services.return_document(sysadmin, document, "Please fix.")


def test_refused_certificate_actions(sysadmin, new_activity) -> None:
    """The System Administrator approves certificates and adds signatures (ASSUMPTIONS 361, tested in
    certificates/tests/test_signatures.py) but never applies the design, hands over or resends (certificates are created
    by the System, ASSUMPTIONS 362)."""
    for action in (
        lambda: certificate_services.regenerate_certificates(sysadmin, activity=new_activity),
        lambda: certificate_services.mark_handed_over(sysadmin, activity=new_activity),
        lambda: certificate_services.resend_certificates(sysadmin, activity=new_activity),
    ):
        with pytest.raises(PermissionDenied):
            action()


def test_refused_report_generation(sysadmin) -> None:
    with pytest.raises(PermissionDenied):
        report_services.request_report(sysadmin, {"report_type": "accomplishment", "format": "pdf",
                                                  "date_range_start": "2026-01-01", "date_range_end": "2026-12-31"})


def test_refused_pages(sysadmin, new_activity) -> None:
    client = client_for(sysadmin)
    assert client.get(reverse("activities:create")).status_code == 403
    assert client.get(reverse("activities:edit", args=[new_activity.pk])).status_code == 403
    assert client.post(reverse("activities:change_status", args=[new_activity.pk]), {"status": "Scheduled"}).status_code == 403
    response = client.post(reverse("reports:list"), {"report_type": "accomplishment", "format": "pdf",
                                                     "date_range_start": "2026-01-01", "date_range_end": "2026-12-31"})
    assert response.status_code == 403


# --- Still allowed: viewing for support, and every Admin Console page --------------------------------------


def test_can_view_every_workspace_page(sysadmin, new_activity) -> None:
    client = client_for(sysadmin)
    for url in (reverse("home"), reverse("activities:list"), reverse("activities:detail", args=[new_activity.pk]),
                reverse("documents:list"), reverse("certificates:list"), reverse("reports:list"),
                reverse("assistant:chat"), reverse("notifications:list")):
        assert client.get(url).status_code == 200, url


@pytest.mark.parametrize("name", ["accounts:console", "accounts:console_roles", "accounts:console_certificates",
                                  "accounts:console_system"])
def test_reaches_every_admin_console_page(sysadmin, name) -> None:
    assert client_for(sysadmin).get(reverse(name)).status_code == 200


def test_action_buttons_are_hidden(sysadmin, director, new_activity) -> None:
    create_url = reverse("activities:create")
    assert create_url not in client_for(sysadmin).get(reverse("home")).content.decode()
    assert create_url not in client_for(sysadmin).get(reverse("activities:list")).content.decode()
    detail = client_for(sysadmin).get(reverse("activities:detail", args=[new_activity.pk])).content.decode()
    assert reverse("activities:edit", args=[new_activity.pk]) not in detail and "View only" in detail
    assert create_url in client_for(director).get(reverse("home")).content.decode()  # unchanged for staff roles


def test_sidebar_shows_the_system_section_only_to_user_managers(sysadmin, director) -> None:
    html = client_for(sysadmin).get(reverse("home")).content.decode()
    assert 'id="nav-system">System</p>' in html and "Admin Console" in html
    assert 'id="nav-system"' not in client_for(director).get(reverse("home")).content.decode()


# --- Django admin: activity data is view-only without can_create_activity ---------------------------------


def test_django_admin_activity_data_is_view_only(sysadmin, director, new_activity) -> None:
    client = client_for(sysadmin)
    changelist = client.get("/admin/activities/activity/")
    assert changelist.status_code == 200
    assert 'name="action"' not in changelist.content.decode()  # no status / forms / evaluation-link actions
    assert client.get("/admin/activities/activity/add/").status_code == 403
    page = client.get(f"/admin/activities/activity/{new_activity.pk}/change/")
    assert page.status_code == 200 and 'name="_save"' not in page.content.decode()  # view only
    assert "Change status (now:" not in page.content.decode()
    assert client.post(f"/admin/activities/activity/{new_activity.pk}/change-status/", {"status": "Scheduled"}).status_code == 403
    registrations = client.get("/admin/registrations/registration/")
    assert registrations.status_code == 200 and 'name="action"' not in registrations.content.decode()
    # The user-management screens stay available.
    assert client.get("/admin/accounts/tbimember/").status_code == 200
    assert client.get("/admin/activities/participantcategory/add/").status_code == 200
