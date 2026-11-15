"""Periods in questions, worked out by OUR code in Manila time (ASSUMPTIONS 381), never by Gemini.

`parse_period(text, now)` reads phrases like "November", "last November", "November 14", "2026-11-14", "this week",
"next month", "this year", "in 2026", "today", "upcoming", "happening now" and returns a Period with the exact dates
and a readable label ("November 1–30, 2026"). Weeks run Monday to Sunday (TBI question for item 381).

A month (or a month and day) without a year is in the current year, unless the question is clearly about the past
or the future:
- past wording (conducted, held, was, were, did, ...) and a month later than this month -> last year;
- future wording (upcoming, will, planned, scheduled, ...) and a month earlier than this month -> next year.

Pure functions: `now` is passed in (tests fix it), and only the date in Manila matters.
"""

import calendar
import re
from dataclasses import dataclass
from datetime import date, datetime, timedelta

from django.utils import timezone

RANGE, UPCOMING, NOW = "range", "upcoming", "now"

MONTHS = {
    "january": 1, "jan": 1, "february": 2, "feb": 2, "march": 3, "mar": 3, "april": 4, "apr": 4, "may": 5,
    "june": 6, "jun": 6, "july": 7, "jul": 7, "august": 8, "aug": 8, "september": 9, "sept": 9, "sep": 9,
    "october": 10, "oct": 10, "november": 11, "nov": 11, "december": 12, "dec": 12,
}
_MONTH = r"(january|february|march|april|may|june|july|august|september|october|november|december|" \
         r"jan|feb|mar|apr|jun|jul|aug|sept|sep|oct|nov|dec)\.?"
_DAY = r"(\d{1,2})(?:st|nd|rd|th)?"
_YEAR = r"(20\d{2})"

ISO_DATE = re.compile(r"\b(20\d{2})-(\d{2})-(\d{2})\b")
MONTH_DAY = re.compile(rf"\b{_MONTH}\s+{_DAY}\b(?:,?\s+{_YEAR}\b)?(?!\s*(?:am|pm|:))")
DAY_MONTH = re.compile(rf"\b{_DAY}\s+(?:of\s+)?{_MONTH}(?:'s)?(?:,?\s+{_YEAR}\b)?")
MONTH_YEAR = re.compile(rf"\b(?:(last|next|this|past|coming)\s+)?{_MONTH}(?:'s|s)?\b(?:,?\s+(?:of\s+)?{_YEAR}\b)?")
RELATIVE = re.compile(r"\b(this|next|last|past|previous|coming)\s+(week|month|year)\b")
YEAR_ONLY = re.compile(rf"\b{_YEAR}\b")
SINGLE_DAY = re.compile(r"\b(today|tomorrow|yesterday|tonight)\b")
UPCOMING_WORDS = re.compile(r"\b(upcoming|coming up|coming|future|soon|ahead|next (?:activity|activities|event|events))\b")
NOW_WORDS = re.compile(r"\b(happening now|happening right now|right now|currently|at the moment|going on now|ongoing now|"
                       r"running now|now happening|happening at the moment)\b")

PAST_CUES = re.compile(r"\b(conducted|held|was|were|did|done|happened|finished|completed|took place|had)\b")
FUTURE_CUES = re.compile(r"\b(upcoming|will|planned|plan|planning|scheduled|coming|going to|next)\b")
# "may" is also an ordinary word ("may I see ..."): it is a month only next to a date word.
_MAY_AS_MONTH = re.compile(r"\b(in|of|for|during|last|next|this|on|until|from|since|before|after)\s+may\b|\bmay\s+(\d|20\d{2})")


@dataclass(frozen=True)
class Period:
    """A period of whole days in Manila (start and end included), or UPCOMING / NOW (rules of the Activities list)."""

    start: date | None
    end: date | None
    label: str
    kind: str = RANGE


def _today(now: datetime) -> date:
    return timezone.localtime(now).date() if timezone.is_aware(now) else now.date()


def _month_name(month: int) -> str:
    return calendar.month_name[month]


def range_label(start: date, end: date) -> str:
    """November 14, 2026 / November 1–30, 2026 / November 30 – December 6, 2026 / December 28, 2026 – January 3, 2027."""
    if start == end:
        return f"{_month_name(start.month)} {start.day}, {start.year}"
    if start.year != end.year:
        return f"{_month_name(start.month)} {start.day}, {start.year} – {_month_name(end.month)} {end.day}, {end.year}"
    if start.month != end.month:
        return f"{_month_name(start.month)} {start.day} – {_month_name(end.month)} {end.day}, {end.year}"
    return f"{_month_name(start.month)} {start.day}–{end.day}, {end.year}"


