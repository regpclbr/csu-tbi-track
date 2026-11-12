"""Certificate status rules (certificates/services.py)."""

import pytest

from activities.models import ResourcePerson
from certificates.models import Certificate
from certificates.services import CertificateStatusError, set_certificate_status
from registrations.models import EvaluationSubmission, FormTemplate, FormType

S = Certificate.Status


def move_to(certificate: Certificate, target: str, member=None) -> Certificate:
    """Walk a fresh certificate forward to `target` (Approved, then Issued)."""
    path = [S.APPROVED, S.ISSUED]
    for status in path[: path.index(target) + 1]:
        set_certificate_status(certificate, status, member=member)
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
        (S.GENERATED, S.ISSUED),  # skips Approved
        (S.GENERATED, S.GENERATED),  # not a move
        (S.APPROVED, S.APPROVED),  # not a move
    ],
)
def test_blocked_moves(certificate, submit_evaluation, start, target) -> None:
    submit_evaluation()
    if start != S.GENERATED:
        move_to(certificate, start)
    with pytest.raises(CertificateStatusError, match="Cannot move"):
        set_certificate_status(certificate, target)
    certificate.refresh_from_db()
    assert certificate.status == start


@pytest.mark.django_db
@pytest.mark.parametrize("target", [S.GENERATED, S.APPROVED, S.ISSUED])
def test_issued_is_final(certificate, submit_evaluation, target) -> None:
    submit_evaluation()
    move_to(certificate, S.ISSUED)
    with pytest.raises(CertificateStatusError, match="Cannot move"):
        set_certificate_status(certificate, target)


@pytest.mark.django_db
@pytest.mark.parametrize("old", ["pending_signoff", "for_review"])
def test_unknown_status_is_rejected(certificate, old) -> None:
    with pytest.raises(CertificateStatusError, match="Unknown"):
        set_certificate_status(certificate, old)  # Pending Sign-off no longer exists (ASSUMPTIONS 357)


# --- Approval and going back (regenerated) ---------------------------------------------


@pytest.mark.django_db
def test_approval_records_who_and_when(certificate, member) -> None:
    move_to(certificate, S.APPROVED, member=member)
    certificate.refresh_from_db()
    assert certificate.approved_by == member and certificate.approved_at is not None


@pytest.mark.django_db
def test_back_to_waiting_clears_the_approval(certificate, member) -> None:
    move_to(certificate, S.APPROVED, member=member)
    set_certificate_status(certificate, S.GENERATED)
    certificate.refresh_from_db()
    assert certificate.status == S.GENERATED and certificate.approved_by is None and certificate.approved_at is None
    move_to(certificate, S.APPROVED, member=member)  # and it can be approved again
    assert certificate.status == S.APPROVED


@pytest.mark.django_db
def test_approval_needs_both_signature_images(certificate, signatories) -> None:
    from pathlib import Path
    from django.conf import settings

    (Path(settings.MEDIA_ROOT) / signatories[1].signature_image_path.name).unlink()
    with pytest.raises(CertificateStatusError, match="Add the Director's signature first."):
        set_certificate_status(certificate, S.APPROVED)
    certificate.refresh_from_db()
    assert certificate.status == S.GENERATED


# --- Issued requirements -----------------------------------------


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
