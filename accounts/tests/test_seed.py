"""`manage.py seed_data`: lookups, document due rules and the Admin account."""

from io import StringIO

import pytest
from django.core.management import CommandError, call_command

from accounts.models import Role, TBIMember
from activities.models import ActivityStatus, ActivityType, ParticipantCategory
from documents.models import DocumentStatus, DocumentType

ADMIN_EMAIL = "admin@example.com"
ADMIN_PASSWORD = "Seed-Admin-Pass-2026!"
SEEDED_MODELS = [ActivityStatus, ParticipantCategory, ActivityType, DocumentStatus, Role, DocumentType, TBIMember]


@pytest.fixture
def admin_env(monkeypatch) -> None:
    monkeypatch.setenv("SEED_ADMIN_EMAIL", ADMIN_EMAIL)
    monkeypatch.setenv("SEED_ADMIN_PASSWORD", ADMIN_PASSWORD)
    monkeypatch.setenv("SEED_ADMIN_NAME", "Seed Admin")


def seed() -> str:
    out = StringIO()
    call_command("seed_data", stdout=out)
    return out.getvalue()


def row_counts() -> dict[str, int]:
    return {model.__name__: model.objects.count() for model in SEEDED_MODELS}


@pytest.mark.django_db
def test_seed_loads_every_lookup(admin_env) -> None:
    seed()
    assert set(ActivityStatus.objects.values_list("name", flat=True)) == {"Draft", "Scheduled", "Ongoing", "Conducted"}
    assert set(ParticipantCategory.objects.values_list("name", flat=True)) == {"Faculty", "Incubatees", "MSMEs"}
    assert set(ActivityType.objects.values_list("name", flat=True)) == {
        "Workshop", "Training", "Seminar", "Mentoring", "Networking", "Forum",
    }
    assert set(DocumentStatus.objects.values_list("status_name", flat=True)) == {"For Review", "Approved"}
    assert DocumentType.objects.count() == 8


@pytest.mark.django_db
def test_seed_twice_creates_no_duplicates(admin_env) -> None:
    seed()
    first = row_counts()
    output = seed()
    assert row_counts() == first
    assert "0 created" in output
    assert "already exists" in output


@pytest.mark.django_db
def test_role_flags(admin_env) -> None:
    seed()
    flags = {r.role_name: (r.can_create_activity, r.can_manage_users) for r in Role.objects.all()}
    assert flags == {
        "Director": (True, False),
        "Incubation Project Head": (True, False),
        "Community Development Head": (True, False),
        "Admin Staff": (False, False),
        "Admin": (True, True),
    }


@pytest.mark.django_db
def test_required_document_types_and_due_rules(admin_env) -> None:
    seed()
    required = {
        t.name: (t.due_reference, t.due_offset_days) for t in DocumentType.objects.filter(is_required=True)
    }
    assert required == {
        "Invitation Letter": ("start_date", -7),
        "Activity Design": ("start_date", -10),
        "Request Letter": ("start_date", -5),
        "Attendance Sheet": ("end_date", 0),
        "Accomplishment Report": ("end_date", 7),
    }
    optional = DocumentType.objects.filter(is_required=False)
    assert set(optional.values_list("name", flat=True)) == {"Pre-registration Form", "Evaluation Form", "Certificate"}
    assert all(t.due_reference is None and t.due_offset_days is None for t in optional)


@pytest.mark.django_db
def test_admin_account_gets_admin_role(admin_env) -> None:
    seed()
    admin = TBIMember.objects.get(email=ADMIN_EMAIL)
    assert admin.role.role_name == "Admin"
    assert admin.is_staff is True
    assert admin.must_change_password is False
    assert admin.check_password(ADMIN_PASSWORD)


@pytest.mark.django_db
def test_rerun_keeps_admin_edits_but_resets_defined_fields(admin_env) -> None:
    seed()
    # An admin edits fields the seed does not define, and one it does.
    DocumentType.objects.filter(name="Certificate").update(is_active=False, description="Edited")
    DocumentType.objects.filter(name="Invitation Letter").update(due_offset_days=-3)
    Role.objects.filter(role_name="Director").update(description="Edited role")
    admin = TBIMember.objects.get(email=ADMIN_EMAIL)
    admin.set_password("Changed-By-Admin-99!")
    admin.save()

    seed()

    certificate = DocumentType.objects.get(name="Certificate")
    assert (certificate.is_active, certificate.description) == (False, "Edited")  # kept
    assert DocumentType.objects.get(name="Invitation Letter").due_offset_days == -7  # defined field reset
    assert Role.objects.get(role_name="Director").description == "Edited role"  # kept
    assert TBIMember.objects.get(email=ADMIN_EMAIL).check_password("Changed-By-Admin-99!")  # never reset


@pytest.mark.django_db
def test_admin_skipped_without_env(monkeypatch) -> None:
    monkeypatch.delenv("SEED_ADMIN_EMAIL", raising=False)
    monkeypatch.delenv("SEED_ADMIN_PASSWORD", raising=False)
    output = seed()
    assert "Admin account skipped" in output
    assert TBIMember.objects.count() == 0
    assert Role.objects.count() == 5  # lookups are still loaded


@pytest.mark.django_db
def test_weak_admin_password_is_refused(monkeypatch) -> None:
    monkeypatch.setenv("SEED_ADMIN_EMAIL", ADMIN_EMAIL)
    monkeypatch.setenv("SEED_ADMIN_PASSWORD", "123")
    with pytest.raises(CommandError, match="too weak"):
        seed()
    assert not TBIMember.objects.filter(email=ADMIN_EMAIL).exists()
