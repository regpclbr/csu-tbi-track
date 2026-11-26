"""The generated "Attendance" and "Confirmation of Attendance" forms (ASSUMPTIONS 434): the right people on each page,
the signature cells, "Prepared by" with the POSITION, saved as approved-copy documents of the right types.
The PDF renderer is the fake one (root conftest): its "PDF" contains the HTML, so the names can be checked."""

from datetime import date, datetime, time, timedelta

import pytest
from django.conf import settings
from django.urls import reverse
from django.utils import timezone

from accounts.models import Position
from activities import services as activity_services
from documents import services as document_services
from documents.models import DocumentStatus, DocumentType
from registrations import attendance, attendance_forms, services

from .conftest import FIXED

pytestmark = pytest.mark.django_db

TYPED = {"kind": "typed", "typed_name": "Typed Signer", "confirmed": True}


@pytest.fixture
def form_types() -> None:
    for name in ("Attendance Sheet", settings.CONFIRMATION_OF_ATTENDANCE):
        DocumentType.objects.get_or_create(name=name, defaults={"is_required": True, "due_reference": "end_date",
                                                                "due_offset_days": 5})


def at(day, hour, minute=0) -> datetime:
    return timezone.make_aware(datetime.combine(day, time(hour, minute)))


@pytest.fixture
def people(scheduled, signup):
    """Ana (online, typed signature, both days), Ben (online, cancelled, no check-in), Walter (walk-in, day 2 only)."""
    ana = signup(scheduled, "ana@example.com", name="Ana Cruz", signature=TYPED)
    ben = signup(scheduled, "ben@example.com", name="Ben Reyes", signature={**TYPED, "typed_name": "Ben Reyes"})
    services.cancel_registration(ben, now=at(scheduled.start_date - timedelta(days=3), 9))
    attendance.check_in(scheduled, "ana@example.com", now=at(scheduled.start_date, 8, 5))
    attendance.check_in(scheduled, "ana@example.com", now=at(scheduled.end_date, 8, 10))
    walter = attendance.register_walk_in(scheduled, name="Walter In", email="walter@example.com", **FIXED,
                                         signature=TYPED, now=at(scheduled.end_date, 8, 30)).registration
    return ana, ben, walter


def test_date_span() -> None:
    assert attendance_forms.date_span(date(2026, 10, 21), date(2026, 10, 22)) == "October 21–22, 2026"
    assert attendance_forms.date_span(date(2026, 10, 21), date(2026, 10, 21)) == "October 21, 2026"
    assert attendance_forms.date_span(date(2026, 10, 31), date(2026, 11, 1)) == "October 31 – November 1, 2026"
    assert attendance_forms.date_span(date(2026, 12, 31), date(2027, 1, 1)) == "December 31, 2026 – January 1, 2027"


def test_attendance_has_one_page_per_day_with_that_days_people(scheduled, people) -> None:
    pages = attendance_forms.attendance_pages(scheduled)
    assert [page["date"] for page in pages] == [attendance_forms.long_date(d) for d in (scheduled.start_date, scheduled.end_date)]
    assert [row["name"] for row in pages[0]["rows"]] == ["Ana Cruz"]
    assert [row["name"] for row in pages[1]["rows"]] == ["Ana Cruz", "Walter In"]
    assert pages[0]["rows"][0]["time"] == "8:05 AM" and pages[1]["rows"][1]["walk_in"]


def test_confirmation_lists_pre_registrations_only(scheduled, people) -> None:
    rows = attendance_forms.confirmation_rows(scheduled)
    assert [(row["name"], row["will_attend"]) for row in rows] == [("Ana Cruz", True), ("Ben Reyes", False)]


