"""Typed dates and times on the activity form (ASSUMPTIONS 422, NAVIGATU UAT): dates typed as words or numbers, times
as hh:mm + AM/PM, deadlines as date + time + AM/PM; every value is checked on the server with a clear message."""

from datetime import date, time, timedelta

import pytest
from django.core.exceptions import ValidationError
from django.urls import reverse
from django.utils import timezone

from activities.forms import ActivityPageForm
from activities.models import Activity
from activities.typed_fields import (
    CHOOSE_PERIOD, INVALID_DATE, INVALID_TIME, format_typed_date, parse_typed_date, parse_typed_time, split_time,
)
from conftest import creation_document_files

from .test_pages import field_error, login, post_data  # noqa: F401 (fixtures)

pytestmark = pytest.mark.django_db
CREATE = reverse("activities:create")


@pytest.mark.parametrize("text", ["June 5, 2026", "june 5 2026", "Jun 5, 2026", "JUN. 5, 2026", "5 June 2026",
                                  "2026-06-05", "06/05/2026", "  June  5 ,2026 "])
def test_dates_typed_as_words_or_numbers(text) -> None:
    assert parse_typed_date(text) == date(2026, 6, 5)


@pytest.mark.parametrize("text", ["", "Ju 5, 2026", "June 31, 2026", "13/05/2026", "tomorrow", "2026-02-30"])
def test_unreadable_dates_get_a_clear_message(text) -> None:
    with pytest.raises(ValidationError) as excinfo:
        parse_typed_date(text)
    assert excinfo.value.messages == [INVALID_DATE]


@pytest.mark.parametrize(("text", "period", "expected"), [
    ("07:30", "AM", time(7, 30)),
    ("07:30", "PM", time(19, 30)),
    ("12:00", "AM", time(0, 0)),
    ("12:15", "PM", time(12, 15)),
    ("730", "PM", time(19, 30)),
    ("14:30", "", time(14, 30)),  # a 24-hour time needs no AM/PM
    ("14:30", "PM", time(14, 30)),
    ("00:15", "", time(0, 15)),
    ("7:30 pm", "", time(19, 30)),  # typed with the period
])
def test_times_with_am_pm(text, period, expected) -> None:
    assert parse_typed_time(text, period) == expected


@pytest.mark.parametrize(("text", "period", "message"), [
    ("07:30", "", CHOOSE_PERIOD),
    ("25:00", "", INVALID_TIME),
    ("07:75", "AM", INVALID_TIME),
    ("7.30", "AM", INVALID_TIME),
    ("noon", "", INVALID_TIME),
    ("14:30", "AM", "14:30 is a 24-hour time: leave AM/PM empty or type it as 02:30."),
])
def test_invalid_times_are_refused_with_a_clear_message(text, period, message) -> None:
    with pytest.raises(ValidationError) as excinfo:
        parse_typed_time(text, period)
    assert excinfo.value.messages == [message]


def test_stored_values_are_shown_the_same_way() -> None:
    assert format_typed_date(date(2026, 9, 5)) == "September 5, 2026"
    assert split_time(time(14, 30)) == ("02:30", "PM") and split_time(time(0, 5)) == ("12:05", "AM")


def test_the_form_reads_typed_values_and_an_empty_evaluation_deadline(roles) -> None:
    form = ActivityPageForm(data={
        "start_date": "jun 5 2030", "end_date": "06/06/2030", "start_time": "0800", "start_time_period": "AM",
        "end_time": "05:00", "end_time_period": "PM", "registration_deadline": "June 1, 2030",
        "registration_deadline_time": "05:00", "registration_deadline_period": "PM", "evaluation_deadline": "",
        "evaluation_deadline_time": "", "evaluation_deadline_period": "",
    })
    form.is_valid()
    cleaned = form.cleaned_data
    assert cleaned["start_date"] == date(2030, 6, 5) and cleaned["end_date"] == date(2030, 6, 6)
    assert (cleaned["start_time"], cleaned["end_time"]) == (time(8), time(17))
    assert timezone.localtime(cleaned["registration_deadline"]).strftime("%Y-%m-%d %H:%M") == "2030-06-01 17:00"
    assert cleaned["evaluation_deadline"] is None and "evaluation_deadline" not in form.errors


def test_a_deadline_with_only_the_date_is_refused(roles) -> None:
    form = ActivityPageForm(data={"registration_deadline": "June 1, 2030", "registration_deadline_time": ""})
    form.is_valid()
    assert form.errors["registration_deadline"] == ["Enter the time too, for example 05:00 PM."]


def test_errors_are_shown_under_the_field_and_nothing_is_saved(director, login, post_data) -> None:
    data = {**post_data(), "start_time": "07:30", "start_time_period": "", "end_date": "Juno 5"}
    html = login(director).post(CREATE, data).content.decode()
    assert field_error(html, "start_time") == CHOOSE_PERIOD
    assert field_error(html, "end_date") == INVALID_DATE
    assert not Activity.objects.exists()


