"""Certificate status rules (certificates/services.py)."""

import pytest
from django.utils import timezone

from certificates.models import Certificate
from certificates.services import CertificateStatusError, set_certificate_status
from registrations.models import EvaluationSubmission, FormTemplate, FormType


def sign_off(certificate: Certificate) -> None:
    now = timezone.now()
    certificate.reviewed_at = now
    certificate.director_signed_at = now
    certificate.vp_signed_at = now


@pytest.fixture
def certificate(registration, make_certificate) -> Certificate:
    return make_certificate(registration)


@pytest.fixture
def submit_evaluation(registration, member):
    def submit() -> EvaluationSubmission:
        form = FormTemplate.objects.create(
            activity=registration.activity, form_type=FormType.EVALUATION, title="Evaluation", created_by=member
        )
        return EvaluationSubmission.objects.create(template=form, registration=registration)

    return submit


@pytest.mark.django_db
def test_pending_signoff_has_no_preconditions(certificate) -> None:
    set_certificate_status(certificate, Certificate.Status.PENDING_SIGNOFF)
    certificate.refresh_from_db()
    assert certificate.status == Certificate.Status.PENDING_SIGNOFF


@pytest.mark.django_db
@pytest.mark.parametrize("missing", ["reviewed_at", "director_signed_at", "vp_signed_at"])
def test_approved_requires_all_sign_off_dates(certificate, missing) -> None:
    sign_off(certificate)
    setattr(certificate, missing, None)
    with pytest.raises(CertificateStatusError, match=missing):
        set_certificate_status(certificate, Certificate.Status.APPROVED)


@pytest.mark.django_db
def test_approved_when_fully_signed(certificate) -> None:
    sign_off(certificate)
    set_certificate_status(certificate, Certificate.Status.APPROVED)
    certificate.refresh_from_db()
    assert certificate.status == Certificate.Status.APPROVED


@pytest.mark.django_db
def test_issued_requires_approved(certificate, submit_evaluation) -> None:
    submit_evaluation()
    with pytest.raises(CertificateStatusError, match="Approved"):
        set_certificate_status(certificate, Certificate.Status.ISSUED)


@pytest.mark.django_db
def test_participant_cannot_be_issued_without_evaluation(certificate) -> None:
    sign_off(certificate)
    set_certificate_status(certificate, Certificate.Status.APPROVED)
    with pytest.raises(CertificateStatusError, match="evaluation"):
        set_certificate_status(certificate, Certificate.Status.ISSUED)


@pytest.mark.django_db
def test_participant_issued_after_evaluation(certificate, submit_evaluation) -> None:
    evaluation = submit_evaluation()
    sign_off(certificate)
    set_certificate_status(certificate, Certificate.Status.APPROVED)

    set_certificate_status(certificate, Certificate.Status.ISSUED)

    certificate.refresh_from_db()
    assert certificate.status == Certificate.Status.ISSUED
    assert certificate.issued_at is not None
    assert certificate.evaluation_submission == evaluation


@pytest.mark.django_db
def test_resource_person_issued_without_evaluation(activity, certificate_template, signatories) -> None:
    from activities.models import ResourcePerson

    vp, director = signatories
    certificate = Certificate.objects.create(
        certificate_template=certificate_template,
        certificate_number="TBI-RP-0001",
        recipient_type=Certificate.RecipientType.RESOURCE_PERSON,
        resource_person=ResourcePerson.objects.create(name="Dr. Reyes"),
        activity=activity,
        vp_signatory=vp,
        director_signatory=director,
        delivery_method=Certificate.DeliveryMethod.HARD_COPY,
    )
    sign_off(certificate)
    set_certificate_status(certificate, Certificate.Status.APPROVED)
    set_certificate_status(certificate, Certificate.Status.ISSUED)
    assert certificate.status == Certificate.Status.ISSUED


@pytest.mark.django_db
def test_unknown_status_is_rejected(certificate) -> None:
    with pytest.raises(CertificateStatusError):
        set_certificate_status(certificate, "for_review")
