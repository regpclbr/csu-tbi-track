"""Reading the old free-text ACTIVITY.time_schedule ("8:00 AM - 5:00 PM") into start and end times.

Used by migration activities/0005 (and its reverse) only. Keep it stable: a migration must give the same
result every time it runs. An unreadable text gives None, and the migration then leaves both times empty.
"""

import re
from datetime import time

# "8:00 AM - 5:00 PM", "8:00 AM – 5:00 PM", "8:00 AM — 5:00 PM", "8:00 AM to 5:00 PM"
_SEPARATOR = re.compile(r"\s*(?:-|–|—|\bto\b)\s*", re.IGNORECASE)
# "8", "8:30", "08:30", "8 AM", "8:30am", "8:30 p.m.", "17:00"
_TIME = re.compile(r"^(?P<hour>\d{1,2})(?::(?P<minute>\d{2}))?\s*(?:(?P<meridiem>[ap])\.?\s*m\.?)?$", re.IGNORECASE)


def _part(text: str) -> tuple[int, int, str | None] | None:
    """(hour, minute, "a"/"p"/None) of one side, or None when it is not a time."""
    match = _TIME.match(text.strip())
    if not match:
        return None
    hour, minute = int(match["hour"]), int(match["minute"] or 0)
    meridiem = match["meridiem"].lower() if match["meridiem"] else None
    if minute > 59 or (meridiem and not 1 <= hour <= 12) or (not meridiem and hour > 23):
        return None
    return hour, minute, meridiem


def _to_time(hour: int, minute: int, meridiem: str | None) -> time:
    if meridiem == "a":
        hour = 0 if hour == 12 else hour
    elif meridiem == "p":
        hour = 12 if hour == 12 else hour + 12
    return time(hour, minute)


def parse_time_schedule(text: str | None) -> tuple[time, time] | None:
    """(start, end) from a text such as "8:00 AM - 5:00 PM", "8:00-17:00" or "9 to 11:30 AM"; None if unreadable.

    - The two sides are separated by "-", "–", "—" or "to".
    - Each side is AM/PM or 24-hour. When only one side has AM/PM, the other side takes the same one, or the
      opposite one if that is the only way the end comes after the start ("10 - 2 PM" = 10:00 AM to 2:00 PM).
    - The end must come after the start; otherwise the text is unreadable (e.g. "9-5": 9:00 to 5:00).
    """
    sides = _SEPARATOR.split((text or "").strip())
    if len(sides) != 2:
        return None
    first, second = _part(sides[0]), _part(sides[1])
    if first is None or second is None:
        return None
    candidates: list[tuple[time, time]] = []
    if (first[2] is None) == (second[2] is None):  # both or neither have AM/PM
        candidates.append((_to_time(*first), _to_time(*second)))
    elif first[2] is None:
        other = "a" if second[2] == "p" else "p"
        if first[0] <= 12:
            candidates += [(_to_time(first[0], first[1], m), _to_time(*second)) for m in (second[2], other)]
        candidates.append((_to_time(*first), _to_time(*second)))  # a 24-hour start, e.g. "13:00 - 5 PM"
    else:
        other = "a" if first[2] == "p" else "p"
        if second[0] <= 12:
            candidates += [(_to_time(*first), _to_time(second[0], second[1], m)) for m in (first[2], other)]
        candidates.append((_to_time(*first), _to_time(*second)))
    return next(((start, end) for start, end in candidates if end > start), None)


def time_schedule_text(start: time | None, end: time | None) -> str:
    """The old text form, rebuilt when the migration is reversed: "8:00 AM - 5:00 PM" (or "" without times)."""
    if start is None or end is None:
        return ""

    def text(value: time) -> str:
        return f"{value.hour % 12 or 12}:{value:%M} {'AM' if value.hour < 12 else 'PM'}"

    return f"{text(start)} - {text(end)}"