def day_period(day: date) -> Period:
    return Period(day, day, range_label(day, day))


def month_period(year: int, month: int) -> Period:
    start = date(year, month, 1)
    end = date(year, month, calendar.monthrange(year, month)[1])
    return Period(start, end, range_label(start, end))


def week_period(day: date) -> Period:
    """The Monday-to-Sunday week that contains `day`."""
    start = day - timedelta(days=day.weekday())
    end = start + timedelta(days=6)
    return Period(start, end, range_label(start, end))


def year_period(year: int) -> Period:
    return Period(date(year, 1, 1), date(year, 12, 31), range_label(date(year, 1, 1), date(year, 12, 31)))


def _cue(text: str) -> str | None:
    """"past" / "future" when the question clearly points one way, else None."""
    past, future = bool(PAST_CUES.search(text)), bool(FUTURE_CUES.search(text))
    if past and not future:
        return "past"
    if future and not past:
        return "future"
    return None


def resolve_year(month: int, text: str, today: date) -> int:
    """The year of a month named without one (module docstring)."""
    cue = _cue(text)
    if cue == "past" and month > today.month:
        return today.year - 1
    if cue == "future" and month < today.month:
        return today.year + 1
    return today.year


def _month_number(word: str) -> int:
    return MONTHS[word.lower().rstrip(".")]


def _is_month(match: re.Match, text: str, group: int) -> bool:
    word = match.group(group).lower()
    return word != "may" or bool(_MAY_AS_MONTH.search(text))


def _safe_day(year: int, month: int, day: int) -> date | None:
    try:
        return date(year, month, day)
    except ValueError:
        return None


def parse_period(text: str, now: datetime) -> Period | None:
    """The first period found in the question (lowercase is not needed), or None."""
    text = " ".join((text or "").lower().replace("’", "'").split())
    today = _today(now)

    if match := ISO_DATE.search(text):
        day = _safe_day(int(match.group(1)), int(match.group(2)), int(match.group(3)))
        if day:
            return day_period(day)
    for pattern, month_group, day_group, year_group in ((MONTH_DAY, 1, 2, 3), (DAY_MONTH, 2, 1, 3)):
        for match in pattern.finditer(text):
            if not _is_month(match, text, month_group):
                continue
            month = _month_number(match.group(month_group))
            year = int(match.group(year_group)) if match.group(year_group) else resolve_year(month, text, today)
            day = _safe_day(year, month, int(match.group(day_group)))
            if day:
                return day_period(day)

    if match := SINGLE_DAY.search(text):
        offset = {"today": 0, "tonight": 0, "tomorrow": 1, "yesterday": -1}[match.group(1)]
        return day_period(today + timedelta(days=offset))

    if match := RELATIVE.search(text):
        step = {"this": 0, "coming": 1, "next": 1, "last": -1, "past": -1, "previous": -1}[match.group(1)]
        unit = match.group(2)
        if unit == "week":
            return week_period(today + timedelta(weeks=step))
        if unit == "month":
            month_index = today.year * 12 + (today.month - 1) + step
            return month_period(month_index // 12, month_index % 12 + 1)
        return year_period(today.year + step)

    for match in MONTH_YEAR.finditer(text):
        if not _is_month(match, text, 2):
            continue
        month = _month_number(match.group(2))
        modifier, year = match.group(1), match.group(3)
        if year:
            return month_period(int(year), month)
        if modifier in ("last", "past"):
            return month_period(today.year if month < today.month else today.year - 1, month)
        if modifier in ("next", "coming"):
            return month_period(today.year if month > today.month else today.year + 1, month)
        if modifier == "this":
            return month_period(today.year, month)
        return month_period(resolve_year(month, text, today), month)

    if match := YEAR_ONLY.search(text):
        return year_period(int(match.group(1)))

    if NOW_WORDS.search(text):
        return Period(None, None, "happening now", NOW)
    if UPCOMING_WORDS.search(text):
        return Period(today, None, f"upcoming (from {range_label(today, today)})", UPCOMING)
    return None


def period_words() -> re.Pattern:
    """Every pattern above, to remove period words before matching activity titles (routing)."""
    return re.compile("|".join(p.pattern for p in (ISO_DATE, MONTH_DAY, DAY_MONTH, RELATIVE, SINGLE_DAY, MONTH_YEAR, YEAR_ONLY)))
