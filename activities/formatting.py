"""Readable text for audit log values. One function, used for every audit row."""

from datetime import date, datetime, time
from decimal import Decimal

from django.db import models
from django.utils import timezone


def format_date(value: date) -> str:
    """Oct 5, 2026"""
    return f"{value:%b} {value.day}, {value.year}"


def format_datetime(value: datetime) -> str:
    """Oct 5, 2026 5:00 PM (in Manila time)"""
    local = timezone.localtime(value) if timezone.is_aware(value) else value
    hour = local.hour % 12 or 12
    return f"{format_date(local)} {hour}:{local:%M} {'AM' if local.hour < 12 else 'PM'}"


def format_time(value: time) -> str:
    """8:00 AM"""
    return f"{value.hour % 12 or 12}:{value:%M} {'AM' if value.hour < 12 else 'PM'}"


TIME_NOT_SET = "Time not set"


def format_time_range(start: time | None, end: time | None) -> str:
    """The one display of an activity's times: "8:00 AM – 5:00 PM", or "Time not set" (old activities whose
    free-text time could not be read, migration activities/0005). Used on every page, email and sheet."""
    if start is None or end is None:
        return TIME_NOT_SET
    return f"{format_time(start)} – {format_time(end)}"


def compact_time_range(text: str) -> str:
    """format_time_range()'s text without the spaces around the dash, "8:00 AM–5:00 PM" (narrow table columns such
    as the report PDF). "Time not set" stays as it is."""
    return text.replace(" – ", "–")


def format_date_span(start: date, end: date) -> str:
    """Compact dates on one line: Nov 6, 2026 / Oct 30–31, 2026 / Oct 30 – Nov 2, 2026 / Dec 30, 2026 – Jan 2, 2027."""
    if start == end:
        return f"{start:%b} {start.day}, {start.year}"
    if start.year != end.year:
        return f"{start:%b} {start.day}, {start.year} – {end:%b} {end.day}, {end.year}"
    if start.month != end.month:
        return f"{start:%b} {start.day} – {end:%b} {end.day}, {end.year}"
    return f"{start:%b} {start.day}–{end.day}, {end.year}"


def format_money(value: Decimal) -> str:
    """₱30,000.00"""
    return f"₱{value:,.2f}"


def format_audit_value(value: object) -> str:
    """Turn any activity field value into the text stored in ACTIVITY_AUDIT_LOG.

    Lookups (status, activity type, category, resource person) are stored by name,
    never by ID. A group of categories becomes their names, sorted and joined with ", ".
    Empty values become "".
    """
    if value is None or value == "":
        return ""
    if isinstance(value, (set, frozenset, list, tuple)):  # e.g. participant categories
        return ", ".join(sorted(format_audit_value(item) for item in value))
    if isinstance(value, models.Model):
        return str(value)  # every lookup's __str__ is its name
    if isinstance(value, datetime):  # check datetime before date: a datetime is also a date
        return format_datetime(value)
    if isinstance(value, date):
        return format_date(value)
    if isinstance(value, time):
        return format_time(value)
    if isinstance(value, Decimal):
        return format_money(value)
    if isinstance(value, bool):
        return "Yes" if value else "No"
    return str(value)


SENTENCE_VALUE_LIMIT = 60  # longer values are cut in sentences (the audit log itself keeps them whole)


def _short(value: str) -> str:
    return value if len(value) <= SENTENCE_VALUE_LIMIT else value[: SENTENCE_VALUE_LIMIT - 1].rstrip() + "…"


SYSTEM_NAME = "System"


def changed_by_name(entry) -> str:
    """Who made an audit row's change: the member's name, or "System" (an automatic status change)."""
    return entry.changed_by.name if entry.changed_by_id else SYSTEM_NAME


def audit_sentence(entry) -> str:
    """One ACTIVITY_AUDIT_LOG row as a sentence, for example
    "Juan Dela Cruz changed the status of Business Model Training from Draft to Scheduled".
    Uses the stored (already readable) values; the labels are the ones activities.services writes."""
    from .services import CERTIFICATES_LABEL, DOCUMENTS_LABEL, RESOURCE_PERSON_ADDED, RESOURCE_PERSON_REMOVED, STATUS_LABEL

    who, title = changed_by_name(entry), entry.activity.title
    label, old, new = entry.field_changed, _short(entry.old_value), _short(entry.new_value)
    field = label.lower()
    if label == STATUS_LABEL and not old:
        return f"{who} created {title} as {new}"
    if label == STATUS_LABEL:
        return f"{who} changed the status of {title} from {old} to {new}"
    if label == RESOURCE_PERSON_ADDED:
        return f"{who} added {new} as a resource person to {title}"
    if label == RESOURCE_PERSON_REMOVED:
        return f"{who} removed {old} as a resource person from {title}"
    if label == DOCUMENTS_LABEL:  # ASSUMPTIONS 432: "Ana Cruz: documents of X: Activity Design "v1" deleted"
        return f"{who}: documents of {title}: {new or old}"
    if label == CERTIFICATES_LABEL and not old:  # "System: certificates of X: 3 created automatically (...)"
        return f"{who}: certificates of {title}: {new}"
    if label == CERTIFICATES_LABEL:  # a certificate deleted because its attendance was undone
        return f"{who}: certificate {old} of {title}: {new}"
    if label == "Description":
        return f"{who} changed the description of {title}"
    if not new:
        return f"{who} cleared the {field} of {title}"
    return f"{who} changed the {field} of {title} to {new}"
