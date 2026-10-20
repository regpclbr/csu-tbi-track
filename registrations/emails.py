"""Emails to registrants. During development the console backend prints them to the terminal.
Step 9 can replace the sending here; callers don't change."""

from django.conf import settings
from django.core.mail import send_mail

from activities.formatting import format_date

from .models import Registration


def _activity_lines(registration: Registration) -> str:
    activity = registration.activity
    dates = format_date(activity.start_date)
    if activity.end_date != activity.start_date:
        dates += f" to {format_date(activity.end_date)}"
    return f"Activity: {activity.title}\nDate: {dates}, {activity.time_schedule}\nVenue: {activity.venue}\n"


def send_registration_confirmation(registration: Registration) -> None:
    """Tell the registrant they are registered. It never mentions other registrants."""
    send_mail(
        subject=f"You are registered: {registration.activity.title}",
        message=(
            f"Hello {registration.name},\n\n"
            "You are registered for this CSU-TBI activity.\n\n"
            f"{_activity_lines(registration)}"
            f"Registered as: {registration.participant_category}\n\n"
            "Please bring this email. Thank you!\n"
        ),
        from_email=settings.DEFAULT_FROM_EMAIL,
        recipient_list=[registration.email],
    )


def send_evaluation_link(registration: Registration, link: str) -> None:
    """Send an attendee their personal evaluation link."""
    send_mail(
        subject=f"Please evaluate: {registration.activity.title}",
        message=(
            f"Hello {registration.name},\n\n"
            "Thank you for attending. Please tell us how it went using your personal link:\n\n"
            f"{link}\n\n"
            f"{_activity_lines(registration)}\n"
            "This link is only for you; please do not share it.\n"
        ),
        from_email=settings.DEFAULT_FROM_EMAIL,
        recipient_list=[registration.email],
    )
