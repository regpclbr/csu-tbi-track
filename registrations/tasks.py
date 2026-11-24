"""Background tasks for registration emails, run by the Django-Q2 cluster (`python manage.py qcluster`).

Sending in the background means a mail-server problem never turns a saved registration (or the
"send evaluation links" action) into an error page. The tasks never raise: a failure is logged
(registration ID and error type only, never the address or the link) and returned as the result.
A failed email is not retried automatically; staff can resend the evaluation links on the Evaluation tab.
"""

import logging

from . import emails, services
from .models import AttendanceDay, Registration

logger = logging.getLogger("registrations")


def _registration(registration_id: int) -> Registration | None:
    return Registration.objects.select_related("activity", "participant_category").filter(pk=registration_id).first()


def run_send_registration_confirmation(registration_id: int) -> dict:
    """Queued after a registration is saved: the confirmation email."""
    try:
        registration = _registration(registration_id)
        if registration is None:
            return {"registration_id": registration_id, "result": "not found"}
        emails.send_registration_confirmation(registration, services.cancel_link(registration))
        return {"registration_id": registration_id, "result": "sent"}
    except Exception as error:
        logger.error("Registration confirmation %s was not sent: %s.", registration_id, type(error).__name__)
        return {"registration_id": registration_id, "result": "failed", "reason": type(error).__name__}


def run_send_evaluation_link(registration_id: int) -> dict:
    """Queued by send_evaluation_links (a resend) and request_evaluation_link: one attendee's personal evaluation
    link, recorded as sent or failed on the registration (services.deliver_evaluation_link). Skipped if they
    submitted the evaluation in the meantime."""
    try:
        if not Registration.objects.filter(pk=registration_id).exists():
            return {"registration_id": registration_id, "result": "not found"}
        return {"registration_id": registration_id, "result": services.deliver_evaluation_link(registration_id)}
    except Exception as error:  # e.g. the database is unreachable
        logger.error("Evaluation link for registration %s was not sent: %s.", registration_id, type(error).__name__)
        return {"registration_id": registration_id, "result": "failed", "reason": type(error).__name__}


def run_send_checkin_email(attendance_day_id: int) -> dict:
    """Queued after a QR check-in (registrations.attendance): "You were checked in to … If this wasn't you, contact
    the TBI." Never raises; a failure is logged with the ID and the error type only."""
    try:
        row = AttendanceDay.objects.select_related("registration__activity").filter(pk=attendance_day_id).first()
        if row is None:
            return {"attendance_day_id": attendance_day_id, "result": "not found"}
        emails.send_checkin_notice(row.registration, row.day, row.checked_in_at)
        return {"attendance_day_id": attendance_day_id, "result": "sent"}
    except Exception as error:
        logger.error("Check-in email %s was not sent: %s.", attendance_day_id, type(error).__name__)
        return {"attendance_day_id": attendance_day_id, "result": "failed", "reason": type(error).__name__}


def run_purge_signatures() -> dict:
    """The daily job (config.schedules, 7:45): delete the participant signatures that are due (ASSUMPTIONS 434,
    registrations.signatures.purge_signatures). Never raises."""
    from . import signatures

    try:
        return signatures.purge_signatures()
    except Exception as error:
        logger.error("Signature deletion failed: %s.", type(error).__name__)
        return {"result": "failed", "reason": type(error).__name__}