def test_generated_pdfs_contain_the_right_people_and_are_saved_as_documents(
        scheduled, people, director, form_types, fake_pdf_renderer) -> None:
    director.position = Position.objects.create(name="Director, CSU-TBI")
    director.save()
    attendance_sheet = attendance_forms.generate_form(director, scheduled, attendance_forms.ATTENDANCE)
    html = fake_pdf_renderer.last
    assert html.count("<h1>ATTENDANCE</h1>") == 2  # one sheet per day
    for text in ("Ana Cruz", "Walter In", "Typed Signer", "F-PROC-COA-005 Rev.03, 10-10-2023", "Director, CSU-TBI",
                 director.name, attendance_forms.FOOTER_NOTE, scheduled.venue):
        assert text in html
    assert "Ben Reyes" not in html  # cancelled and never checked in

    confirmation = attendance_forms.generate_form(director, scheduled, attendance_forms.CONFIRMATION)
    html = fake_pdf_renderer.last
    assert "CONFIRMATION OF ATTENDANCE" in html and "Will attend" in html and "Will not attend" in html
    assert "Ana Cruz" in html and "Ben Reyes" in html and "Walter In" not in html  # walk-ins never signified
    assert attendance_forms.date_span(scheduled.start_date, scheduled.end_date) in html

    for document, type_name in ((attendance_sheet, "Attendance Sheet"), (confirmation, settings.CONFIRMATION_OF_ATTENDANCE)):
        assert document.document_type.name == type_name
        assert document.status.status_name == DocumentStatus.APPROVED and document.reviewed_by is None  # approved copy
        assert document.file_path.read().startswith(b"%PDF")


def test_image_signatures_are_embedded_as_data_uris(scheduled, signup, director, form_types, fake_pdf_renderer) -> None:
    from .test_attendance_days import drawn

    signup(scheduled, "ana@example.com", signature=drawn())
    attendance_forms.generate_form(director, scheduled, attendance_forms.CONFIRMATION)
    assert 'src="data:image/png;base64,' in fake_pdf_renderer.last


def test_staff_marked_without_signature_says_so(scheduled, signup, director, form_types, fake_pdf_renderer) -> None:
    person = signup(scheduled, "ana@example.com")
    activity_services.change_status(director, scheduled, "Ongoing")
    services.set_attendance(director, scheduled, [person.pk], True)
    attendance_forms.generate_form(director, scheduled, attendance_forms.ATTENDANCE)
    assert "Marked by staff" in fake_pdf_renderer.last


def test_refused_reasons(scheduled, director, form_types, valid_data) -> None:
    draft = activity_services.create_activity(director, valid_data(title="Still a draft"))
    assert attendance_forms.refused_reason(draft, attendance_forms.ATTENDANCE) == attendance_forms.DRAFT_REFUSED
    assert attendance_forms.refused_reason(scheduled, attendance_forms.ATTENDANCE) == attendance_forms.NO_ATTENDANCE
    assert attendance_forms.refused_reason(scheduled, attendance_forms.CONFIRMATION) == attendance_forms.NO_PRE_REGISTRATION
    with pytest.raises(attendance_forms.FormError):
        attendance_forms.generate_form(director, scheduled, attendance_forms.ATTENDANCE)


def test_only_can_create_activity_generates(scheduled, people, view_only, form_types) -> None:
    from django.core.exceptions import PermissionDenied

    with pytest.raises(PermissionDenied):
        attendance_forms.generate_form(view_only, scheduled, attendance_forms.ATTENDANCE)


def test_generate_view_and_documents_tab_button(client, scheduled, people, director, form_types, move_to) -> None:
    client.force_login(director)
    activity_services.change_status(director, scheduled, "Ongoing")
    activity_services.change_status(director, scheduled, "Conducted")
    html = client.get(reverse("activities:detail", args=[scheduled.pk]) + "?tab=documents").content.decode()
    assert "Generate Attendance" in html and "Generate Confirmation of Attendance" in html
    url = reverse("registrations:generate_attendance_form", args=[scheduled.pk, "confirmation"])
    response = client.post(url, follow=True)
    assert "was generated and saved to the documents" in response.content.decode()
    assert scheduled.documents.filter(document_type__name=settings.CONFIRMATION_OF_ATTENDANCE).count() == 1


def test_confirmation_is_a_payment_type_only_for_new_activities(scheduled, form_types) -> None:
    confirmation = DocumentType.objects.get(name=settings.CONFIRMATION_OF_ATTENDANCE)
    assert document_services.is_payment_type(confirmation)
    scheduled.end_date = settings.CONFIRMATION_DOCUMENTS_FROM
    assert document_services.type_applies(confirmation, scheduled)
    scheduled.end_date = settings.CONFIRMATION_DOCUMENTS_FROM - timedelta(days=1)
    assert not document_services.type_applies(confirmation, scheduled)
