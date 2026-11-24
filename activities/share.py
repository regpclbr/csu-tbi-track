"""The Share card of an activity page: its public links and their QR codes (display only, no rules of its own).

- Draft: no card (Draft is never public).
- Scheduled: the registration link, Open / Closed from registrations.services.registration_closed_reason.
- Ongoing / Conducted: the general evaluation link, shown only while registrations.services.evaluation_request_state
  is open; otherwise when it opens or that it has closed.
Every link starts with settings.SITE_URL (registrations.services), never the Host of the request.
"""

import io
from datetime import datetime

import segno

from registrations import services as registration_services

from . import services as activity_services
from .models import Activity, ActivityStatus

EVALUATION_STATUSES = (ActivityStatus.ONGOING, ActivityStatus.CONDUCTED)  # the evaluation section instead of registration
QR_SCALE = 12  # pixels per module: about 500 px for a typical link, sharp enough for an A4 poster
QR_BORDER = 4  # the quiet zone the QR standard asks for (in modules)


BUILD_FORM_FIRST = ("Build the registration form first: create it on the Forms tab, check its fields and click "
                    "“Open registration”. The registration link and QR code appear then.")


def build_form_first(activity: Activity, reason: str | None) -> bool:
    """ASSUMPTIONS 409: an activity created with its documents shows no registration link or QR code while its
    registration form is missing or not marked ready (older activities keep showing the link with the reason)."""
    return (activity.creation_documents_required and activity.status.name == ActivityStatus.SCHEDULED
            and reason == registration_services.REGISTRATION_FORM_NOT_READY)


def share_card(activity: Activity, registration: dict, now: datetime | None = None) -> dict | None:
    """What the Share card shows; None for Draft. `registration` is activities.pages.registration_summary().
    `switch` (Scheduled only): the hand switch offered, "close" or "reopen" (services.registration_switch,
    ASSUMPTIONS 427); the template shows it only to members who can create activities."""
    status = activity.status.name
    if status == ActivityStatus.SCHEDULED:
        switch = activity_services.registration_switch(activity)
        if build_form_first(activity, registration["reason"]):
            return {"registration": {"url": None, "open": False, "reason": BUILD_FORM_FIRST, "build_form_first": True,
                                     "switch": switch}}
        return {"registration": {"url": registration_services.registration_page_url(activity),
                                 "open": registration["open"], "reason": registration["reason"], "switch": switch}}
    if status in EVALUATION_STATUSES:
        state = registration_services.evaluation_request_state(activity, now)
        return {"evaluation": {"url": registration_services.evaluation_request_url(activity),
                               "open": state.kind == "open", "reason": state.reason}}
    return None  # Draft


def qr_url(activity: Activity, kind: str, now: datetime | None = None) -> str | None:
    """The link a QR code may show: only a link the Share card shows (None otherwise, answered with 404)."""
    status = activity.status.name
    if kind == "registration" and status == ActivityStatus.SCHEDULED:
        if build_form_first(activity, registration_services.registration_closed_reason(activity, now)):
            return None  # ASSUMPTIONS 409: no QR code before the registration form is ready
        return registration_services.registration_page_url(activity)
    if kind == "evaluation" and status in EVALUATION_STATUSES:
        if registration_services.evaluation_request_state(activity, now).kind == "open":
            return registration_services.evaluation_request_url(activity)
    return None


def qr_png(url: str) -> bytes:
    """A PNG QR code of `url` (segno, pure Python; error correction level M)."""
    buffer = io.BytesIO()
    segno.make(url, error="m", micro=False).save(buffer, kind="png", scale=QR_SCALE, border=QR_BORDER)
    return buffer.getvalue()
