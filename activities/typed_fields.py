"""Typed dates and times on the activity form (ASSUMPTIONS 422, NAVIGATU UAT, the TBI Director).

Conversion only: every rule about the values (not in the past, end after start, ...) stays in activities.services.

- Date: one text box. Accepted: "June 5, 2026", "Jun 5 2026", "5 June 2026", "2026-06-05", "06/05/2026" (month/day/year,
  as in the Philippines). The page adds month suggestions and a calendar button (static/js/typed_dates.js); without
  JavaScript the box still works.
- Time: a text box "hh:mm" and an AM/PM choice. 1-12 o'clock needs AM or PM; 00:xx and 13:xx-23:xx are 24-hour times
  (an AM/PM that contradicts them is refused). Accepted as typed: 11:00, 1100, 1:30, 130, 9, 12:15, "7:30 pm". The page
  rewrites nothing while typing (only the colon after 4 digits) and tidies the box when it is left (ASSUMPTIONS 432).
- Deadline: a date box + a time box + AM/PM, combined into a Manila date-time.
Each field's error says what to type, for example "Enter a valid time, for example 07:30 AM.".
"""

import re
from datetime import date, datetime, time

from django import forms
from django.core.exceptions import ValidationError
from django.utils import timezone

DATE_FORMATS = ["%Y-%m-%d", "%m/%d/%Y", "%B %d, %Y", "%B %d %Y", "%b %d, %Y", "%b %d %Y", "%d %B %Y", "%d %b %Y",
                "%b. %d, %Y"]
AM, PM = "AM", "PM"
PERIOD_CHOICES = [("", "AM/PM"), (AM, "AM"), (PM, "PM")]

INVALID_DATE = "Enter a valid date, for example June 5, 2026 or 06/05/2026."
INVALID_TIME = "Enter a valid time, for example 07:30 AM."
CHOOSE_PERIOD = "Choose AM or PM."
TIME_PATTERN = re.compile(r"^\s*(\d{1,2})(?::?(\d{2}))?\s*([AaPp]\.?[Mm]\.?)?\s*$")


def format_typed_date(value: date | None) -> str:
    """"June 5, 2026" (what the box shows; also accepted back)."""
    return f"{value:%B} {value.day}, {value.year}" if value else ""


def parse_typed_date(text: str) -> date:
    """The typed date, or ValidationError(INVALID_DATE). Spaces are tidied; month names in any case."""
    cleaned = " ".join(str(text or "").replace(",", ", ").split()).replace(" ,", ",")
    for pattern in DATE_FORMATS:
        try:
            return datetime.strptime(cleaned, pattern).date()
        except ValueError:
            continue
    raise ValidationError(INVALID_DATE, code="invalid")


def split_time(value: time | None) -> tuple[str, str]:
    """(hh:mm on a 12-hour clock, AM/PM) for showing a stored time: 14:30 -> ("02:30", "PM"), 00:15 -> ("12:15", "AM")."""
    if value is None:
        return "", ""
    hour = value.hour % 12 or 12
    return f"{hour:02d}:{value.minute:02d}", PM if value.hour >= 12 else AM


def parse_typed_time(text: str, period: str = "") -> time:
    """The time from the box and the AM/PM choice (a period typed in the box also counts), or ValidationError with a
    clear message. 1-12 needs AM/PM; 0 and 13-23 are 24-hour times; minutes 00-59."""
    match = TIME_PATTERN.match(str(text or ""))
    if not match:
        raise ValidationError(INVALID_TIME, code="invalid")
    hour, minute = int(match.group(1)), int(match.group(2) or 0)
    typed_period = (match.group(3) or "").replace(".", "").upper()
    period = (typed_period or str(period or "")).upper()
    if minute > 59 or hour > 23 or period not in ("", AM, PM):
        raise ValidationError(INVALID_TIME, code="invalid")
    if hour == 0 or hour > 12:  # a 24-hour time
        if period == AM and hour > 12 or period == PM and hour == 0:
            raise ValidationError(f"{hour:02d}:{minute:02d} is a 24-hour time: leave AM/PM empty or type it as "
                                  f"{hour % 12 or 12:02d}:{minute:02d}.", code="invalid")
        return time(hour, minute)
    if not period:
        raise ValidationError(CHOOSE_PERIOD, code="period")
    return time(hour % 12 + (12 if period == PM else 0), minute)


