"""Shared pytest fixtures for all apps. App-specific fixtures live in each app's tests/."""

import os
from datetime import date, datetime, time, timedelta
from decimal import Decimal

import pytest
from django.utils import timezone

from accounts.models import Role, TBIMember
from activities.models import Activity, ActivityStatus, ActivityType, ParticipantCategory
from certificates.models import Certificate, CertificateSignatory, CertificateTemplate
from registrations.models import FormTemplate, FormType, Registration


@pytest.fixture(autouse=True)
def fast_password_hashing(settings) -> None:
    """Tests only: a fast hasher. The real (slow, secure) PBKDF2 hasher is used everywhere else."""
    settings.PASSWORD_HASHERS = ["django.contrib.auth.hashers.MD5PasswordHasher"]


@pytest.fixture(autouse=True)
def fake_gemini(monkeypatch):
    """Tests NEVER call the real Gemini API.

    - Every caller of assistant.gemini.get_gateway() gets this FakeGemini (the one injection point).
    - Creating a real google-genai Client fails the test immediately.
    - Retry waits are skipped.
    """
    from google import genai

    from assistant import gemini
    from assistant.testing import FakeGemini

    def refuse_real_client(*args, **kwargs):
        raise RuntimeError("Tests must never call the real Gemini API (google.genai.Client was created).")

    monkeypatch.setattr(genai, "Client", refuse_real_client)
    fake = FakeGemini()
    monkeypatch.setattr(gemini, "_override", fake)
    monkeypatch.setattr(gemini, "sleep", lambda seconds: None)
    return fake


@pytest.fixture(autouse=True)
def fake_pdf_renderer(monkeypatch, request):
    """Tests NEVER render with real WeasyPrint (no Pango needed): every certificate PDF goes
    through this FakeRenderer (certificates.pdf.get_renderer is the one injection point).
    Exception: a test marked @pytest.mark.real_weasyprint (it skips itself without Pango)."""
    from certificates import pdf
    from certificates.testing import FakeRenderer

    if request.node.get_closest_marker("real_weasyprint"):
        return None

    def refuse_real_render(self, html):
        raise RuntimeError("Tests must never render with real WeasyPrint.")

    monkeypatch.setattr(pdf.WeasyPrintRenderer, "render", refuse_real_render)
    fake = FakeRenderer()
    monkeypatch.setattr(pdf, "_override", fake)
    return fake


@pytest.fixture(autouse=True)
def no_real_smtp(monkeypatch) -> None:
    """Tests NEVER send real email. Django already swaps every mailer for in-memory mail
    (mail.outbox); opening an SMTP connection anyway fails the test."""
    import smtplib

    def refuse(*args, **kwargs):
        raise RuntimeError("Tests must never open a real SMTP connection.")

    monkeypatch.setattr(smtplib, "SMTP", refuse)
    monkeypatch.setattr(smtplib, "SMTP_SSL", refuse)


@pytest.fixture(autouse=True)
def registration_emails_run_now(monkeypatch):
    """Registration emails are Django-Q2 tasks; in tests they run straight away instead of waiting
    for qcluster (like run_tasks for certificates), so mail.outbox shows them. Returns the real
    queue functions, for the tests that check a task is really queued."""
    from registrations import services, tasks

    real = {"confirmation": services.queue_confirmation, "evaluation_link": services.queue_evaluation_link}
    monkeypatch.setattr(services, "queue_confirmation", tasks.run_send_registration_confirmation)
    monkeypatch.setattr(services, "queue_evaluation_link", tasks.run_send_evaluation_link)
    return real


def image_bytes(width: int = 8, height: int = 6, kind: str = "PNG") -> bytes:
    """A small real image (PNG or JPEG) for upload tests."""
    from io import BytesIO

    from PIL import Image

    buffer = BytesIO()
    Image.new("RGB", (width, height), "white").save(buffer, kind)
    return buffer.getvalue()


@pytest.fixture(autouse=True)
def temporary_media_root(settings, tmp_path) -> None:
    """Tests only: uploaded files go to a temporary folder, never the real media folder.
    tmp_path is new for every test (and separate for every xdist worker), so tests never share files."""
    settings.MEDIA_ROOT = tmp_path / "media"


