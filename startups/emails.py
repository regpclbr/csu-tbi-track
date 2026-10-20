"""Emails to startups. Development uses the console backend (printed in the terminal)."""

from django.conf import settings
from django.core.mail import send_mail

from .models import Startup


def send_set_password_email(startup: Startup, link: str, hours: int) -> None:
    """The "set your password" link. The link itself is the secret: it is never logged."""
    send_mail(
        subject="Set your CSU-TBI Track password",
        message=(
            f"Hello {startup.name},\n\n"
            "The CSU-TBI team invites you to the CSU-TBI Track startup portal.\n"
            "Open this link to choose your password:\n\n"
            f"{link}\n\n"
            f"The link works once and expires in {hours} hours.\n"
            f"After setting your password, sign in with your email: {startup.email}\n"
        ),
        from_email=settings.DEFAULT_FROM_EMAIL,
        recipient_list=[startup.email],
    )
