"""Small display filters for the templates ({% load ui %}). Display only: no business rules here."""

from datetime import date, datetime

from django import template
from django.utils import timezone

from activities.formatting import format_datetime, format_time_range

register = template.Library()


@register.filter
def number(value) -> str:
    """1240 -> "1,240"; None -> "–"."""
    if value is None or value == "":
        return "–"
    return f"{value:,}"


@register.filter
def percent(value) -> str:
    """A rate stored as a fraction (0.857) -> "86%"; None (nothing to divide by) -> "–"."""
    if value is None:
        return "–"
    return f"{round(value * 100)}%"


@register.filter
def rating(value) -> str:
    """4.567 -> "4.6"; None -> "–"."""
    if value is None:
        return "–"
    return f"{value:.1f}"


@register.filter
def short_date(value: date | datetime | None) -> str:
    """Oct 5 (a date-time is shown in Manila time)."""
    if not value:
        return ""
    if isinstance(value, datetime):
        value = timezone.localtime(value)
    return f"{value:%b} {value.day}"


@register.filter
def long_date(value: date | None) -> str:
    """Friday, 23 October 2026 (as in the design's top bar)."""
    if not value:
        return ""
    return f"{value:%A}, {value.day} {value:%B %Y}"


@register.filter
def relative_time(value: datetime | None, now: datetime | None = None) -> str:
    """"just now", "10 min ago", "1 hour ago", "3 days ago", then the date (Oct 5, 2026)."""
    if not value:
        return ""
    seconds = int(((now or timezone.now()) - value).total_seconds())
    if seconds < 60:
        return "just now"
    minutes = seconds // 60
    if minutes < 60:
        return f"{minutes} min ago"
    hours = minutes // 60
    if hours < 24:
        return "1 hour ago" if hours == 1 else f"{hours} hours ago"
    days = hours // 24
    if days < 7:
        return "yesterday" if days == 1 else f"{days} days ago"
    local = timezone.localtime(value)
    return f"{local:%b} {local.day}, {local.year}"


@register.filter
def date_tile(activity) -> dict:
    """The month and day(s) of an activity's date tile: OCT / 28, NOV / 4–5, or OCT–NOV / 30–2."""
    start, end = activity.start_date, activity.end_date
    if start == end:
        return {"month": f"{start:%b}".upper(), "days": str(start.day)}
    if (start.year, start.month) == (end.year, end.month):
        return {"month": f"{start:%b}".upper(), "days": f"{start.day}–{end.day}"}
    return {"month": f"{start:%b}–{end:%b}".upper(), "days": f"{start.day}–{end.day}"}


@register.filter
def date_span(activity) -> str:
    """Compact dates on one line: Nov 6, 2026 / Oct 30–31, 2026 / Oct 30 – Nov 2, 2026 / Dec 30, 2026 – Jan 2, 2027."""
    start, end = activity.start_date, activity.end_date
    if start == end:
        return f"{start:%b} {start.day}, {start.year}"
    if start.year != end.year:
        return f"{start:%b} {start.day}, {start.year} – {end:%b} {end.day}, {end.year}"
    if start.month != end.month:
        return f"{start:%b} {start.day} – {end:%b} {end.day}, {end.year}"
    return f"{start:%b} {start.day}–{end.day}, {end.year}"


@register.filter
def time_range(activity) -> str:
    """An activity's times: "8:00 AM – 5:00 PM" or "Time not set" (activities.formatting.format_time_range)."""
    return format_time_range(activity.start_time, activity.end_time)


@register.filter
def date_time(value: datetime | None) -> str:
    """Oct 5, 2026 5:00 PM in Manila time (activities.formatting.format_datetime, as the services' messages)."""
    return format_datetime(value) if value else ""
