"""Emails sent to staff members, through the project's mailer (settings.MAILERS: printed in the
terminal while DEBUG is on, real SMTP otherwise). Temporary passwords exist only in these emails:
they are never stored in plain text and never written to the log.
"""

from django.conf import settings
from django.core.mail import send_mail
from django.urls import reverse

from .models import TBIMember


def login_url() -> str:
    """The staff sign-in page on the public address (SITE_URL, never the request's Host)."""
    return settings.SITE_URL + reverse("accounts:login")


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
            f"Sign in here: {login_url()}\n"
            f"Email: {member.email}\n"
            f"Temporary password: {temporary_password}\n\n"
            "You will be asked to choose a new password the first time you sign in.\n"
        ),
        from_email=settings.DEFAULT_FROM_EMAIL,
        recipient_list=[member.email],
    )


def send_password_reset_email(member: TBIMember, temporary_password: str) -> None:
    """A System Administrator reset the member's password: the new temporary password (only in this email)."""
    send_mail(
        subject="Your CSU-TBI Track password was reset",
        message=(
            f"Hello {member.name},\n\n"
            "The System Administrator reset the password of your CSU-TBI Track account.\n\n"
            f"Email: {member.email}\n"
            f"Temporary password: {temporary_password}\n\n"
            "You will be asked to choose a new password the next time you sign in.\n"
            "If you did not ask for this, please tell the TBI System Administrator.\n"
        ),
        from_email=settings.DEFAULT_FROM_EMAIL,
        recipient_list=[member.email],
    )


def send_password_changed_email(member: TBIMember) -> None:
    """The member changed their own password on My profile (ASSUMPTIONS 390). Never contains the password. Printed in
    the terminal while emails are not really sent (settings.MAILERS / EMAIL_REAL_SENDING)."""
    send_mail(
        subject="Your CSU-TBI Track password was changed",
        message=(
            f"Hello {member.name},\n\n"
            "The password of your CSU-TBI Track account was just changed on your My profile page.\n\n"
            "If this was you, there is nothing else to do.\n"
            "If it was not you, please tell the TBI System Administrator right away so they can reset your password.\n\n"
            f"Sign in here: {login_url()}\n"
        ),
        from_email=settings.DEFAULT_FROM_EMAIL,
        recipient_list=[member.email],
    )
