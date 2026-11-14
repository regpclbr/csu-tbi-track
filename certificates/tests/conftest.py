"""Fixtures for Step 9 tests: a Conducted activity with attendees and a resource person, the
active design (template + VP + Director), and background tasks run straight away."""

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile

from activities import services as activity_services
from activities.models import Activity
from certificates import services
from certificates.models import Certificate, CertificateSignatory, CertificateTemplate
from conftest import _stored, image_bytes
from registrations import services as registration_services
from registrations.models import Registration
from registrations.tests.conftest import (  # noqa: F401  (re-exported fixtures)
    CERT,
    TEST_NOW,
    admin_staff,
    attended,
    defaults,
    director,
    evaluation_answers,
    move_to,
    new_activity,
    opens_at,
    scheduled,
    signup,
    valid_data,
    with_forms,
)
from startups.tests.conftest import STARTUP_PASSWORD, make_startup  # noqa: F401

S = Certificate.Status


@pytest.fixture
def portal_for():
    """Factory: portal_for(startup) is a new test client logged in to the startup portal."""
    from django.test import Client

    def login(startup) -> Client:
        client = Client()
        response = client.post(
            "/startups/portal/api/login/", {"email": startup.email, "password": STARTUP_PASSWORD}, content_type="application/json"
        )
        assert response.status_code == 200
        return client

    return login


@pytest.fixture
def startup_portal(portal_for, make_startup):
    return portal_for(make_startup(email="person0@example.com", navigatu_id="NAV-1"))
A4 = (1754, 1240)  # smallest accepted template size


def upload(name: str, content: bytes) -> SimpleUploadedFile:
    return SimpleUploadedFile(name, content)


def template_upload(name: str = "design.png", size: tuple[int, int] = A4, kind: str = "PNG") -> SimpleUploadedFile:
    return upload(name, image_bytes(*size, kind=kind))


@pytest.fixture
def design(director) -> tuple[CertificateTemplate, CertificateSignatory, CertificateSignatory]:
    """The active template, VP and Director (with real image files in the temporary media folder)."""
    template = CertificateTemplate.objects.create(
        name="TBI design", file_path=_stored("certificate_templates/design.png", image_bytes()), uploaded_by=director, is_active=True
    )
    vp = CertificateSignatory.objects.create(name="Dr. Vice Pres", position="Vice President", default_slot="right", signature_image_path=_stored("signatures/vp.png", image_bytes()))
    head = CertificateSignatory.objects.create(name="Engr. Dee Rector", position="Director", default_slot="left", signature_image_path=_stored("signatures/dir.png", image_bytes()))
    # Activities created before these defaults existed get them, as when a member sets the defaults (ASSUMPTIONS 366).
    services.fill_empty_slots_from_defaults()
    return template, vp, head


@pytest.fixture
def conducted(attended, director, move_to) -> tuple[Activity, list[Registration]]:
    """A Conducted activity: 2 attendees, 1 registrant who did not attend, 1 resource person."""
    people = attended(2)
    activity = people[0].activity
    Registration.objects.create(
        activity=activity, template=people[0].template, participant_category=people[0].participant_category,
        name="No Show", email="noshow@example.com",
    )
    speaker = activity_services.create_resource_person(director, "Dr. Maria Santos", "maria@example.com")
    activity_services.add_resource_person(director, activity, speaker, topic="Pitch Decks")
    activity_services.change_status(director, activity, "Conducted")
    activity.refresh_from_db()
    return activity, people


@pytest.fixture
def generated(conducted, design, director) -> tuple[Activity, list[Registration]]:
    activity, people = conducted
    services.generate_missing_certificates(activity)
    return activity, people


@pytest.fixture
def run_tasks(monkeypatch):
    """Background tasks run straight away instead of waiting for qcluster. Returns the list of
    tasks that ran, e.g. [("prepare", 3), ("send", 3)]."""
    from certificates import tasks

    ran: list[tuple[str, int]] = []

    def prepare(certificate_id: int) -> None:
        ran.append(("prepare", certificate_id))
        tasks.run_prepare_certificate(certificate_id)

    def send(certificate_id: int, attempt: int = 1) -> None:
        ran.append(("send", certificate_id))
        tasks.run_send_certificate(certificate_id, attempt)

    monkeypatch.setattr(services, "queue_prepare", prepare)
    monkeypatch.setattr(services, "queue_send", send)
    return ran


@pytest.fixture
def approve_all(director):
    """The Director approves every certificate of an activity waiting for approval ("Approve all")."""

    def run(activity: Activity) -> None:
        services.approve_certificates(director, activity=activity)

    return run


@pytest.fixture
def evaluate():
    """evaluate(registration, name=..., email=...) submits the evaluation through the real service."""

    def submit(registration: Registration, name: str = "Ana Cruz", email: str = "ana.cert@example.com"):
        activity = registration.activity
        return registration_services.submit_evaluation(
            registration_services.evaluation_token(registration),
            certificate_name=name,
            certificate_email=email,
            answers=evaluation_answers(activity),
            now=opens_at(activity),
        )

    return submit


def certificate_of(registration: Registration) -> Certificate:
    return Certificate.objects.get(registration=registration)


def appreciation_of(activity: Activity) -> Certificate:
    return Certificate.objects.get(activity=activity, recipient_type=Certificate.RecipientType.RESOURCE_PERSON)
