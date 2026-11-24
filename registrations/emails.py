"""Emails to registrants. During development the console backend prints them to the terminal.
Step 9 can replace the sending here; callers don't change."""

from django.conf import settings
from django.core.mail import send_mail

from activities.formatting import format_date, format_time_range

from .models import Registration


def _activity_lines(registration: Registration) -> str:
    activity = registration.activity
    dates = format_date(activity.start_date)
    if activity.end_date != activity.start_date:
        dates += f" to {format_date(activity.end_date)}"
    times = format_time_range(activity.start_time, activity.end_time)
    return f"Activity: {activity.title}\nDate: {dates}\nTime: {times}\nVenue: {activity.venue}\n"


def send_registration_confirmation(registration: Registration, cancel_link: str | None = None) -> None:
    """Tell the registrant they are registered, with their "I can no longer attend" link (ASSUMPTIONS 434; the task
    always passes it). It never mentions other registrants."""
    cancel = (
        "If you can no longer attend, please tell us, so we can free your slot and plan the meals:\n"
        f"{cancel_link}\n\n"
    ) if cancel_link else ""
    send_mail(
        subject=f"You are registered: {registration.activity.title}",
        message=(
            f"Hello {registration.name},\n\n"
            "You are registered for this CSU-TBI activity.\n\n"
            f"{_activity_lines(registration)}"
            f"Registered as: {registration.participant_category}\n\n"
            "On each day of the activity, scan the attendance QR code at the venue and type this email address to "
            "check in.\n\n"
            f"{cancel}"
            "Thank you!\n"
        ),
        from_email=settings.DEFAULT_FROM_EMAIL,
        recipient_list=[registration.email],
    )


def send_checkin_notice(registration: Registration, day, checked_in_at) -> None:
    """After every QR check-in (ASSUMPTIONS 434), so a person checked in by someone else who typed their email finds
    out (the proxy check-in risk, docs/TBI_QUESTIONS.md)."""
    from django.utils import timezone

    from activities.formatting import format_time

    send_mail(
        subject=f"Checked in: {registration.activity.title}",
        message=(
            f"Hello {registration.name},\n\n"
            f"You were checked in to {registration.activity.title} on {format_date(day)} at "
            f"{format_time(timezone.localtime(checked_in_at).time())}. If this wasn't you, contact the TBI.\n\n"
            f"{_activity_lines(registration)}"
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
