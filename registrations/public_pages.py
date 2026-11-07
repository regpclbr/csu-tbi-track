"""Display helpers for the public pages (phase F3): registration, evaluation and the open activities list.

Display and form parsing only. Every rule is in registrations.services (who may register or evaluate, what an
answer must be); this module only turns posted values into the shape the services take and prepares what the
templates show.
"""

import json
from datetime import UTC, datetime, timedelta

from django.conf import settings
from django.http import HttpRequest, QueryDict
from django.utils import timezone

from activities.formatting import format_time_range
from activities.models import Activity
from activities.services import activity_start_end

from . import services
from .models import FieldType, FormField, FormTemplate

# Fixed fields of the registration page, in page order (the first one with an error gets the focus).
REGISTRATION_FIELDS = ("name", "email", "contact_number", "participant_category")
EVALUATION_FIELDS = ("certificate_name", "certificate_email")

# Activity IDs this browser registered for (the success page is shown only to them).
REGISTERED_SESSION_KEY = "public_registered_activity_ids"


def _fields(form: FormTemplate | None) -> list[FormField]:
    return list(form.fields.order_by("sort_order", "field_id")) if form else []


def answers_from_post(form: FormTemplate | None, post: QueryDict) -> dict[str, object]:
    """The posted answers as services.validate_answers takes them: {"<field id>": value}.
    Checkbox fields are lists (every ticked option); unknown inputs are ignored."""
    answers: dict[str, object] = {}
    for field in _fields(form):
        name = f"field_{field.pk}"
        if field.field_type == FieldType.CHECKBOX:
            answers[str(field.pk)] = post.getlist(name)
        elif name in post:
            answers[str(field.pk)] = post.get(name)
    return answers


def first_error(order: list[str], errors: dict[str, str]) -> str | None:
    """The first key of `order` that has an error: that input gets the focus (autofocus, works without JS)."""
    return next((key for key in order if key in errors), None)


def answer_rows(form: FormTemplate | None, answers: dict, errors: dict[str, str], focus: str | None) -> list[dict]:
    """One row per form field for components/answer_field.html: the field, its options, the value(s) typed
    so far, its error and whether it gets the focus."""
    rows = []
    for field in _fields(form):
        value = answers.get(str(field.pk))
        key = f"field_{field.pk}"
        rows.append(
            {
                "field": field,
                "options": services.parse_options(field.options),
                "value": value if isinstance(value, str) else "",
                "values": value if isinstance(value, list) else [],
                "error": errors.get(key, ""),
                "autofocus": focus == key,
            }
        )
    return rows


def field_order(form: FormTemplate | None, fixed: tuple[str, ...]) -> list[str]:
    return [*fixed, *(f"field_{field.pk}" for field in _fields(form))]


def flat_errors(error_dict: dict[str, list[str]]) -> dict[str, str]:
    """ValidationError.message_dict -> one message per input (the services give one each)."""
    return {key: " ".join(messages) for key, messages in error_dict.items()}


# --- Registration success page -----------------------------------------------------------------


def remember_registration(request: HttpRequest, activity_id: int) -> None:
    ids = [i for i in request.session.get(REGISTERED_SESSION_KEY, []) if i != activity_id]
    request.session[REGISTERED_SESSION_KEY] = [*ids, activity_id][-20:]


def has_registered(request: HttpRequest, activity_id: int) -> bool:
    return activity_id in request.session.get(REGISTERED_SESSION_KEY, [])


# --- Evaluation review step --------------------------------------------------------------------


def evaluation_values(form: FormTemplate | None, post: QueryDict) -> dict:
    """The posted evaluation as services.check_evaluation / submit_evaluation take it."""
    return {
        "certificate_name": post.get("certificate_name", ""),
        "certificate_email": post.get("certificate_email", ""),
        "comments": post.get("comments", ""),
        "answers": answers_from_post(form, post),
    }


def hidden_answers(answers: dict) -> list[tuple[str, str]]:
    """(input name, value) pairs that carry the answers through the review page (one per ticked option)."""
    pairs: list[tuple[str, str]] = []
    for field_id, value in answers.items():
        for item in value if isinstance(value, list) else [value]:
            pairs.append((f"field_{field_id}", str(item)))
    return pairs