def test_a_new_activity_from_typed_values_and_the_edit_form_shows_them_back(director, login, post_data) -> None:
    start = timezone.localdate() + timedelta(days=40)
    data = {**post_data(title="Typed"), **creation_document_files(),
            "start_date": f"{start:%B} {start.day} {start.year}", "end_date": start.strftime("%m/%d/%Y"),
            "start_time": "0930", "start_time_period": "AM", "end_time": "1430", "end_time_period": "",
            "evaluation_deadline": "", "evaluation_deadline_time": "", "evaluation_deadline_period": ""}
    login(director).post(CREATE, data)
    activity = Activity.objects.get(title="Typed")
    assert (activity.start_date, activity.end_date) == (start, start)
    assert (activity.start_time, activity.end_time) == (time(9, 30), time(14, 30))
    assert activity.evaluation_deadline is None
    html = login(director).get(reverse("activities:edit", args=[activity.pk])).content.decode()
    assert f'value="{format_typed_date(start)}"' in html and 'value="02:30"' in html
    assert '<option value="PM" selected>PM</option>' in html


def test_the_form_has_labels_the_calendar_button_and_the_suggestion_list(director, login) -> None:
    html = login(director).get(CREATE).content.decode()
    assert '<label for="id_start_date"' in html and 'role="combobox"' in html and 'aria-controls="id_start_date-suggestions"' in html
    assert 'aria-label="Choose start date from a calendar"' in html
    assert 'inputmode="numeric"' in html and 'aria-label="Start time: AM or PM"' in html
    assert 'aria-label="Registration deadline: date"' in html and 'name="registration_deadline_period"' in html
    assert "js/typed_dates.js" in html


# --- Typed freely (ASSUMPTIONS 432): every way of typing a time, in every time box, New and Edit --------------------

FREE_TIMES = [("11:00", time(11, 0)), ("1100", time(11, 0)), ("1:30", time(1, 30)), ("130", time(1, 30)),
              ("9", time(9, 0)), ("12:15", time(0, 15)), ("01:40", time(1, 40))]


@pytest.mark.parametrize(("text", "morning"), FREE_TIMES)
@pytest.mark.parametrize("period", ["AM", "PM"])
def test_every_way_of_typing_a_time_is_read_with_its_am_pm(text, morning, period) -> None:
    """The server rule (parse_typed_time) reads what the box sends without any script, with the select's AM/PM."""
    expected = morning if period == "AM" else time(morning.hour + 12 if morning.hour < 12 else 12, morning.minute)
    assert parse_typed_time(text, period) == expected


TIME_BOXES = {"start_time": "start_time", "end_time": "end_time",
              "registration_deadline": "registration_deadline_time", "evaluation_deadline": "evaluation_deadline_time"}


@pytest.mark.parametrize(("text", "morning"), FREE_TIMES)
def test_every_time_box_reads_pm(roles, text, morning) -> None:
    """Each of the four boxes (start, end, registration and evaluation deadline) sends <box> + <field>_period."""
    data = {"start_date": "June 5, 2030", "end_date": "June 6, 2030", "registration_deadline": "June 1, 2030",
            "evaluation_deadline": "June 20, 2030"}
    for field, box in TIME_BOXES.items():
        data[box], data[f"{field}_period"] = text, "PM"
    form = ActivityPageForm(data=data)
    form.is_valid()
    pm = time(morning.hour % 12 + 12, morning.minute)
    for field in TIME_BOXES:
        assert field not in form.errors, (field, form.errors.get(field))
    assert (form.cleaned_data["start_time"], form.cleaned_data["end_time"]) == (pm, pm)
    for field in ("registration_deadline", "evaluation_deadline"):
        assert timezone.localtime(form.cleaned_data[field]).time() == pm


def test_new_and_edit_forms_save_every_time_box_with_pm(director, login, post_data) -> None:
    start = timezone.localdate() + timedelta(days=40)
    data = {**post_data(title="All PM"), "start_date": f"{start:%B} {start.day}, {start.year}",
            "end_date": f"{start:%B} {start.day}, {start.year}", "start_time": "1:30", "start_time_period": "PM",
            "end_time": "0340", "end_time_period": "PM", "registration_deadline_time": "1100",
            "registration_deadline_period": "PM", "evaluation_deadline": f"{start + timedelta(days=5):%B %d, %Y}",
            "evaluation_deadline_time": "9", "evaluation_deadline_period": "PM"}
    client = login(director)
    assert client.post(CREATE, data).status_code == 302
    activity = Activity.objects.get(title="All PM")
    assert (activity.start_time, activity.end_time) == (time(13, 30), time(15, 40))
    assert timezone.localtime(activity.registration_deadline).time() == time(23, 0)
    assert timezone.localtime(activity.evaluation_deadline).time() == time(21, 0)
    edit = reverse("activities:edit", args=[activity.pk])
    data.update(title="All PM edited", start_time="130", end_time="04:15", registration_deadline_time="10:00",
                evaluation_deadline_time="0800")
    assert client.post(edit, data).status_code == 302
    activity.refresh_from_db()
    assert (activity.start_time, activity.end_time) == (time(13, 30), time(16, 15))
    assert timezone.localtime(activity.registration_deadline).time() == time(22, 0)
    assert timezone.localtime(activity.evaluation_deadline).time() == time(20, 0)
