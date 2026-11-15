"""Periods in questions, worked out by our code in Manila time (ASSUMPTIONS 381). Pure functions: no database."""

from datetime import date, datetime
from zoneinfo import ZoneInfo

import pytest
from django.utils import timezone

from assistant.periods import NOW, RANGE, UPCOMING, parse_period, range_label

MANILA = ZoneInfo("Asia/Manila")


def at(year: int, month: int, day: int, hour: int = 10, minute: int = 0) -> datetime:
    return timezone.make_aware(datetime(year, month, day, hour, minute))


SUNDAY = at(2026, 11, 15)  # the day the bug was reported
MONDAY = at(2026, 11, 16)


def dates(text: str, now: datetime = MONDAY) -> tuple[date, date]:
    period = parse_period(text, now)
    assert period is not None and period.kind == RANGE, text
    return period.start, period.end


# --- Months -------------------------------------------------------------------------------------------------------


def test_the_november_example() -> None:
    period = parse_period("What are the activities for the month of November?", MONDAY)
    assert (period.start, period.end) == (date(2026, 11, 1), date(2026, 11, 30))
    assert period.label == "November 1–30, 2026"


@pytest.mark.parametrize("text", ["november", "Nov", "nov.", "NOVEMBER", "in November 2026", "november's activity",
                                  "this November", "for the month of november"])
def test_month_spellings(text) -> None:
    assert dates(text) == (date(2026, 11, 1), date(2026, 11, 30))


def test_month_boundaries_and_leap_years() -> None:
    assert dates("February 2028") == (date(2028, 2, 1), date(2028, 2, 29))  # leap year
    assert dates("February 2027") == (date(2027, 2, 1), date(2027, 2, 28))
    assert dates("December 2026") == (date(2026, 12, 1), date(2026, 12, 31))
    assert dates("January 2027") == (date(2027, 1, 1), date(2027, 1, 31))
    assert dates("April 2026") == (date(2026, 4, 1), date(2026, 4, 30))


def test_a_month_without_a_year_is_the_current_year_unless_clearly_past_or_future() -> None:
    assert dates("activities in December") == (date(2026, 12, 1), date(2026, 12, 31))  # no cue: this year
    assert dates("activities in March") == (date(2026, 3, 1), date(2026, 3, 31))
    assert dates("activities conducted in December") == (date(2025, 12, 1), date(2025, 12, 31))  # past, later month
    assert dates("what was held in October") == (date(2026, 10, 1), date(2026, 10, 31))  # past, earlier month: this year
    assert dates("upcoming activities in January") == (date(2027, 1, 1), date(2027, 1, 31))  # future, earlier month
    assert dates("activities planned for December") == (date(2026, 12, 1), date(2026, 12, 31))  # future, later month


def test_last_and_next_month_names() -> None:
    assert dates("last November") == (date(2025, 11, 1), date(2025, 11, 30))
    assert dates("last October") == (date(2026, 10, 1), date(2026, 10, 31))
    assert dates("next March") == (date(2027, 3, 1), date(2027, 3, 31))
    assert dates("next December") == (date(2026, 12, 1), date(2026, 12, 31))


def test_may_is_a_month_only_next_to_a_date_word() -> None:
    assert dates("activities in May") == (date(2026, 5, 1), date(2026, 5, 31))
    assert parse_period("may I see the activities?", MONDAY) is None


# --- Days ---------------------------------------------------------------------------------------------------------


def test_single_days() -> None:
    assert dates("November 14") == (date(2026, 11, 14), date(2026, 11, 14))
    assert dates("on Nov 3, 2026") == (date(2026, 11, 3), date(2026, 11, 3))
    assert dates("14th of November") == (date(2026, 11, 14), date(2026, 11, 14))
    assert dates("2026-11-14") == (date(2026, 11, 14), date(2026, 11, 14))
    assert parse_period("November 14", MONDAY).label == "November 14, 2026"


