"""The hosted review version starts from a clean database: `seed_data` only (deploy/release.sh), no SEED_ADMIN_* values,
then create_first_admin (ASSUMPTIONS 395). Checks what a fresh seed gives, and that every email people get on the server
carries the https SITE_URL (ASSUMPTIONS 391)."""

from io import StringIO

import pytest
from django.core import mail
from django.core.management import call_command

from accounts.models import Position, Role, TBIMember
from activities.models import Activity
from documents.models import DocumentType
from startups.models import Startup

pytestmark = pytest.mark.django_db

FLAGS = ("can_create_activity", "can_manage_users", "can_approve_documents", "can_approve_certificates",
         "can_delete_activities")

# CLAUDE.md "Roles (seed defaults, ASSUMPTIONS 388)".
EXPECTED_ROLES = {
    "Director": (True, False, True, True, False),
    "Incubation Project Head": (True, False, False, False, False),  # certificates: the Director only (ASSUMPTIONS 421)
    "Community Development Head": (True, False, False, False, False),
    "Admin Staff": (True, False, False, False, False),
    Role.SYSTEM_ADMINISTRATOR: (False, True, False, False, True),
}


@pytest.fixture
def fresh_seed(monkeypatch) -> str:
    for name in ("SEED_ADMIN_EMAIL", "SEED_ADMIN_PASSWORD", "SEED_ADMIN_NAME"):
        monkeypatch.delenv(name, raising=False)
    out = StringIO()
    call_command("seed_data", stdout=out)
    return out.getvalue()


def test_fresh_seed_has_no_account_and_no_demo_data(fresh_seed) -> None:
    assert "Admin account skipped" in fresh_seed
    assert not TBIMember.objects.exists()
    assert not Activity.objects.exists() and not Startup.objects.exists()
    assert not Position.objects.exists()  # added by the System Administrator (Admin Console → Positions)


def test_fresh_seed_role_defaults(fresh_seed) -> None:
    roles = {role.role_name: tuple(getattr(role, flag) for flag in FLAGS) for role in Role.objects.all()}
    assert roles == EXPECTED_ROLES


def test_fresh_seed_document_types(fresh_seed) -> None:
    required = set(DocumentType.objects.filter(is_required=True).values_list("name", flat=True))
    assert required == {"Activity Design", "Request Letter", "Attendance Sheet", "Activity Report", "Event Photos"}  # 425
    optional = set(DocumentType.objects.filter(is_required=False).values_list("name", flat=True))
    assert {"Invitation Letter", DocumentType.OTHER} <= optional and "Accomplishment Report" not in optional  # 430


def test_seed_again_changes_nothing(fresh_seed) -> None:
    before = list(Role.objects.values_list("role_name", *FLAGS).order_by("role_name"))
    call_command("seed_data", stdout=StringIO())
    assert list(Role.objects.values_list("role_name", *FLAGS).order_by("role_name")) == before


# --- Every email carries the server's address -------------------------------------------------------------------------

SERVER = "https://tbi-review.southeastasia.cloudapp.azure.com"


@pytest.fixture
def on_server(settings):
    settings.SITE_URL = SERVER
    return settings


def test_invitation_email_has_the_site_url(on_server, admin_member, roles) -> None:
    from accounts.services import create_member

    create_member(admin_member, name="New Staff", email="new.staff@example.com", role=roles["Admin Staff"])
    assert f"{SERVER}/accounts/login/" in mail.outbox[-1].body


def test_password_changed_email_has_the_site_url(on_server, admin_member) -> None:
    from accounts.emails import send_password_changed_email

    send_password_changed_email(admin_member)
    assert f"{SERVER}/accounts/login/" in mail.outbox[-1].body


def test_evaluation_link_email_has_the_site_url(on_server, registration) -> None:
    from registrations import emails, services

    emails.send_evaluation_link(registration, services.evaluation_link(registration))
    assert f"{SERVER}/evaluate/" in mail.outbox[-1].body


def test_certificate_email_has_the_site_url(on_server, registration, make_certificate) -> None:
    from certificates.emails import send_certificate_email

    certificate = make_certificate(registration)
    send_certificate_email(certificate, "Ana Cruz", "ana@example.com", b"%PDF-1.7 test")
    assert f"CSU-TBI Track: {SERVER}/" in mail.outbox[-1].body