def review_rows(form: FormTemplate | None, stored: dict[FormField, str]) -> list[dict]:
    """Each question with the answer as it will be saved (from services.check_evaluation)."""
    rows = []
    for field in _fields(form):
        value = stored.get(field)
        if value is None:
            answer = "No answer"
        elif field.field_type == FieldType.CHECKBOX:
            answer = ", ".join(json.loads(value))
        elif field.field_type == FieldType.RATING:
            answer = f"{value} out of 5"
        else:
            answer = value
        rows.append({"label": field.field_label, "answer": answer, "empty": value is None})
    return rows


# --- Open activities list ----------------------------------------------------------------------


def open_activity_cards(activities: list[Activity]) -> list[dict]:
    """The cards of /register/: the activity (for the date tile and dates) and its public summary
    (services.public_activity_summary: categories, slots left, deadline; never who registered)."""
    return [{"activity": activity, "summary": services.public_activity_summary(activity)} for activity in activities]


# --- "Add to calendar" (.ics) ------------------------------------------------------------------


def _ics_text(value: str) -> str:
    """Escape a text value (RFC 5545 3.3.11): backslash, semicolon, comma and line breaks."""
    value = value.replace("\\", "\\\\").replace(";", "\\;").replace(",", "\\,")
    return value.replace("\r\n", "\\n").replace("\n", "\\n").replace("\r", "\\n")


def _fold(line: str) -> str:
    """Lines longer than 75 octets continue on the next line after a space (RFC 5545 3.1)."""
    parts, current = [], b""
    for char in line:
        encoded = char.encode("utf-8")
        if len(current) + len(encoded) > (75 if not parts else 74):
            parts.append(current.decode("utf-8"))
            current = b""
        current += encoded
    parts.append(current.decode("utf-8"))
    return "\r\n ".join(parts)


def _ics_when(activity: Activity) -> list[str]:
    """DTSTART and DTEND: timed (UTC) when the activity has times, otherwise all-day."""
    if activity.start_time is None or activity.end_time is None:
        # The end of an all-day event is exclusive: the day after the end date.
        return [f"DTSTART;VALUE=DATE:{activity.start_date:%Y%m%d}",
                f"DTEND;VALUE=DATE:{activity.end_date + timedelta(days=1):%Y%m%d}"]
    start, end = activity_start_end(activity)
    return [f"DTSTART:{start.astimezone(UTC):%Y%m%dT%H%M%SZ}", f"DTEND:{end.astimezone(UTC):%Y%m%dT%H%M%SZ}"]


def activity_ics(activity: Activity, now: datetime | None = None) -> str:
    """A calendar file for one activity: one event from the start (start date + start time) to the end (end date
    + end time), in UTC; an all-day event from the start date to the end date when the times are not set. Only
    public facts: never who registered and nothing about the person who downloads it."""
    page_url = services.registration_page_url(activity)
    host = settings.SITE_URL.split("://", 1)[-1].split("/", 1)[0].split(":", 1)[0] or "localhost"
    stamp = (now or timezone.now()).astimezone(UTC)
    description = f"Time: {format_time_range(activity.start_time, activity.end_time)}\nTechnology Business Incubator, Caraga State University\n{page_url}"
    lines = [
        "BEGIN:VCALENDAR",
        "VERSION:2.0",
        "PRODID:-//CSU-TBI Track//Activities//EN",
        "CALSCALE:GREGORIAN",
        "METHOD:PUBLISH",
        "BEGIN:VEVENT",
        f"UID:activity-{activity.pk}@{host}",
        f"DTSTAMP:{stamp:%Y%m%dT%H%M%SZ}",
        *_ics_when(activity),
        f"SUMMARY:{_ics_text(activity.title)}",
        f"LOCATION:{_ics_text(activity.venue)}",
        f"DESCRIPTION:{_ics_text(description)}",
        f"URL:{page_url}",
        "END:VEVENT",
        "END:VCALENDAR",
    ]
    return "\r\n".join(_fold(line) for line in lines) + "\r\n"
