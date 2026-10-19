"""Readable text for audit log values. One function, used for every audit row."""

from datetime import date, datetime
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
    if isinstance(value, Decimal):
        return format_money(value)
    if isinstance(value, bool):
        return "Yes" if value else "No"
    return str(value)
