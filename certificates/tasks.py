"""Background tasks for certificates, run by the Django-Q2 cluster (`python manage.py qcluster`).

They never raise: a failure is logged (certificate ID and error type only) and returned as the
task result, so the cluster keeps running and the certificate is retried.
"""

import logging

from django.conf import settings

from . import services

logger = logging.getLogger("certificates")

RETRY_SCHEDULE_NAME = "Retry certificate PDFs and emails"


def run_prepare_certificate(certificate_id: int) -> dict:
    """Queued after an approval or an evaluation: render the PDF (and issue a participant's)."""
    try:
        return {"certificate_id": certificate_id, "result": services.prepare_certificate(certificate_id)}
    except Exception as error:
        logger.error("Rendering certificate %s failed: %s. The daily retry will try again.", certificate_id, type(error).__name__)
        return {"certificate_id": certificate_id, "result": "failed", "reason": type(error).__name__}


def run_send_certificate(certificate_id: int, attempt: int = 1) -> dict:
    """Queued after issuing. A failed send is retried after CERTIFICATE_EMAIL_RETRY_MINUTES, then daily."""
    try:
        return {"certificate_id": certificate_id, "result": services.send_certificate(certificate_id)}
    except Exception as error:
        delays = settings.CERTIFICATE_EMAIL_RETRY_MINUTES
        if attempt <= len(delays):
            minutes = delays[attempt - 1]
            logger.warning("Sending certificate %s failed (attempt %s): %s. Retrying in %s minutes.", certificate_id, attempt, type(error).__name__, minutes)
            services.schedule_send_retry(certificate_id, attempt + 1, minutes)
        else:
            logger.error("Sending certificate %s failed (attempt %s): %s. Left for the daily retry.", certificate_id, attempt, type(error).__name__)
        return {"certificate_id": certificate_id, "result": "failed", "attempt": attempt, "reason": type(error).__name__}


def run_certificate_retries() -> dict:
    """Daily (8:30 AM Manila): render and issue what is ready, send what is Issued but unsent."""
    try:
        return services.retry_pending_certificates()
    except Exception as error:
        logger.error("The daily certificate retry crashed: %s.", type(error).__name__)
        return {"result": "failed", "reason": type(error).__name__}
