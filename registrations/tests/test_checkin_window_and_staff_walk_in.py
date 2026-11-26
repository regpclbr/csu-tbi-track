"""The QR check-in window (ASSUMPTIONS 454) and the staff "Add a walk-in" (ASSUMPTIONS 455).

The `scheduled` activity: two days, 8:00 AM - 5:00 PM, one category (Incubatees), both forms. Every time is passed as
`now` or set with the `clock` fixture: nothing depends on today's date.
"""

from datetime import datetime, time, timedelta

import pytest
from django.core.exceptions import ValidationError
from django.urls import reverse
from django.utils import timezone

from activities import services as activity_services
from activities.models import ActivityAuditLog
from registrations import attendance, services
from registrations.models import AttendanceDay, Registration

from .conftest import FIXED, SIGN_POST

pytestmark = pytest.mark.django_db

TYPED = {"kind": "typed", "typed_name": "Walter In", "confirmed": True}


def at(day, hour, minute=0) -> datetime:
    return timezone.make_aware(datetime.combine(day, time(hour, minute)))


# --- The check-in window: 60 minutes before to 60 minutes after the start, each day --------------------------------------


def test_window_is_an_hour_before_to_an_hour_after_the_start_on_each_day(scheduled, settings) -> None:
    settings.PARTICIPANT_CHECKIN_EARLY_MINUTES = 60
    settings.PARTICIPANT_CHECKIN_LATE_MINUTES = 60
    first, second = scheduled.start_date, scheduled.end_date
    assert attendance.checkin_state(scheduled, at(first, 6, 59)).kind == "not_yet_open"
    assert attendance.checkin_state(scheduled, at(first, 7, 0)).kind == "open"
    assert attendance.checkin_state(scheduled, at(first, 8, 59)).kind == "open"
    late = attendance.checkin_state(scheduled, at(first, 9, 0))  # the start + 60 minutes: closed for today
    assert late.kind == "closed" and late.day == first
    assert "closed at 9:00 AM" in late.reason and "ask the TBI staff" in late.reason
    assert attendance.checkin_state(scheduled, at(first, 16, 0)).kind == "closed"  # was open until 5:00 PM before
    assert attendance.checkin_state(scheduled, at(second, 7, 30)) == attendance.CheckinState("open", None, second)
    assert "closed at 9:00 AM" in attendance.checkin_state(scheduled, at(second, 9, 30)).reason
    assert attendance.checkin_state(scheduled, at(second + timedelta(days=1), 8)).reason == attendance.CHECKIN_CLOSED


def test_the_late_minutes_are_a_setting(scheduled, settings) -> None:
    settings.PARTICIPANT_CHECKIN_LATE_MINUTES = 30
    assert attendance.checkin_state(scheduled, at(scheduled.start_date, 8, 29)).kind == "open"
    assert attendance.checkin_state(scheduled, at(scheduled.start_date, 8, 30)).kind == "closed"


def test_after_the_window_qr_check_in_and_walk_ins_are_refused_but_staff_still_mark(scheduled, signup, director,
                                                                                   clock) -> None:
    person = signup(scheduled, "ana@example.com")
    with pytest.raises(attendance.AttendanceError, match="ask the TBI staff"):
        attendance.check_in(scheduled, "ana@example.com", now=at(scheduled.start_date, 10))
    with pytest.raises(attendance.AttendanceError, match="ask the TBI staff"):
        attendance.register_walk_in(scheduled, name="Late", email="late@example.com", **FIXED, signature=TYPED,
                                    now=at(scheduled.start_date, 10))
    clock(datetime.combine(scheduled.start_date, time(10)))
    activity_services.change_status(director, scheduled, "Ongoing")
    services.set_attendance(director, scheduled, [person.pk], True, day=scheduled.start_date)
    assert AttendanceDay.objects.get(registration=person).method == AttendanceDay.Method.STAFF


def test_the_closed_page_says_to_ask_the_staff(client, scheduled, clock) -> None:
    clock(datetime.combine(scheduled.start_date, time(9, 15)))
    page = client.get(reverse("checkin", args=[scheduled.pk])).content.decode()
    assert "Check-in is closed" in page and "closed at 9:00 AM" in page and "ask the TBI staff" in page


