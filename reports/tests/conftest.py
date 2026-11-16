"""Fixtures for dashboard and report tests: seeded lookups and small factories that write rows
directly (fast), so each test builds exactly the data its number needs."""

from datetime import date, datetime, time, timedelta
from decimal import Decimal

import pytest
from django.core.files.base import ContentFile
from django.test import Client
from django.utils import timezone

from activities.models import Activity, ActivityStatus, ActivityType, ParticipantCategory
from certificates.models import Certificate, CertificateSignatory, CertificateTemplate
from documents.models import Document, DocumentStatus, DocumentType
from registrations.models import EvaluationSubmission, FieldType, FormAnswer, FormField, FormTemplate, FormType, Registration
from conftest import _stored, image_bytes
from reports.metrics import ReportFilters

# Tests pass this as `now`: Friday, October 23, 2026, 9:00 AM Manila.
TEST_NOW = timezone.make_aware(datetime(2026, 10, 23, 9, 0))
TODAY = date(2026, 10, 23)
YEAR_2026 = ReportFilters(date(2026, 1, 1), date(2026, 12, 31))


def manila(day: date, hour: int = 9, minute: int = 0) -> datetime:
    return timezone.make_aware(datetime.combine(day, time(hour, minute)))


@pytest.fixture
def director(make_member):
    return make_member("Director")


@pytest.fixture
def login():
    def log_in(member) -> Client:
        client = Client()
        client.force_login(member)
        return client

    return log_in


@pytest.fixture
def make_activity(roles, director):
    """make_activity("Title", status="Conducted", start=date, end=date, type_name="Workshop", categories=["Faculty"])."""
    counter = {"n": 0}

    def make(title: str | None = None, status: str = ActivityStatus.CONDUCTED, start: date = date(2026, 3, 10),
             end: date | None = None, type_name: str = "Workshop", categories: tuple[str, ...] = ("Faculty",),
             max_slots: int = 100) -> Activity:
        counter["n"] += 1
        end = end or start
        activity = Activity.objects.create(
            created_by=director,
            status=ActivityStatus.objects.get(name=status),
            activity_type=ActivityType.objects.get(name=type_name),
            title=title or f"Activity {counter['n']}",
            start_date=start,
            end_date=end,
            start_time=time(8, 0), end_time=time(17, 0),
            venue="CSU Hall",
            max_slots=max_slots,
            registration_deadline=manila(start - timedelta(days=2), 17),
            evaluation_deadline=manila(end + timedelta(days=7), 17),
            budget_amount=Decimal("1000.00"),
        )
        activity.participant_categories.set(ParticipantCategory.objects.filter(name__in=categories))
        return activity

    return make


def _form(activity: Activity, form_type: str) -> FormTemplate:
    form, _ = FormTemplate.objects.get_or_create(
        activity=activity, form_type=form_type, defaults={"title": f"{activity.title} {form_type}", "created_by": activity.created_by}
    )
    return form


@pytest.fixture
def register():
    """register(activity, "Ana", attended=True, category="Faculty", email=None, startup=None) -> Registration."""

    def make(activity: Activity, name: str = "Ana Cruz", *, attended: bool = False, category: str | None = None,
             email: str | None = None, startup=None) -> Registration:
        category_row = (
            ParticipantCategory.objects.get(name=category) if category else activity.participant_categories.order_by("name").first()
        )
        email = email or f"{name.lower().replace(' ', '.')}.{activity.pk}@example.com"
        return Registration.objects.create(
            activity=activity, template=_form(activity, FormType.REGISTRATION), participant_category=category_row,
            name=name, email=email, attendance_confirmed=attended,
            confirmation_method=Registration.ConfirmationMethod.EMAIL if attended else "", startup=startup,
        )

    return make


@pytest.fixture
def evaluate():
    """evaluate(registration, {"Overall rating": 5, "Venue rating": 4}, comments="...") -> EvaluationSubmission.
    Rating fields are created on the activity's evaluation form when missing (by exact label)."""

    def make(registration: Registration, ratings: dict[str, int], comments: str = "", text_answers: dict[str, str] | None = None):
        form = _form(registration.activity, FormType.EVALUATION)
        submission = EvaluationSubmission.objects.create(
            template=form, registration=registration, comments=comments,
            certificate_name=registration.name, certificate_email=registration.email,
        )
        answers = [(label, FieldType.RATING, str(value)) for label, value in ratings.items()]
        answers += [(label, FieldType.LONG_TEXT, value) for label, value in (text_answers or {}).items()]
        for order, (label, field_type, value) in enumerate(answers):
            field, _ = FormField.objects.get_or_create(
                template=form, field_label=label, defaults={"field_type": field_type, "sort_order": order}
            )
            FormAnswer.objects.create(
                field=field, answer_source=FormAnswer.AnswerSource.EVALUATION, evaluation_submission=submission, answer_value=value
            )
        return submission

    return make


@pytest.fixture
def add_document(director):
    """add_document(activity, "Activity Design", "Approved", returned=False, certificate=None) -> Document."""

    def make(activity: Activity, type_name: str, status: str = DocumentStatus.APPROVED, *, returned: bool = False,
             certificate: Certificate | None = None) -> Document:
        reviewed = status == DocumentStatus.APPROVED or returned
        document = Document(
            activity=activity,
            document_type=DocumentType.objects.get(name=type_name),
            uploaded_by=director,
            reviewed_by=director if reviewed else None,
            reviewed_at=timezone.now() if reviewed else None,
            status=DocumentStatus.objects.get(status_name=status),
            related_certificate=certificate,
            title=type_name,
            file_name="file.pdf",
            file_extension="pdf",
            file_size_kb=1,
        )
        document.file_path.save("file.pdf", ContentFile(b"%PDF-1.4"), save=False)
        document.save()
        return document

    return make


@pytest.fixture
def design(director):
    """An active certificate template, VP and Director (images are tiny real PNGs)."""
    template = CertificateTemplate.objects.create(
        name="Design", file_path=_stored("certificate_templates/t.png", image_bytes()), uploaded_by=director, is_active=True
    )
    vp = CertificateSignatory.objects.create(name="VP", position="Vice President", default_slot="right", signature_image_path=_stored("signatures/vp.png", image_bytes()))
    head = CertificateSignatory.objects.create(
        name="Director", position="Director", default_slot="left", signature_image_path=_stored("signatures/d.png", image_bytes())
    )
    from certificates.services import fill_empty_slots_from_defaults

    fill_empty_slots_from_defaults()  # activities created before the defaults get them (ASSUMPTIONS 366)
    return template, vp, head


@pytest.fixture
def make_certificate_row(design):
    """make_certificate_row(registration=r | resource_person=p, activity=a, status=..., issued_at=..., sent_at=...)."""
    counter = {"n": 0}

    def make(*, activity: Activity, registration: Registration | None = None, resource_person=None,
             status: str = Certificate.Status.ISSUED, issued_at: datetime | None = None, sent_at: datetime | None = None) -> Certificate:
        counter["n"] += 1
        template, vp, head = design
        participant = registration is not None
        return Certificate.objects.create(
            certificate_template=template, certificate_number=f"TBI-TEST-{counter['n']:05d}",
            recipient_type=Certificate.RecipientType.REGISTRATION if participant else Certificate.RecipientType.RESOURCE_PERSON,
            registration=registration, resource_person=resource_person, activity=activity,
            right_signatory=vp, left_signatory=head, status=status, issued_at=issued_at, sent_at=sent_at,
            delivery_method=Certificate.DeliveryMethod.EMAIL if participant else Certificate.DeliveryMethod.HARD_COPY,
        )

    return make
