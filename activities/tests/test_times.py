"""Start and end time (replaces time_schedule, ASSUMPTIONS 345): the migration helper, the form rule, the display
helper, the audit labels and the calendar file."""

from datetime import time, timedelta

import pytest
from django.core.exceptions import ValidationError
from django.urls import reverse

from activities import services
from activities.formatting import TIME_NOT_SET, format_audit_value, format_time_range
from activities.models import Activity, ActivityAuditLog
from activities.time_parsing import parse_time_schedule, time_schedule_text
from registrations.public_pages import activity_ics

pytestmark = pytest.mark.django_db


# --- The migration helper ------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "text, expected",
    [
        ("8:00 AM - 5:00 PM", (time(8), time(17))),
        ("8:00 AM – 5:00 PM", (time(8), time(17))),
        ("8:00 AM — 5:00 PM", (time(8), time(17))),
        ("8:00 AM to 5:00 PM", (time(8), time(17))),
        ("8am-5pm", (time(8), time(17))),
        ("8:30 a.m. - 12:00 p.m.", (time(8, 30), time(12))),
        ("08:00-17:00", (time(8), time(17))),
        ("13:00 to 17:30", (time(13), time(17, 30))),
        ("8:00 - 11:30 AM", (time(8), time(11, 30))),  # the start takes the end's AM
        ("10 - 2 PM", (time(10), time(14))),  # 10 PM would be after 2 PM, so 10 AM
        ("1:00 PM - 5:00", (time(13), time(17))),  # 24-hour end after a PM start
        ("  9:00 AM  -  4:00 PM  ", (time(9), time(16))),
    ],
)
def test_readable_time_schedules(text, expected) -> None:
    assert parse_time_schedule(text) == expected


@pytest.mark.parametrize(
    "text",
    ["", None, "8:00 AM", "9-5", "8-5", "Whole day", "8:00 AM - 5:00 PM - 6:00 PM", "25:00 - 26:00", "13 PM - 2 PM",
     "5:00 PM - 8:00 AM", "8:75 - 9:00", "TBA"],
)
def test_unreadable_time_schedules_give_none(text) -> None:
    assert parse_time_schedule(text) is None


def test_reverse_text_is_rebuilt_from_the_times() -> None:
    assert time_schedule_text(time(8), time(17, 30)) == "8:00 AM - 5:30 PM"
    assert time_schedule_text(None, None) == ""
    assert parse_time_schedule(time_schedule_text(time(0, 15), time(12))) == (time(0, 15), time(12))


# --- Display -------------------------------------------------------------------------------------------


def test_format_time_range() -> None:
    assert format_time_range(time(8), time(17)) == "8:00 AM – 5:00 PM"
    assert format_time_range(time(0, 5), time(12, 30)) == "12:05 AM – 12:30 PM"
    assert format_time_range(None, None) == TIME_NOT_SET == "Time not set"
    assert format_audit_value(time(13, 45)) == "1:45 PM"


# --- Form / service rules --------------------------------------------------------------------------------


def test_one_day_activity_must_end_after_it_starts(director, valid_data) -> None:
    day = valid_data()["start_date"]
    with pytest.raises(ValidationError) as error:
        services.create_activity(director, valid_data(end_date=day, start_time=time(17), end_time=time(8)))
    assert error.value.message_dict["end_time"] == ["End time must be after the start time."]
    with pytest.raises(ValidationError) as error:
        services.create_activity(director, valid_data(end_date=day, start_time=time(9), end_time=time(9)))
    assert "end_time" in error.value.message_dict


def test_several_days_may_end_earlier_in_the_day(director, valid_data) -> None:
    activity = services.create_activity(director, valid_data(start_time=time(13), end_time=time(12)))
    assert (activity.start_time, activity.end_time) == (time(13), time(12))


def test_both_times_are_required(director, valid_data) -> None:
    with pytest.raises(ValidationError) as error:
        services.create_activity(director, valid_data(start_time=None, end_time=None))
    assert set(error.value.message_dict) >= {"start_time", "end_time"}


def test_editing_times_writes_start_time_and_end_time_rows(director, new_activity) -> None:
    services.update_activity(director, new_activity, {"start_time": time(9), "end_time": time(16, 30)})
    rows = {row.field_changed: (row.old_value, row.new_value) for row in ActivityAuditLog.objects.filter(activity=new_activity)}
    assert rows["Start Time"] == ("8:00 AM", "9:00 AM")
    assert rows["End Time"] == ("5:00 PM", "4:30 PM")


def test_page_form_has_two_time_pickers(client, director) -> None:
    client.force_login(director)
    html = client.get(reverse("activities:create")).content.decode()
    # Typed hh:mm boxes with AM/PM since ASSUMPTIONS 422.
    assert 'name="start_time" value="" inputmode="numeric"' in html and 'name="end_time_period"' in html
    assert "time_schedule" not in html


# --- Calendar file ---------------------------------------------------------------------------------------


def test_calendar_file_is_timed_when_times_are_known(new_activity) -> None:
    ics = activity_ics(new_activity)
    start = new_activity.start_date
    # 8:00 AM Manila = 00:00 UTC
    assert f"DTSTART:{start:%Y%m%d}T000000Z" in ics
    assert f"DTEND:{new_activity.end_date:%Y%m%d}T090000Z" in ics
    assert "VALUE=DATE" not in ics


def test_calendar_file_is_all_day_without_times(new_activity) -> None:
    Activity.objects.filter(pk=new_activity.pk).update(start_time=None, end_time=None)
    new_activity.refresh_from_db()
    ics = activity_ics(new_activity)
    assert f"DTSTART;VALUE=DATE:{new_activity.start_date:%Y%m%d}" in ics
    assert f"DTEND;VALUE=DATE:{new_activity.end_date + timedelta(days=1):%Y%m%d}" in ics
    assert "Time not set" in ics