# --- The staff "Add a walk-in" -------------------------------------------------------------------------------------------


def add(member, activity, now, **extra):
    values = {"name": "Walter In", "email": "walter@example.com", "contact_number": "0917", **FIXED, "signature": TYPED,
              **extra}
    return attendance.add_walk_in_by_staff(member, activity, now=now, **values)


def test_staff_add_a_walk_in_on_any_day_while_ongoing_and_today_is_marked(scheduled, director, clock) -> None:
    clock(datetime.combine(scheduled.end_date, time(15)))  # day 2, long after the QR window
    activity_services.change_status(director, scheduled, "Ongoing")
    registration = add(director, scheduled, at(scheduled.end_date, 15))
    assert registration.registration_source == Registration.Source.WALK_IN and registration.attendance_confirmed
    row = registration.attendance_days.get()
    assert (row.day, row.method, row.marked_by) == (scheduled.end_date, AttendanceDay.Method.STAFF, director)
    assert registration.signature.typed_name == "Walter In"
    history = ActivityAuditLog.objects.filter(activity=scheduled, field_changed=attendance.WALK_IN_HISTORY_LABEL).get()
    assert history.changed_by == director and "Walter In added by staff" in history.new_value


def test_staff_walk_in_uses_every_rule_of_a_walk_in(scheduled, director, signup, view_only, clock) -> None:
    signup(scheduled, "ana@example.com")
    clock(datetime.combine(scheduled.start_date, time(13)))
    now = at(scheduled.start_date, 13)
    with pytest.raises(attendance.AttendanceError, match="Ongoing"):  # still Scheduled
        add(director, scheduled, now)
    activity_services.change_status(director, scheduled, "Ongoing")
    with pytest.raises(services.RegistrationError, match="already registered"):
        add(director, scheduled, now, email="ANA@example.com")
    with pytest.raises(ValidationError) as error:
        add(director, scheduled, now, name="", designation="", signature={})
    assert {"name", "designation", "signature"} <= set(error.value.message_dict)
    with pytest.raises(attendance.AttendanceError, match="not one of this activity's days"):
        add(director, scheduled, at(scheduled.end_date + timedelta(days=1), 9))
    from django.core.exceptions import PermissionDenied

    with pytest.raises(PermissionDenied):
        add(view_only, scheduled, now)
    for kind in ({"kind": "draw", "drawn": ""}, ):  # an empty drawing is not a signature
        with pytest.raises(ValidationError):
            add(director, scheduled, now, email="x@example.com", signature=kind)
    assert not Registration.objects.filter(email="x@example.com").exists()


def test_the_walk_in_page_needs_the_privacy_consent_and_marks_attendance(client, scheduled, director, clock) -> None:
    clock(datetime.combine(scheduled.start_date, time(13)))
    activity_services.change_status(director, scheduled, "Ongoing")
    client.force_login(director)
    tab = client.get(reverse("activities:detail", args=[scheduled.pk]) + "?tab=registrations").content.decode()
    assert "Add a walk-in" in tab
    url = reverse("registrations:walk_in", args=[scheduled.pk])
    page = client.get(url).content.decode()
    assert 'data-enter-submit="off"' in page and 'data-signature-choice="upload"' in page
    post = {"name": "Walter In", "email": "walter@example.com", **FIXED, **SIGN_POST,
            "participant_category": str(scheduled.participant_categories.get().pk)}
    refused = client.post(url, post)
    assert refused.status_code == 400 and not Registration.objects.filter(email="walter@example.com").exists()
    response = client.post(url, {**post, services.PRIVACY_CONSENT_FIELD: "on"})
    assert response.status_code == 302 and response["Location"].endswith("?tab=registrations")
    assert Registration.objects.get(email="walter@example.com").attendance_confirmed


def test_no_add_a_walk_in_before_ongoing(client, scheduled, director) -> None:
    client.force_login(director)
    tab = client.get(reverse("activities:detail", args=[scheduled.pk]) + "?tab=registrations").content.decode()
    assert "data-add-walk-in" not in tab
    assert attendance.STAFF_WALK_IN_STATUS in client.get(reverse("registrations:walk_in", args=[scheduled.pk])).content.decode()
