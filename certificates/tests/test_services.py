"""Certificate status rules (certificates/services.py)."""

import pytest
from django.utils import timezone

from activities.models import ResourcePerson
from certificates.models import Certificate
from certificates.services import CertificateStatusError, set_certificate_status
from registrations.models import EvaluationSubmission, FormTemplate, FormType

S = Certificate.Status


def sign_off(certificate: Certificate) -> None:
    now = timezone.now()
    certificate.reviewed_at = now
    certificate.director_signed_at = now
    certificate.vp_signed_at = now


def move_to(certificate: Certificate, target: str) -> Certificate:
    """Walk a fresh certificate forward to `target`, signing it off on the way."""
    path = [S.PENDING_SIGNOFF, S.APPROVED, S.ISSUED]
    for status in path[: path.index(target) + 1]:
        if status == S.APPROVED:
            sign_off(certificate)
        set_certificate_status(certificate, status)
    return certificate


@pytest.fixture
def certificate(registration, make_certificate) -> Certificate:
    return make_certificate(registration)


@pytest.fixture
def submit_evaluation(registration, member):
    def submit() -> EvaluationSubmission:
        form = FormTemplate.objects.create(
            activity=registration.activity, form_type=FormType.EVALUATION, title="Evaluation", created_by=member
        )
        return EvaluationSubmission.objects.create(
            template=form, registration=registration, certificate_name="Ana Cruz", certificate_email="ana@example.com"
        )

    return submit


# --- Allowed and blocked moves ---------------------------------------------


@pytest.mark.django_db
def test_forward_moves_one_step_at_a_time(certificate, submit_evaluation) -> None:
    submit_evaluation()
    move_to(certificate, S.ISSUED)
    certificate.refresh_from_db()
    assert certificate.status == S.ISSUED
    assert certificate.issued_at is not None


@pytest.mark.django_db
@pytest.mark.parametrize(
    "start, target",
    [
        (S.GENERATED, S.APPROVED),  # skips Pending Sign-off
        (S.GENERATED, S.ISSUED),
        (S.GENERATED, S.GENERATED),  # not a move
        (S.PENDING_SIGNOFF, S.ISSUED),  # skips Approved
        (S.APPROVED, S.PENDING_SIGNOFF),  # backward
        (S.APPROVED, S.GENERATED),  # backward
    ],
)
def test_blocked_moves(certificate, submit_evaluation, start, target) -> None:
    submit_evaluation()
    if start != S.GENERATED:
        move_to(certificate, start)
    sign_off(certificate)
    with pytest.raises(CertificateStatusError, match="Cannot move"):
        set_certificate_status(certificate, target)
    certificate.refresh_from_db()
    assert certificate.status == start


@pytest.mark.django_db
@pytest.mark.parametrize("target", [S.GENERATED, S.PENDING_SIGNOFF, S.APPROVED, S.ISSUED])
def test_issued_is_final(certificate, submit_evaluation, target) -> None:
    submit_evaluation()
    move_to(certificate, S.ISSUED)
    with pytest.raises(CertificateStatusError, match="Cannot move"):
        set_certificate_status(certificate, target)


@pytest.mark.django_db
def test_unknown_status_is_rejected(certificate) -> None:
    with pytest.raises(CertificateStatusError, match="Unknown"):
        set_certificate_status(certificate, "for_review")


# --- Rejection --------------------------------------------------------------


@pytest.mark.django_db
def test_reject_moves_back_to_generated_and_clears_sign_off(certificate) -> None:
    set_certificate_status(certificate, S.PENDING_SIGNOFF)
    sign_off(certificate)
    certificate.save()

    set_certificate_status(certificate, S.GENERATED)

    certificate.refresh_from_db()
    assert certificate.status == S.GENERATED
    assert certificate.reviewed_at is None
    assert certificate.director_signed_at is None
    assert certificate.vp_signed_at is None


@pytest.mark.django_db
def test_rejected_certificate_can_be_sent_for_sign_off_again(certificate) -> None:
    set_certificate_status(certificate, S.PENDING_SIGNOFF)
    set_certificate_status(certificate, S.GENERATED)
    set_certificate_status(certificate, S.PENDING_SIGNOFF)
    assert certificate.status == S.PENDING_SIGNOFF


# --- Approved and Issued requirements -----------------------------------------


@pytest.mark.django_db
@pytest.mark.parametrize("missing", ["reviewed_at", "director_signed_at", "vp_signed_at"])
def test_approved_requires_all_sign_off_dates(certificate, missing) -> None:
    set_certificate_status(certificate, S.PENDING_SIGNOFF)
    sign_off(certificate)
    setattr(certificate, missing, None)
    with pytest.raises(CertificateStatusError, match=missing):
        set_certificate_status(certificate, S.APPROVED)


@pytest.mark.django_db
def test_participant_cannot_be_issued_without_evaluation(certificate) -> None:
    move_to(certificate, S.APPROVED)
    with pytest.raises(CertificateStatusError, match="evaluation"):
        set_certificate_status(certificate, S.ISSUED)


@pytest.mark.django_db
def test_issuing_links_the_participants_evaluation(certificate, submit_evaluation) -> None:
    evaluation = submit_evaluation()
    move_to(certificate, S.ISSUED)
    certificate.refresh_from_db()
    assert certificate.evaluation_submission == evaluation


@pytest.mark.django_db
def test_resource_person_issued_without_evaluation(activity, certificate_template, signatories) -> None:
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
    move_to(certificate, S.ISSUED)
    assert certificate.status == S.ISSUED
