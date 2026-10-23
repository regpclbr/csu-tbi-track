"""Background tasks for registration emails, run by the Django-Q2 cluster (`python manage.py qcluster`).

Sending in the background means a mail-server problem never turns a saved registration (or the
"send evaluation links" action) into an error page. The tasks never raise: a failure is logged
(registration ID and error type only, never the address or the link) and returned as the result.
A failed email is not retried automatically; staff can send the evaluation links again.
"""

import logging

from . import emails, services
from .models import EvaluationSubmission, Registration

logger = logging.getLogger("registrations")


def _registration(registration_id: int) -> Registration | None:
    return Registration.objects.select_related("activity", "participant_category").filter(pk=registration_id).first()


def run_send_registration_confirmation(registration_id: int) -> dict:
    """Queued after a registration is saved: the confirmation email."""
    try:
        registration = _registration(registration_id)
        if registration is None:
            return {"registration_id": registration_id, "result": "not found"}
        emails.send_registration_confirmation(registration)
        return {"registration_id": registration_id, "result": "sent"}
    except Exception as error:
        logger.error("Registration confirmation %s was not sent: %s.", registration_id, type(error).__name__)
        return {"registration_id": registration_id, "result": "failed", "reason": type(error).__name__}


def run_send_evaluation_link(registration_id: int) -> dict:
    """Queued by send_evaluation_links: one attendee's personal evaluation link.
    Skipped if they submitted the evaluation in the meantime."""
    try:
        registration = _registration(registration_id)
        if registration is None:
            return {"registration_id": registration_id, "result": "not found"}
        if EvaluationSubmission.objects.filter(registration=registration).exists():
            return {"registration_id": registration_id, "result": "already submitted"}
        emails.send_evaluation_link(registration, services.evaluation_link(registration))
        return {"registration_id": registration_id, "result": "sent"}
    except Exception as error:
        logger.error("Evaluation link for registration %s was not sent: %s.", registration_id, type(error).__name__)
        return {"registration_id": registration_id, "result": "failed", "reason": type(error).__name__}