@pytest.fixture(autouse=True)
def fresh_cache(settings):
    """Tests only: an empty in-memory cache for every test (login lockout and rate-limit counters).
    The real database cache table is not a model, so a transactional test's flush would not empty it;
    in memory it also belongs to one xdist worker only."""
    from django.core.cache import cache

    settings.CACHES = {"default": {"BACKEND": "django.core.cache.backends.locmem.LocMemCache", "LOCATION": "tests"}}
    cache.clear()
    yield
    cache.clear()


# --- The clock ---------------------------------------------------------------------------------------------
# The app reads the time only through django.utils.timezone.now() (localdate/localtime use it too), so replacing
# that one function moves every "now" and "today". Signed-link timestamps (time.time) are not moved.
_REAL_NOW = timezone.now


def set_clock(monkeypatch, moment: datetime) -> None:
    """From now on timezone.now() starts at `moment` and keeps ticking with the real clock (so "later" stays later)."""
    if timezone.is_naive(moment):
        moment = timezone.make_aware(moment)  # naive = Manila time (TIME_ZONE)
    real_start = _REAL_NOW()
    monkeypatch.setattr(timezone, "now", lambda: moment + (_REAL_NOW() - real_start))


@pytest.fixture(autouse=True)
def fake_clock_from_env(monkeypatch) -> None:
    """TEST_FAKE_NOW=2026-10-28T23:30 (Manila time) runs every test as if it started at that moment, to find tests
    that depend on the time of day (e.g. after 23:00 "now + 1 hour" is tomorrow). Unset: the real clock."""
    if value := os.environ.get("TEST_FAKE_NOW"):
        set_clock(monkeypatch, datetime.fromisoformat(value))


@pytest.fixture
def clock(monkeypatch):
    """Factory: clock(datetime(2026, 10, 28, 23, 30)) sets the time (Manila) for the rest of the test."""
    return lambda moment: set_clock(monkeypatch, moment)


@pytest.fixture
def admin_role() -> Role:
    return Role.objects.create(role_name=Role.SYSTEM_ADMINISTRATOR, can_create_activity=False, can_manage_users=True)


@pytest.fixture
def staff_role() -> Role:
    return Role.objects.create(role_name="Admin Staff", can_create_activity=True, can_manage_users=False)


@pytest.fixture
def member(staff_role: Role) -> TBIMember:
    return TBIMember.objects.create_user("staff@example.com", "Staff Member", staff_role, "pass-12345")


@pytest.fixture
def activity(member: TBIMember) -> Activity:
    deadline = timezone.make_aware(datetime(2026, 11, 1, 17, 0))
    activity = Activity.objects.create(
        created_by=member,
        status=ActivityStatus.objects.create(name="Draft"),
        activity_type=ActivityType.objects.create(name="Workshop"),
        title="Pitching Workshop",
        start_date=date(2026, 11, 5),
        end_date=date(2026, 11, 6),
        start_time=time(8, 0), end_time=time(17, 0),
        venue="CSU Hall",
        max_slots=30,
        registration_deadline=deadline,
        evaluation_deadline=deadline + timedelta(days=14),
        budget_amount=Decimal("5000.00"),
    )
    activity.participant_categories.add(ParticipantCategory.objects.create(name="Faculty"))
    return activity


@pytest.fixture
def registration_form(activity: Activity, member: TBIMember) -> FormTemplate:
    return FormTemplate.objects.create(
        activity=activity, form_type=FormType.REGISTRATION, title="Registration", created_by=member
    )


@pytest.fixture
def registration(activity: Activity, registration_form: FormTemplate) -> Registration:
    return Registration.objects.create(
        activity=activity,
        template=registration_form,
        participant_category=activity.participant_categories.first(),
        name="Ana Cruz",
        email="ana@example.com",
    )


def _stored(name: str, content: bytes) -> str:
    """Save a file in the (temporary) media folder and return its stored name."""
    from django.core.files.base import ContentFile
    from django.core.files.storage import default_storage

    return default_storage.save(name, ContentFile(content))


@pytest.fixture
def certificate_template(member: TBIMember) -> CertificateTemplate:
    return CertificateTemplate.objects.create(
        name="Default design", file_path=_stored("certificate_templates/default.png", image_bytes()),
        uploaded_by=member, is_active=True,
    )


