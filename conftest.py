"""Shared pytest fixtures for all apps. App-specific fixtures live in each app's tests/."""

from datetime import date, datetime, timedelta
from decimal import Decimal

import pytest
from django.utils import timezone

from accounts.models import Role, TBIMember
from activities.models import Activity, ActivityStatus, ActivityType, ParticipantCategory
from certificates.models import Certificate, CertificateSignatory, CertificateTemplate
from registrations.models import FormTemplate, FormType, Registration


@pytest.fixture
def admin_role() -> Role:
    return Role.objects.create(role_name="Admin", can_create_activity=True, can_manage_users=True)


@pytest.fixture
def staff_role() -> Role:
    return Role.objects.create(role_name="Admin Staff", can_create_activity=True, can_manage_users=False)


@pytest.fixture
def member(staff_role: Role) -> TBIMember:
    return TBIMember.objects.create_user("staff@example.com", "Staff Member", staff_role, "pass-12345")


@pytest.fixture
def activity(member: TBIMember) -> Activity:
    deadline = timezone.make_aware(datetime(2026, 11, 1, 17, 0))
    return Activity.objects.create(
        created_by=member,
        status=ActivityStatus.objects.create(name="Draft"),
        activity_type=ActivityType.objects.create(name="Workshop"),
        participant_category=ParticipantCategory.objects.create(name="Faculty"),
        title="Pitching Workshop",
        start_date=date(2026, 11, 5),
        end_date=date(2026, 11, 6),
        time_schedule="8:00 AM - 5:00 PM",
        venue="CSU Hall",
        max_slots=30,
        registration_deadline=deadline,
        evaluation_deadline=deadline + timedelta(days=14),
        budget_amount=Decimal("5000.00"),
    )


@pytest.fixture
def registration_form(activity: Activity, member: TBIMember) -> FormTemplate:
    return FormTemplate.objects.create(
        activity=activity, form_type=FormType.REGISTRATION, title="Registration", created_by=member
    )


@pytest.fixture
def registration(activity: Activity, registration_form: FormTemplate) -> Registration:
    return Registration.objects.create(
        activity=activity, template=registration_form, name="Ana Cruz", email="ana@example.com"
    )


@pytest.fixture
def certificate_template(member: TBIMember) -> CertificateTemplate:
    return CertificateTemplate.objects.create(
        name="Default design", file_path="certificate_templates/default.pdf", uploaded_by=member, is_active=True
    )


@pytest.fixture
def signatories() -> tuple[CertificateSignatory, CertificateSignatory]:
    vp = CertificateSignatory.objects.create(name="VP", position="vp", signature_image_path="signatures/vp.png")
    director = CertificateSignatory.objects.create(
        name="Director", position="director", signature_image_path="signatures/director.png"
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
            vp_signatory=vp,
            director_signatory=director,
            delivery_method=Certificate.DeliveryMethod.EMAIL,
        )

    return make
