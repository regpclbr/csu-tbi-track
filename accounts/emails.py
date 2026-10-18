"""Emails sent to staff members.

For now Django's console email backend prints these to the terminal (not to the log).
Step 9 replaces the body of `send_invitation_email` with real sending; callers don't change.
"""

from django.conf import settings
from django.core.mail import send_mail

from .models import TBIMember


def send_invitation_email(member: TBIMember, temporary_password: str) -> None:
    """Send a new member their login email and one-time temporary password.

    The temporary password exists only in this email. It is never stored in plain
    text and never written to the log.
    """
    send_mail(
        subject="Your CSU-TBI Track account",
        message=(
            f"Hello {member.name},\n\n"
            "An account has been created for you in CSU-TBI Track.\n\n"
            f"Email: {member.email}\n"
            f"Temporary password: {temporary_password}\n\n"
            "You will be asked to choose a new password the first time you sign in.\n"
        ),
        from_email=settings.DEFAULT_FROM_EMAIL,
        recipient_list=[member.email],
    )
