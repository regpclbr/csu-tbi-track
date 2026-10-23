"""The certificate email. The mailer (console in development, SMTP otherwise) comes from settings.MAILERS."""

from django.conf import settings
from django.core.mail import EmailMessage

from .models import Certificate
from .pdf import long_date


def send_certificate_email(certificate: Certificate, name: str, address: str, pdf: bytes) -> None:
    """Email a participant's certificate with the PDF attached. Raises if sending fails."""
    activity = certificate.activity
    message = EmailMessage(
        subject=f"Your Certificate of Participation: {activity.title}",
        body=(
            f"Hello {name},\n\n"
            f"Thank you for participating in \"{activity.title}\" ({long_date(activity.end_date)}) "
            "and for completing the evaluation.\n\n"
            f"Your Certificate of Participation (No. {certificate.certificate_number}) is attached as a PDF.\n\n"
            f"{settings.CERTIFICATE_ORGANIZATION_NAME}\n"
        ),
        from_email=settings.DEFAULT_FROM_EMAIL,
        to=[address],
    )
    message.attach(f"{certificate.certificate_number}.pdf", pdf, "application/pdf")
    message.send()