@pytest.fixture
def signatories() -> tuple[CertificateSignatory, CertificateSignatory]:
    vp = CertificateSignatory.objects.create(
        name="VP", position="Vice President", default_slot="right", signature_image_path=_stored("signatures/vp.png", image_bytes())
    )
    director = CertificateSignatory.objects.create(
        name="Director", position="Director", default_slot="left", signature_image_path=_stored("signatures/director.png", image_bytes())
    )
    return vp, director


@pytest.fixture
def make_certificate(certificate_template, signatories):
    """Factory: make_certificate(registration=..., number=...) creates a participant certificate."""

    def make(registration: Registration, number: str = "TBI-0001") -> Certificate:
        vp, director = signatories
        return Certificate.objects.create(
            certificate_template=certificate_template,
            certificate_number=number,
            recipient_type=Certificate.RecipientType.REGISTRATION,
            registration=registration,
            activity=registration.activity,
            right_signatory=vp,
            left_signatory=director,
            delivery_method=Certificate.DeliveryMethod.EMAIL,
        )

    return make


# --- Staff accounts with the real seeded roles (Step 3) ------------------------------

STRONG_PASSWORD = "Strong-Pass-2026!"


# A test-only role with NO permission flags (ASSUMPTIONS 388): the "cannot ..." checks use it (they used Admin Staff
# before Admin Staff could create activities and approve certificates). It exists only in test databases.
VIEW_ONLY_ROLE = "View Only (test)"

CREATION_PDF = b"%PDF-1.4\n1 0 obj << >> endobj\ntrailer << >>\n%%EOF\n"


def creation_document_files() -> dict:
    """The New activity form's required uploads (ASSUMPTIONS 405) as POST data: `required-<type id>-file` for every
    documents.services.creation_required_types() type (needs the `roles` fixture: it seeds the types). Add it to any
    test that posts the New activity form."""
    from django.core.files.uploadedfile import SimpleUploadedFile

    from documents.services import creation_required_types

    return {f"required-{t.pk}-file": SimpleUploadedFile(f"{t.pk}.pdf", CREATION_PDF) for t in creation_required_types()}


@pytest.fixture
def roles() -> dict[str, Role]:
    """The five seeded roles, by name, plus the test-only VIEW_ONLY_ROLE (every flag off)."""
    from accounts.seed import load_lookups

    load_lookups()
    Role.objects.get_or_create(role_name=VIEW_ONLY_ROLE, defaults={
        "can_create_activity": False, "can_manage_users": False, "can_approve_documents": False,
        "can_approve_certificates": False, "description": "Test only: every permission flag off."})
    return {role.role_name: role for role in Role.objects.all()}


@pytest.fixture
def view_only(make_member) -> TBIMember:
    """An active staff member whose role has no permission flags: views everything, changes nothing that needs a flag
    (uploads documents, like every active member)."""
    return make_member(VIEW_ONLY_ROLE)


@pytest.fixture
def make_member(roles):
    """Factory: make_member("Director", email=..., must_change_password=False) with STRONG_PASSWORD."""

    def make(role_name: str, email: str | None = None, **extra) -> TBIMember:
        email = email or f"{role_name.lower().replace(' ', '.')}@example.com"
        extra.setdefault("must_change_password", False)
        return TBIMember.objects.create_user(email, f"Test {role_name}", roles[role_name], STRONG_PASSWORD, **extra)

    return make


@pytest.fixture
def admin_member(make_member) -> TBIMember:
    return make_member(Role.SYSTEM_ADMINISTRATOR)


@pytest.fixture
def both_flags_member(roles) -> TBIMember:
    """For the tests of the Django-admin activity screens, which need is_staff (can_manage_users) AND
    can_create_activity. No real role may have both (separation of duties, ASSUMPTIONS 339: the services refuse
    it), so in practice those screens are view only; this test-only role is written straight to the database."""
    role = Role.objects.create(role_name="Test role with both flags", can_create_activity=True, can_manage_users=True)
    return TBIMember.objects.create_user("both.flags@example.com", "Test Both Flags", role, STRONG_PASSWORD,
                                         must_change_password=False)