def test_today_tomorrow_yesterday_in_manila() -> None:
    assert dates("today") == (date(2026, 11, 16), date(2026, 11, 16))
    assert dates("tomorrow") == (date(2026, 11, 17), date(2026, 11, 17))
    assert dates("yesterday") == (date(2026, 11, 15), date(2026, 11, 15))
    # 11:30 PM UTC on Nov 15 is already Nov 16 in Manila (UTC+8).
    utc_late = datetime(2026, 11, 15, 23, 30, tzinfo=ZoneInfo("UTC"))
    assert dates("today", utc_late) == (date(2026, 11, 16), date(2026, 11, 16))


# --- Weeks (Monday to Sunday) -------------------------------------------------------------------------------------


def test_this_week_on_a_sunday_is_the_week_ending_today() -> None:
    assert dates("this week", SUNDAY) == (date(2026, 11, 9), date(2026, 11, 15))
    assert dates("next week", SUNDAY) == (date(2026, 11, 16), date(2026, 11, 22))
    assert dates("last week", SUNDAY) == (date(2026, 11, 2), date(2026, 11, 8))


def test_this_week_on_a_monday_starts_today() -> None:
    assert dates("this week", MONDAY) == (date(2026, 11, 16), date(2026, 11, 22))
    assert dates("next week", MONDAY) == (date(2026, 11, 23), date(2026, 11, 29))
    assert dates("last week", MONDAY) == (date(2026, 11, 9), date(2026, 11, 15))


def test_weeks_across_month_and_year_boundaries() -> None:
    period = parse_period("this week", at(2026, 12, 2))
    assert (period.start, period.end) == (date(2026, 11, 30), date(2026, 12, 6))
    assert period.label == "November 30 – December 6, 2026"
    period = parse_period("next week", at(2026, 12, 24))
    assert (period.start, period.end) == (date(2026, 12, 28), date(2027, 1, 3))
    assert period.label == "December 28, 2026 – January 3, 2027"


# --- Months and years relative to today ---------------------------------------------------------------------------


def test_this_next_last_month_across_year_boundaries() -> None:
    assert dates("this month") == (date(2026, 11, 1), date(2026, 11, 30))
    assert dates("next month") == (date(2026, 12, 1), date(2026, 12, 31))
    assert dates("last month") == (date(2026, 10, 1), date(2026, 10, 31))
    assert dates("next month", at(2026, 12, 31, 23, 59)) == (date(2027, 1, 1), date(2027, 1, 31))
    assert dates("last month", at(2027, 1, 1, 0, 1)) == (date(2026, 12, 1), date(2026, 12, 31))


def test_years() -> None:
    assert dates("how many trainings this year") == (date(2026, 1, 1), date(2026, 12, 31))
    assert dates("last year") == (date(2025, 1, 1), date(2025, 12, 31))
    assert dates("in 2025") == (date(2025, 1, 1), date(2025, 12, 31))
    assert parse_period("this year", MONDAY).label == "January 1 – December 31, 2026"


# --- Upcoming and now ---------------------------------------------------------------------------------------------


def test_upcoming_and_happening_now() -> None:
    upcoming = parse_period("upcoming activities", MONDAY)
    assert upcoming.kind == UPCOMING and upcoming.start == date(2026, 11, 16)
    assert parse_period("what's happening now", MONDAY).kind == NOW
    assert parse_period("which activities are going on right now", MONDAY).kind == NOW
    # A plain "now" is not a period ("show me the files now").
    assert parse_period("show me the files now", MONDAY) is None


def test_no_period() -> None:
    assert parse_period("When is the Investor Networking?", MONDAY) is None
    assert parse_period("ITE 16 Pitching Competition", MONDAY) is None


def test_range_label() -> None:
    assert range_label(date(2026, 11, 1), date(2026, 11, 30)) == "November 1–30, 2026"
    assert range_label(date(2026, 11, 14), date(2026, 11, 14)) == "November 14, 2026"
