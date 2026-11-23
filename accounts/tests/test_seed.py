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
    assert DocumentType.objects.count() == 11  # incl. "Other" (ASSUMPTIONS 386), Activity Report, Event Photos (425)


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
    flags = {r.role_name: (r.can_create_activity, r.can_manage_users, r.can_approve_documents) for r in Role.objects.all()}
    assert flags == {
        "Director": (True, False, True),  # only the Director approves documents (ASSUMPTIONS 344)
        "Incubation Project Head": (True, False, False),
        "Community Development Head": (True, False, False),
        "Admin Staff": (True, False, False),  # creates and edits activities since ASSUMPTIONS 388
        "System Administrator": (False, True, False),
    }


@pytest.mark.django_db
def test_required_document_types_and_due_rules(admin_env) -> None:
    seed()
    required = {
        t.name: (t.due_reference, t.due_offset_days) for t in DocumentType.objects.filter(is_required=True)
    }
    assert required == {  # the Invitation Letter and the Accomplishment Report are optional (ASSUMPTIONS 386)
        "Activity Design": ("start_date", -5),  # NAVIGATU's 5-day rule (ASSUMPTIONS 416)
        "Request Letter": ("start_date", -5),
        "Attendance Sheet": ("end_date", 5),  # payment documents for the Supply Office (ASSUMPTIONS 425)
        "Activity Report": ("end_date", 5),
        "Event Photos": ("end_date", 5),
    }
    optional = DocumentType.objects.filter(is_required=False)
    assert set(optional.values_list("name", flat=True)) == {
        "Invitation Letter", "Accomplishment Report", "Pre-registration Form", "Evaluation Form", "Certificate", "Other"}
    assert all(t.due_reference is None and t.due_offset_days is None for t in optional)


@pytest.mark.django_db
def test_admin_account_gets_admin_role(admin_env) -> None:
    seed()
    admin = TBIMember.objects.get(email=ADMIN_EMAIL)
    assert admin.role.role_name == "System Administrator"
    assert admin.is_staff is True
    assert admin.must_change_password is False
    assert admin.check_password(ADMIN_PASSWORD)


@pytest.mark.django_db
def test_rerun_keeps_admin_edits_but_resets_defined_fields(admin_env) -> None:
    seed()
    # An admin edits fields the seed does not define, and (roles) one it does.
    DocumentType.objects.filter(name="Certificate").update(is_active=False, description="Edited")
    Role.objects.filter(role_name="Director").update(description="Edited role")
    admin = TBIMember.objects.get(email=ADMIN_EMAIL)
    admin.set_password("Changed-By-Admin-99!")
    admin.save()

    seed()

    certificate = DocumentType.objects.get(name="Certificate")
    assert (certificate.is_active, certificate.description) == (False, "Edited")  # kept
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


@pytest.mark.django_db
def test_rerun_never_undoes_a_document_type_change(admin_env) -> None:
    """Whether a type is required and its due rule are set only when seed_data creates the type (ASSUMPTIONS 386):
    an Admin who marks the Invitation Letter required again, or moves a due date, keeps that after a rerun."""
    seed()
    DocumentType.objects.filter(name="Invitation Letter").update(is_required=True, due_reference="start_date",
                                                                 due_offset_days=-7)
    DocumentType.objects.filter(name="Request Letter").update(due_offset_days=-3)
    DocumentType.objects.filter(name="Attendance Sheet").update(is_required=False, due_reference=None,
                                                                due_offset_days=None)
    seed()
    rules = {t.name: (t.is_required, t.due_reference, t.due_offset_days) for t in DocumentType.objects.all()}
    assert rules["Invitation Letter"] == (True, "start_date", -7)
    assert rules["Request Letter"] == (True, "start_date", -3)
    assert rules["Attendance Sheet"] == (False, None, None)


@pytest.mark.django_db
def test_other_type_is_created_only_if_missing(admin_env) -> None:
    DocumentType.objects.create(name="Other", description="Our own description")
    seed()
    other = DocumentType.objects.get(name="Other")
    assert other.description == "Our own description" and other.is_required is False
    assert DocumentType.objects.filter(name="Other").count() == 1
