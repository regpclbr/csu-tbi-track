"""Fixtures for document tests: seeded lookups, members, activities and small real files."""

import io
import zipfile
from datetime import date, datetime, time, timedelta
from decimal import Decimal

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile
from django.utils import timezone

from activities.models import Activity, ActivityStatus, ActivityType, ParticipantCategory
from documents import services
from documents.models import Document, DocumentType

# Tests pass this as `now`, so fixed calendar dates keep working after they pass.
TEST_NOW = timezone.make_aware(datetime(2026, 10, 20, 9, 0))  # Manila time
TODAY = date(2026, 10, 20)


def at(day: date, hour: int = 9) -> datetime:
    return timezone.make_aware(datetime.combine(day, time(hour, 0)))


# --- Real (tiny) file contents --------------------------------------------------------------

PDF_BYTES = b"%PDF-1.4\n1 0 obj << >> endobj\ntrailer << >>\n%%EOF\n"
PNG_BYTES = b"\x89PNG\r\n\x1a\n" + b"\x00" * 32
JPG_BYTES = b"\xff\xd8\xff\xe0" + b"\x00" * 32


def office_bytes(kind: str) -> bytes:
    """A minimal DOCX ("docx") or XLSX ("xlsx") ZIP package. The entries get a fixed date, so the bytes
    (and the test IDs that contain them) are the same on every run and in every xdist worker."""
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        for name, text in [("[Content_Types].xml", "<Types/>"), ("word/document.xml" if kind == "docx" else "xl/workbook.xml", "<x/>")]:
            archive.writestr(zipfile.ZipInfo(name, date_time=(2026, 1, 1, 0, 0, 0)), text)
    return buffer.getvalue()


def make_file(name: str = "Invitation.pdf", content: bytes = PDF_BYTES) -> SimpleUploadedFile:
    return SimpleUploadedFile(name, content)


# --- People ----------------------------------------------------------------------------------


@pytest.fixture
def director(make_member):
    return make_member("Director")


@pytest.fixture
def second_director(make_member):
    """A second member who can approve documents (Director role)."""
    return make_member("Director", email="second.director@example.com")


@pytest.fixture
def head(make_member):
    """Incubation Project Head: can create activities, cannot approve or return documents (ASSUMPTIONS 344)."""
    return make_member("Incubation Project Head")


@pytest.fixture
def system_admin(make_member):
    """System Administrator: manages users, never approves documents (separation of duties)."""
    return make_member("System Administrator")


# --- Activities and types -----------------------------------------------------------------------


@pytest.fixture
def make_activity(roles, director):
    """Factory: make_activity(status="Scheduled", start=date, end=date, created_by=member)."""

    def make(status: str = ActivityStatus.SCHEDULED, start: date = date(2026, 11, 10), end: date | None = None,
             created_by=None, title: str = "Pitching Workshop") -> Activity:
        end = end or start + timedelta(days=1)
        activity = Activity.objects.create(
            created_by=created_by or director,
            status=ActivityStatus.objects.get(name=status),
            activity_type=ActivityType.objects.get(name="Workshop"),
            title=title,
            start_date=start,
            end_date=end,
            start_time=time(8, 0), end_time=time(17, 0),
            venue="CSU Hall",
            max_slots=30,
            registration_deadline=at(start - timedelta(days=2), 17),
            evaluation_deadline=at(end + timedelta(days=7), 17),
            budget_amount=Decimal("1000.00"),
        )
        activity.participant_categories.add(ParticipantCategory.objects.get(name="Faculty"))
        return activity

    return make


@pytest.fixture
def scheduled(make_activity) -> Activity:
    return make_activity()


@pytest.fixture
def doc_type(roles) -> DocumentType:
    """Seeded required type: Request Letter, due start_date - 5 (the Invitation Letter is optional, ASSUMPTIONS 386)."""
    return DocumentType.objects.get(name="Request Letter")


@pytest.fixture
def upload(doc_type):
    """Factory: upload(member, activity, file=None, document_type=None, title="") through the service."""

    def make(member, activity, file=None, document_type=None, title: str = "") -> Document:
        return services.upload_document(
            member, activity, document_type or doc_type, file or make_file(), title, now=TEST_NOW
        )

    return make


@pytest.fixture
def certificate_for(director):
    """Factory: certificate_for(activity) creates a participant certificate (for "certificate file" tests)."""
    from certificates.models import Certificate, CertificateSignatory, CertificateTemplate
    from registrations.models import FormTemplate, FormType, Registration

    def make(activity: Activity) -> Certificate:
        form = FormTemplate.objects.create(activity=activity, form_type=FormType.REGISTRATION, title="R", created_by=director)
        registration = Registration.objects.create(
            activity=activity, template=form, participant_category=activity.participant_categories.first(),
            name="Ana", email="ana@example.com",
        )
        return Certificate.objects.create(
            certificate_template=CertificateTemplate.objects.create(name="Design", file_path="t.pdf", uploaded_by=director),
            certificate_number=f"TBI-{activity.pk}",
            recipient_type=Certificate.RecipientType.REGISTRATION,
            registration=registration,
            activity=activity,
            right_signatory=CertificateSignatory.objects.create(name="VP", position="Vice President", signature_image_path="vp.png"),
            left_signatory=CertificateSignatory.objects.create(name="D", position="Director", signature_image_path="d.png"),
            delivery_method=Certificate.DeliveryMethod.EMAIL,
        )

    return make
