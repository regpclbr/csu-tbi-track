"""Fixtures for assistant tests: real small files (pdf, docx, xlsx), approved through Step 6 services."""

import io
from datetime import date, datetime, time, timedelta
from decimal import Decimal

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile
from django.utils import timezone

from activities.models import Activity, ActivityStatus, ActivityType, ParticipantCategory
from documents import services as document_services
from documents.models import Document, DocumentType
from documents.tests.conftest import certificate_for  # noqa: F401  (fixture reused here)


def text_pdf(*lines: str) -> bytes:
    """A small but valid PDF whose page contains real text (one line per string)."""
    def escape(text: str) -> str:
        return text.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")

    commands = ["BT", "/F1 11 Tf", "50 780 Td", "14 TL"]
    for line in lines:
        commands.append(f"({escape(line)}) Tj T*")
    commands.append("ET")
    stream = "\n".join(commands).encode("latin-1")
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 842] /Contents 4 0 R /Resources << /Font << /F1 5 0 R >> >> >>",
        b"<< /Length " + str(len(stream)).encode() + b" >>\nstream\n" + stream + b"\nendstream",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    ]
    output = io.BytesIO()
    output.write(b"%PDF-1.4\n")
    offsets = []
    for number, body in enumerate(objects, start=1):
        offsets.append(output.tell())
        output.write(f"{number} 0 obj\n".encode() + body + b"\nendobj\n")
    xref = output.tell()
    output.write(f"xref\n0 {len(objects) + 1}\n0000000000 65535 f \n".encode())
    for offset in offsets:
        output.write(f"{offset:010d} 00000 n \n".encode())
    output.write(f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF\n".encode())
    return output.getvalue()


def scanned_pdf() -> bytes:
    """A valid PDF with a page but no text at all (like a scan without OCR)."""
    from pypdf import PdfWriter

    writer = PdfWriter()
    writer.add_blank_page(width=612, height=842)
    buffer = io.BytesIO()
    writer.write(buffer)
    return buffer.getvalue()


def docx_file(*paragraphs: str, table: list[list[str]] | None = None) -> bytes:
    from docx import Document as DocxDocument

    document = DocxDocument()
    for paragraph in paragraphs:
        document.add_paragraph(paragraph)
    if table:
        grid = document.add_table(rows=len(table), cols=len(table[0]))
        for r, row in enumerate(table):
            for c, value in enumerate(row):
                grid.cell(r, c).text = value
    buffer = io.BytesIO()
    document.save(buffer)
    return buffer.getvalue()


def xlsx_file(rows: list[list], title: str = "Budget") -> bytes:
    from openpyxl import Workbook

    workbook = Workbook()
    sheet = workbook.active
    sheet.title = title
    for row in rows:
        sheet.append(row)
    buffer = io.BytesIO()
    workbook.save(buffer)
    return buffer.getvalue()


PNG_BYTES = b"\x89PNG\r\n\x1a\n" + b"\x00" * 32


@pytest.fixture
def director(make_member):
    return make_member("Director")


@pytest.fixture
def head(make_member):
    return make_member("Incubation Project Head")


@pytest.fixture
def make_activity(roles, director):
    def make(title: str = "Pitching Workshop", start: date = date(2026, 11, 10), status: str = ActivityStatus.SCHEDULED) -> Activity:
        activity = Activity.objects.create(
            created_by=director,
            status=ActivityStatus.objects.get(name=status),
            activity_type=ActivityType.objects.get(name="Workshop"),
            title=title,
            start_date=start,
            end_date=start + timedelta(days=1),
            time_schedule="8:00 AM - 5:00 PM",
            venue="CSU Hall",
            max_slots=30,
            registration_deadline=timezone.make_aware(datetime.combine(start - timedelta(days=2), time(17))),
            evaluation_deadline=timezone.make_aware(datetime.combine(start + timedelta(days=8), time(17))),
            budget_amount=Decimal("1000.00"),
        )
        activity.participant_categories.add(ParticipantCategory.objects.get(name="Incubatees"))
        return activity

    return make


@pytest.fixture
def activity(make_activity) -> Activity:
    return make_activity()


@pytest.fixture
def make_document(director, head, activity):
    """Factory: make_document(content, name="design.docx", type_name="Activity Design", approve=True,
    on=activity, approved_at=None). Uploaded by the Director, approved by the Project Head."""

    def make(content: bytes, name: str = "design.docx", type_name: str = "Activity Design", approve: bool = True,
             on: Activity | None = None, title: str = "", approved_at: datetime | None = None) -> Document:
        document = document_services.upload_document(
            director, on or activity, DocumentType.objects.get(name=type_name), SimpleUploadedFile(name, content), title
        )
        if approve:
            document = document_services.approve_document(head, document, now=approved_at)
        return Document.objects.get(pk=document.pk)

    return make
