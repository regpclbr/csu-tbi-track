"""Business logic for certificates, sign-off and emailing.

Views stay thin and call the functions in this module.
"""

from django.utils import timezone

from registrations.models import EvaluationSubmission

from .models import Certificate

Status = Certificate.Status

SIGN_OFF_FIELDS = ("reviewed_at", "director_signed_at", "vp_signed_at")

# The only status moves allowed. Forward one step at a time, plus one backward move:
# Pending Sign-off -> Generated when the reviewer rejects the certificate.
# Issued is final: a wrong certificate is replaced by a new one with a new number.
ALLOWED_MOVES: dict[str, set[str]] = {
    Status.GENERATED: {Status.PENDING_SIGNOFF},
    Status.PENDING_SIGNOFF: {Status.APPROVED, Status.GENERATED},
    Status.APPROVED: {Status.ISSUED},
    Status.ISSUED: set(),
}


class CertificateStatusError(Exception):
    """Raised when a certificate cannot move to the requested status."""


def get_submitted_evaluation(certificate: Certificate) -> EvaluationSubmission | None:
    """Return the participant's submitted evaluation, or None if there isn't one."""
    if certificate.evaluation_submission_id:
        return certificate.evaluation_submission
    if certificate.registration_id is None:
        return None
    return EvaluationSubmission.objects.filter(registration_id=certificate.registration_id).first()


def set_certificate_status(certificate: Certificate, new_status: str) -> Certificate:
    """Move a certificate to `new_status` after checking the rules, then save it.

    - Only the moves in ALLOWED_MOVES are possible.
    - Approved needs reviewed_at, director_signed_at and vp_signed_at.
    - Rejecting (Pending Sign-off -> Generated) clears those three dates.
    - Issued needs, for participants, a submitted evaluation. Issuing sets
      issued_at and links the evaluation to the certificate.
    """
    if new_status not in Status.values:
        raise CertificateStatusError(f"Unknown certificate status: {new_status!r}.")
    if new_status not in ALLOWED_MOVES[certificate.status]:
        raise CertificateStatusError(
            f"Cannot move a certificate from {Status(certificate.status).label} to {Status(new_status).label}."
        )

    if new_status == Status.APPROVED:
        missing = [name for name in SIGN_OFF_FIELDS if getattr(certificate, name) is None]
        if missing:
            raise CertificateStatusError(f"Cannot approve: missing {', '.join(missing)}.")

    elif new_status == Status.GENERATED:  # rejected by the reviewer
        for name in SIGN_OFF_FIELDS:
            setattr(certificate, name, None)

    elif new_status == Status.ISSUED:
        if certificate.recipient_type == Certificate.RecipientType.REGISTRATION:
            evaluation = get_submitted_evaluation(certificate)
            if evaluation is None:
                raise CertificateStatusError("Cannot issue: the participant has not submitted the evaluation.")
            certificate.evaluation_submission = evaluation
        certificate.issued_at = timezone.now()

    certificate.status = new_status
    certificate.save(update_fields=["status", "evaluation_submission", "issued_at", *SIGN_OFF_FIELDS])
    return certificate
