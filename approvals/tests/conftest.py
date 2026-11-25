"""Fixtures for the approvals tests (ASSUMPTIONS 437): members per role, a small real PDF with a "Noted:" line, a linked
signature, submissions made through the service."""

from datetime import date, datetime, time, timedelta
from decimal import Decimal
from io import BytesIO

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile
from django.utils import timezone
from PIL import Image
from pypdf import PdfWriter
from pypdf.generic import DecodedStreamObject, DictionaryObject, NameObject

from activities.models import Activity, ActivityStatus, ActivityType, ParticipantCategory
from approvals import services
from certificates.models import CertificateSignatory
from conftest import _stored

NOW = timezone.make_aware(datetime(2026, 11, 25, 9, 0))


def make_pdf(lines: list[tuple[str, int]] | None = None, pages: int = 1) -> bytes:
    """A real PDF (Helvetica text) with the given (text, y) lines on every page."""
    lines = lines if lines is not None else [("Dear Director,", 700), ("Noted:", 300), ("DR. JUAN DELA CRUZ", 260)]
    writer = PdfWriter()
    for _ in range(pages):
        page = writer.add_blank_page(612, 792)
        font = DictionaryObject({NameObject("/Type"): NameObject("/Font"), NameObject("/Subtype"): NameObject("/Type1"),
                                 NameObject("/BaseFont"): NameObject("/Helvetica")})
        page[NameObject("/Resources")] = DictionaryObject(
            {NameObject("/Font"): DictionaryObject({NameObject("/F1"): writer._add_object(font)})})
        content = DecodedStreamObject()
        content.set_data("".join(f"BT /F1 12 Tf 72 {y} Td ({text}) Tj ET\n" for text, y in lines).encode())
        page[NameObject("/Contents")] = writer._add_object(content)
    buffer = BytesIO()
    writer.write(buffer)
    return buffer.getvalue()


def signature_png() -> bytes:
    image = Image.new("RGBA", (300, 100), (255, 255, 255, 0))
    for x in range(20, 280):
        image.putpixel((x, 50), (10, 20, 90, 255))
    buffer = BytesIO()
    image.save(buffer, "PNG")
    return buffer.getvalue()


def pdf_file(name: str = "Letter.pdf", content: bytes | None = None) -> SimpleUploadedFile:
    return SimpleUploadedFile(name, content if content is not None else make_pdf())


@pytest.fixture
def director(make_member):
    return make_member("Director")


@pytest.fixture
def staff(make_member):
    return make_member("Admin Staff")


@pytest.fixture
def head(make_member):
    return make_member("Incubation Project Head")


@pytest.fixture
def system_admin(make_member):
    return make_member("System Administrator")


@pytest.fixture
def director_signature(director) -> CertificateSignatory:
    """The Director's own signature on the signatory list, linked to their account."""
    return CertificateSignatory.objects.create(
        name="Dr. Director", position="TBI Director", member=director,
        signature_image_path=_stored("signatures/director-sign.png", signature_png()))


@pytest.fixture
def activity(roles, director) -> Activity:
    start = date(2026, 12, 10)
    activity = Activity.objects.create(
        created_by=director, status=ActivityStatus.objects.get(name="Scheduled"),
        activity_type=ActivityType.objects.get(name="Workshop"), title="Pitching Workshop",
        start_date=start, end_date=start + timedelta(days=1), start_time=time(8, 0), end_time=time(17, 0),
        venue="CSU Hall", max_slots=30,
        registration_deadline=timezone.make_aware(datetime(2026, 12, 8, 17, 0)),
        evaluation_deadline=timezone.make_aware(datetime(2026, 12, 20, 17, 0)), budget_amount=Decimal("0.00"))
    activity.participant_categories.add(ParticipantCategory.objects.get(name="Faculty"))
    return activity


@pytest.fixture
def submit(director):
    """Factory: submit(member, title=..., file=..., activity=None, document_type=None) through the service (the
    `director` fixture exists, so there is someone to approve it)."""

    def make(member, title: str = "Request letter", file=None, activity=None, document_type=None, note: str = ""):
        return services.submit(member, file or pdf_file(), title, note, activity, document_type, now=NOW)

    return make
