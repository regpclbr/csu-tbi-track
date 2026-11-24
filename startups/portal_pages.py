"""Display helpers for the startup portal pages (phase F6). Display only: every rule is a service's
(registrations_for, open_activities, evaluation_state, certificates_for_startup, register_as_startup);
this module groups and words what the templates show. Read only: nothing here changes a row.
"""

from dataclasses import dataclass
from datetime import date, datetime

from django.urls import reverse
from django.utils import timezone

from activities.models import ActivityStatus
from certificates.services import certificates_for_startup
from registrations import services as registration_services

from . import services
from .models import Startup

UPCOMING_EVALUATION = "Evaluation opens after the activity."  # owner decision F6-4: instead of "not attended" before it
OVERVIEW_OPEN_LIMIT = 3
# A portal `next` must stay inside the portal (a staff page would only send the startup to the staff login).
PORTAL_PREFIX = "/startups/portal/"


def today(now: datetime | None = None) -> date:
    return timezone.localdate(now or timezone.now())


def safe_portal_next(value: str | None) -> str | None:
    """A `next` from the login form: only a portal page of this site (a path, never another host)."""
    if value and value.startswith(PORTAL_PREFIX) and not value.startswith("//") and "\\" not in value:
        return value
    return None


def registered_activity_ids(startup: Startup) -> set[int]:
    """The activities this startup has a registration for (linked, or an older public one with its email)."""
    return set(services.registrations_for(startup).values_list("activity_id", flat=True))


def open_cards(startup: Startup, now: datetime | None = None) -> list[dict]:
    """The open activities (registrations.services.open_activities, the one rule), each marked when I'm registered."""
    mine = registered_activity_ids(startup)
    return [
        {
            "activity": activity,
            "summary": registration_services.public_activity_summary(activity),
            "registered": activity.pk in mine,
        }
        for activity in registration_services.open_activities(now)
    ]


def overview(startup: Startup, now: datetime | None = None) -> dict:
    """The Overview page: my next registered activity, three counts and open activities I haven't registered for."""
    registrations = services.registrations_for(startup)
    next_registration = (
        registrations.filter(
            activity__status__name__in=(ActivityStatus.SCHEDULED, ActivityStatus.ONGOING),
            activity__end_date__gte=today(now),
        )
        .order_by("activity__start_date", "activity__activity_id")
        .first()
    )
    cards = [card for card in open_cards(startup, now) if not card["registered"]]
    return {
        "next_registration": next_registration,
        "counts": {
            "registered": registrations.count(),
            "attended": registrations.filter(attendance_confirmed=True).count(),
            "certificates": certificates_for_startup(startup).count(),
        },
        "open_cards": cards[:OVERVIEW_OPEN_LIMIT],
        "open_total": len(cards),
    }


@dataclass
class RegistrationRow:
    registration: object
    attendance: str  # the words shown
    attendance_tone: str  # badge tone
    evaluation: str  # "open" (with url), "submitted", or "closed" (with reason)
    evaluation_url: str = ""
    evaluation_reason: str = ""
    cancel_url: str = ""  # "I can no longer attend" / "I can attend after all" (ASSUMPTIONS 434), only when offered


def _row(registration, upcoming: bool, now: datetime | None) -> RegistrationRow:
    row = _base_row(registration, upcoming, now)
    state = registration_services.cancellation_state(registration, now)
    if state.kind == "can_cancel" or (state.kind == "cancelled" and state.can_restore):
        row.cancel_url = reverse("cancel_registration", args=[registration_services.cancel_token(registration)])
    if registration.cancelled_at is not None and not registration.attendance_confirmed:
        row.attendance, row.attendance_tone = "Will not attend", "neutral"
    return row


def _base_row(registration, upcoming: bool, now: datetime | None) -> RegistrationRow:
    if registration.attendance_confirmed:
        attendance, tone = "Attended", "success"
    elif upcoming:
        attendance, tone = "Not recorded yet", "neutral"
    else:
        attendance, tone = "Not marked as attended", "neutral"
    if upcoming and not registration.attendance_confirmed:
        return RegistrationRow(registration, attendance, tone, "closed", evaluation_reason=UPCOMING_EVALUATION)
    state = registration_services.evaluation_state(registration, now)
    if state.kind == "open":
        url = reverse("evaluate", args=[registration_services.evaluation_token(registration)])
        return RegistrationRow(registration, attendance, tone, "open", evaluation_url=url)
    if state.kind == "submitted":
        return RegistrationRow(registration, attendance, tone, "submitted")
    return RegistrationRow(registration, attendance, tone, "closed", evaluation_reason=state.reason or "")


def registration_rows(startup: Startup, now: datetime | None = None) -> dict:
    """My registrations split into upcoming (the activity ends today or later, Manila) and past, with attendance and
    the evaluation: the link only when evaluation_state() says it is open, otherwise its reason."""
    current = today(now)
    upcoming, past = [], []
    registrations = services.registrations_for(startup).select_related("activity__activity_type")
    for registration in registrations.order_by("activity__start_date", "registration_id"):
        is_upcoming = registration.activity.end_date >= current
        (upcoming if is_upcoming else past).append(_row(registration, is_upcoming, now))
    past.reverse()  # most recent first
    return {"upcoming": upcoming, "past": past}