class TypedDateInput(forms.TextInput):
    """The date box (rendered by components/typed_date.html)."""

    typed_kind = "date"

    def format_value(self, value):
        return format_typed_date(value) if isinstance(value, date) else super().format_value(value)


class TypedDateField(forms.DateField):
    widget = TypedDateInput
    default_error_messages = {"invalid": INVALID_DATE}

    def prepare_value(self, value):
        return format_typed_date(value) if isinstance(value, date) else value

    def to_python(self, value):
        if value in self.empty_values:
            return None
        if isinstance(value, datetime):
            return value.date()
        if isinstance(value, date):
            return value
        return parse_typed_date(value)


class TypedTimeWidget(forms.MultiWidget):
    """hh:mm box (name = the field's name) + AM/PM select (<name>_period)."""

    typed_kind = "time"

    def __init__(self, attrs=None):
        super().__init__({"": forms.TextInput(), "period": forms.Select(choices=PERIOD_CHOICES)}, attrs)

    def decompress(self, value):
        return list(split_time(value)) if isinstance(value, time) else ["", ""]


class TypedTimeField(forms.MultiValueField):
    widget = TypedTimeWidget

    def __init__(self, **kwargs):
        kwargs.setdefault("require_all_fields", False)
        super().__init__((forms.CharField(required=False), forms.CharField(required=False)), **kwargs)

    def prepare_value(self, value):
        return list(split_time(value)) if isinstance(value, time) else value

    def compress(self, data_list):
        text, period = (list(data_list) + ["", ""])[:2]
        if not str(text or "").strip():
            if self.required:
                raise ValidationError(self.error_messages["required"], code="required")
            return None
        return parse_typed_time(text, period)


class TypedDateTimeWidget(forms.MultiWidget):
    """Date box (name = the field's name) + time box (<name>_time) + AM/PM (<name>_period), in Manila time."""

    typed_kind = "datetime"

    def __init__(self, attrs=None):
        super().__init__({"": TypedDateInput(), "time": forms.TextInput(),
                          "period": forms.Select(choices=PERIOD_CHOICES)}, attrs)

    def decompress(self, value):
        return _datetime_parts(value)


def _datetime_parts(value) -> list[str]:
    if not isinstance(value, datetime):
        return ["", "", ""]
    local = timezone.localtime(value) if timezone.is_aware(value) else value
    return [format_typed_date(local.date()), *split_time(local.time())]


class TypedDateTimeField(forms.MultiValueField):
    """A Manila date-time from a date box, a time box and AM/PM. Optional when required=False (both boxes empty =
    None); one box filled without the other is refused."""

    widget = TypedDateTimeWidget

    def __init__(self, **kwargs):
        kwargs.setdefault("require_all_fields", False)
        fields = (forms.CharField(required=False), forms.CharField(required=False), forms.CharField(required=False))
        super().__init__(fields, **kwargs)

    def prepare_value(self, value):
        return _datetime_parts(value) if isinstance(value, datetime) else value

    def compress(self, data_list):
        day, clock, period = (list(data_list) + ["", "", ""])[:3]
        day, clock = str(day or "").strip(), str(clock or "").strip()
        if not day and not clock:
            if self.required:
                raise ValidationError(self.error_messages["required"], code="required")
            return None
        if not day:
            raise ValidationError("Enter the date too.", code="invalid")
        if not clock:
            raise ValidationError("Enter the time too, for example 05:00 PM.", code="invalid")
        moment = datetime.combine(parse_typed_date(day), parse_typed_time(clock, period))
        return timezone.make_aware(moment)
