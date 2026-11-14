"""The certificate email. The mailer (console in development, SMTP otherwise) comes from settings.MAILERS."""

from django.conf import settings
from django.core.mail import EmailMessage

from .models import Certificate
from .pdf import long_date


def send_certificate_email(certificate: Certificate, name: str, address: str, pdf: bytes) -> None:
    """Email a participant's certificate with the PDF attached. Raises if sending fails. A reissued certificate
    (ASSUMPTIONS 364) says "Updated copy" in the subject and that it replaces the copy sent earlier."""
    activity = certificate.activity
    updated = certificate.reissued_at is not None
    subject = f"Your Certificate of Participation: {activity.title}"
    replaces = ("This is an updated copy with the current signatures. It replaces the copy we sent you earlier.\n\n"
                if updated else "")
    message = EmailMessage(
        subject=f"Updated copy: {subject}" if updated else subject,
        body=(
            f"Hello {name},\n\n"
            f"Thank you for participating in \"{activity.title}\" ({long_date(activity.end_date)}) "
            "and for completing the evaluation.\n\n"
            f"Your Certificate of Participation (No. {certificate.certificate_number}) is attached as a PDF.\n\n"
            f"{replaces}"
            f"{settings.CERTIFICATE_ORGANIZATION_NAME}\n"
        ),
        from_email=settings.DEFAULT_FROM_EMAIL,
        to=[address],
    )
    message.attach(f"{certificate.certificate_number}.pdf", pdf, "application/pdf")
    message.send()
