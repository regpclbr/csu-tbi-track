"""Business logic for certificates, sign-off and emailing.

Views stay thin and call the functions in this module.
"""

from django.utils import timezone

from registrations.models import EvaluationSubmission

from .models import Certificate

SIGN_OFF_FIELDS = ("reviewed_at", "director_signed_at", "vp_signed_at")


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

    - Approved needs reviewed_at, director_signed_at and vp_signed_at.
    - Issued needs the certificate to be Approved first and, for participants,
      a submitted evaluation. Issuing sets issued_at and links the evaluation.
    """
    status = Certificate.Status
    if new_status not in status.values:
        raise CertificateStatusError(f"Unknown certificate status: {new_status!r}.")

    if new_status == status.APPROVED:
        missing = [name for name in SIGN_OFF_FIELDS if getattr(certificate, name) is None]
        if missing:
            raise CertificateStatusError(f"Cannot approve: missing {', '.join(missing)}.")

    elif new_status == status.ISSUED:
        if certificate.status != status.APPROVED:
            raise CertificateStatusError("Only an Approved certificate can be issued.")
        if certificate.recipient_type == Certificate.RecipientType.REGISTRATION:
            evaluation = get_submitted_evaluation(certificate)
            if evaluation is None:
                raise CertificateStatusError("Cannot issue: the participant has not submitted the evaluation.")
            certificate.evaluation_submission = evaluation
        certificate.issued_at = timezone.now()

    certificate.status = new_status
    certificate.save(update_fields=["status", "evaluation_submission", "issued_at"])
    return certificate
