"""Admin Staff create and edit activities and approve certificates (ASSUMPTIONS 388): the seeded role's flags and what
they open. Everything follows the flags (never the role name); the "cannot ..." checks of the other test files use the
test-only role without flags (conftest.VIEW_ONLY_ROLE)."""

import pytest
from django.core.exceptions import PermissionDenied
from django.test import Client
from django.urls import reverse

from accounts.permissions import Permission, has_permission
from activities import services
from notifications.models import Notification

pytestmark = pytest.mark.django_db


@pytest.fixture
def staff(make_member):
    return make_member("Admin Staff")


def client_for(member) -> Client:
    client = Client()
    client.force_login(member)
    return client


def test_the_seeded_admin_staff_flags(roles, staff) -> None:
    role = roles["Admin Staff"]
    assert (role.can_create_activity, role.can_manage_users, role.can_approve_documents,
            role.can_approve_certificates) == (True, False, False, True)
    assert has_permission(staff, Permission.CREATE_ACTIVITY) and has_permission(staff, Permission.APPROVE_CERTIFICATES)
    assert not has_permission(staff, Permission.APPROVE_DOCUMENTS) and not has_permission(staff, Permission.MANAGE_USERS)


def test_admin_staff_create_and_edit_activities(staff, director, valid_data) -> None:
    activity = services.create_activity(staff, valid_data(title="Staff Workshop"))
    assert activity.created_by == staff and activity.status.name == "Draft"
    services.update_activity(staff, activity, {"venue": "CSU Gymnasium"})
    activity.refresh_from_db()
    assert activity.venue == "CSU Gymnasium"
    assert activity.audit_logs.filter(field_changed="Venue", changed_by=staff).exists()


def test_every_director_is_told_when_admin_staff_create_an_activity(staff, director, valid_data) -> None:
    activity = services.create_activity(staff, valid_data(title="Staff Seminar"))
    note = Notification.objects.get(type=Notification.ACTIVITY_CREATED)
    assert note.member == director and note.activity == activity
    assert note.message.startswith("Test Admin Staff created a new activity: Staff Seminar")


def test_the_activity_page_offers_changes_to_admin_staff_and_not_to_a_role_without_flags(staff, view_only,
                                                                                         director, valid_data) -> None:
    activity = services.create_activity(director, valid_data())
    url = reverse("activities:detail", args=[activity.pk])
    html = client_for(staff).get(url).content.decode()
    assert "View only: your role cannot change activities." not in html
    assert reverse("activities:edit", args=[activity.pk]) in html
    html = client_for(view_only).get(url).content.decode()
    assert "View only: your role cannot change activities." in html
    assert reverse("activities:edit", args=[activity.pk]) not in html
    assert client_for(staff).get(reverse("activities:create")).status_code == 200
    assert client_for(view_only).get(reverse("activities:create")).status_code == 403


def test_admin_staff_open_the_signatories_page_but_not_the_admin_console(staff, view_only) -> None:
    assert client_for(staff).get(reverse("certificates:signatories")).status_code == 200
    assert client_for(view_only).get(reverse("certificates:signatories")).status_code == 403
    assert client_for(staff).get(reverse("accounts:console")).status_code == 403


def test_admin_staff_still_cannot_approve_documents(staff, director, valid_data) -> None:
    from documents import services as document_services
    from documents.models import DocumentType
    from documents.tests.conftest import make_file

    activity = services.create_activity(director, valid_data())
    document = document_services.upload_document(director, activity, DocumentType.objects.get(name="Request Letter"),
                                                 make_file())
    with pytest.raises(PermissionDenied):
        document_services.approve_document(staff, document)
    document.refresh_from_db()
    assert document.status.status_name == "For Review"
