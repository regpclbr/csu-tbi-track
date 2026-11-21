"""Evaluation links before the end (ASSUMPTIONS 419, NAVIGATU UAT): while the activity is still Ongoing, the 5-minute
job sends the first link to every attendee from EVALUATION_LINK_HOURS_BEFORE_END hours before the end, never before the
evaluation opens (00:00 Manila on the end date); without an end time the activity counts as ending at 23:59. Once per
attendee, no automatic retry; one History row by System per run that sent something, shown on the Evaluation tab."""

from datetime import date, datetime, time, timedelta

import pytest
from django.core import mail
from django.urls import reverse
from django.utils import timezone

from activities import services as activity_services
from activities.models import Activity
from registrations import emails, services
from registrations.models import Registration

from .conftest import opens_at

pytestmark = pytest.mark.django_db


def manila(day: date, hour: int, minute: int = 0) -> datetime:
    return timezone.make_aware(datetime.combine(day, time(hour, minute)))


@pytest.fixture
def ongoing(director, valid_data, with_forms, signup):
    """Factory: an Ongoing activity with both forms and `attendees` people marked attended.
    one_day: start = end date; no_times: start/end time empty (an old row: the form always asks for both)."""

    def make(attendees: int = 2, one_day: bool = False, no_times: bool = False, end_time: time = time(17, 0)):
        start = timezone.localdate() + timedelta(days=30)
        end = start if one_day else start + timedelta(days=1)
        activity = activity_services.create_activity(director, valid_data(
            start_date=start, end_date=end, end_time=end_time,
            evaluation_deadline=manila(end + timedelta(days=7), 17)))
        with_forms(activity)
        activity_services.change_status(director, activity, "Scheduled")
        people = [signup(activity, f"person{n}@example.com") for n in range(attendees)]
        activity_services.change_status(director, activity, "Ongoing")
        services.set_attendance(director, activity, [p.pk for p in people], True, "email")
        if no_times:
            Activity.objects.filter(pk=activity.pk).update(start_time=None, end_time=None)
            activity.refresh_from_db()
        mail.outbox.clear()
        return activity, people

    return make


def run(clock, moment: datetime) -> dict[str, int]:
    """The job as qcluster runs it: at `moment` (Manila), without now=."""
    clock(moment)
    return services.send_automatic_evaluation_links()


def history(activity: Activity) -> list[str]:
    return list(activity.audit_logs.filter(field_changed=activity_services.EVALUATION_LINKS_LABEL)
                .order_by("changed_at", "pk").values_list("new_value", flat=True))


def test_one_day_activity_from_three_hours_before_the_end(ongoing, clock) -> None:
    activity, people = ongoing(attendees=3, one_day=True)  # 8:00 AM - 5:00 PM
    day = activity.end_date
    assert services.automatic_link_moment(activity) == manila(day, 14)
    assert run(clock, manila(day, 13, 59))["sent"] == 0 and mail.outbox == []
    assert run(clock, manila(day, 14))["sent"] == 3
    assert sorted(m.to[0] for m in mail.outbox) == sorted(p.email for p in people)
    activity.refresh_from_db()
    assert activity.status.name == "Ongoing"  # sending never changes the status
    assert history(activity) == ["Sent automatically to 3 attendees"]
    row = activity.audit_logs.get(field_changed=activity_services.EVALUATION_LINKS_LABEL)
    assert row.changed_by is None  # System


def test_before_the_window_nothing_is_sent(ongoing, clock) -> None:
    activity, _ = ongoing()  # two days, ends 5:00 PM on the second
    assert run(clock, manila(activity.start_date, 16))["sent"] == 0  # first day: the evaluation is not open yet
    assert run(clock, opens_at(activity) - timedelta(minutes=1))["sent"] == 0
    assert mail.outbox == [] and history(activity) == []


def test_a_whole_day_activity_counts_as_ending_at_2359(ongoing, clock) -> None:
    activity, _ = ongoing(one_day=True, no_times=True)
    day = activity.end_date
    assert services.automatic_link_moment(activity) == manila(day, 20, 59)
    # activity_start_end() (the status moves) keeps 00:00 of the next day; only this rule uses 23:59.
    assert activity_services.activity_start_end(activity)[1] == manila(day + timedelta(days=1), 0)
    assert run(clock, manila(day, 20, 58))["sent"] == 0
    assert run(clock, manila(day, 20, 59))["sent"] == 2


def test_a_multi_day_activity_waits_for_the_window_to_open(ongoing, clock) -> None:
    activity, _ = ongoing(end_time=time(1, 0))  # ends 1:00 AM on the second day: end - 3 h is the evening before
    assert services.automatic_link_moment(activity) == manila(activity.start_date, 22)
    assert run(clock, manila(activity.start_date, 23))["sent"] == 0  # past end - 3 h, but before 00:00 on the end date
    assert run(clock, opens_at(activity))["sent"] == 2


def test_a_multi_day_activity_on_its_last_day(ongoing, clock) -> None:
    activity, _ = ongoing()  # ends 5:00 PM on the second day
    assert run(clock, manila(activity.end_date, 9))["sent"] == 0  # window open, but not yet end - 3 h
    assert run(clock, manila(activity.end_date, 14))["sent"] == 2


def test_the_number_of_hours_is_a_setting(ongoing, clock, settings) -> None:
    settings.EVALUATION_LINK_HOURS_BEFORE_END = 1
    activity, _ = ongoing(one_day=True)
    assert run(clock, manila(activity.end_date, 15, 59))["sent"] == 0
    assert run(clock, manila(activity.end_date, 16))["sent"] == 2


def test_never_twice_and_failures_are_not_retried(ongoing, clock, monkeypatch) -> None:
    activity, people = ongoing(attendees=2, one_day=True)
    day = activity.end_date
    real_send = emails.send_evaluation_link

    def send(registration, link):
        if registration.pk == people[1].pk:
            raise ConnectionRefusedError("mail server down")
        real_send(registration, link)

    monkeypatch.setattr(emails, "send_evaluation_link", send)
    assert run(clock, manila(day, 14)) == {"sent": 1, "failed": 1, "skipped": 0}
    monkeypatch.setattr(emails, "send_evaluation_link", real_send)
    assert run(clock, manila(day, 14, 5)) == {"sent": 0, "failed": 0, "skipped": 0}
    assert len(mail.outbox) == 1 and history(activity) == ["Sent automatically to 1 attendee"]
    # Conducted later: still nobody gets a second email.
    activity_services.apply_automatic_statuses(now=manila(day, 17, 5))
    activity.refresh_from_db()
    assert activity.status.name == "Conducted"
    assert run(clock, manila(day, 17, 10))["sent"] == 0 and len(mail.outbox) == 1


def test_a_link_resent_by_staff_is_not_sent_again(ongoing, clock, director) -> None:
    activity, people = ongoing(attendees=2, one_day=True)
    day = activity.end_date
    Registration.objects.filter(pk=people[0].pk).update(evaluation_link_sent_at=manila(day, 10))  # staff, earlier
    assert run(clock, manila(day, 14))["sent"] == 1
    assert [m.to[0] for m in mail.outbox] == [people[1].email]


def test_people_marked_later_get_theirs_automatically(ongoing, clock, director) -> None:
    activity, people = ongoing(attendees=2, one_day=True)
    day = activity.end_date
    assert run(clock, manila(day, 14))["sent"] == 2
    extra = Registration.objects.create(activity=activity, name="Late Marked", email="late@example.com",
                                        participant_category=people[0].participant_category,
                                        template=people[0].template)
    # Marked while still Ongoing: the next run sends it.
    services.set_attendance(director, activity, [extra.pk], True, "wet_signature", now=manila(day, 15))
    assert run(clock, manila(day, 15, 5))["sent"] == 1
    # Marked after Conducted (as today, ASSUMPTIONS 348): sent too.
    after = Registration.objects.create(activity=activity, name="After Conducted", email="after@example.com",
                                        participant_category=people[0].participant_category,
                                        template=people[0].template)
    activity_services.apply_automatic_statuses(now=manila(day, 17, 5))
    activity.refresh_from_db()
    assert activity.status.name == "Conducted"
    services.set_attendance(director, activity, [after.pk], True, "wet_signature", now=manila(day + timedelta(days=1), 9))
    assert run(clock, manila(day + timedelta(days=1), 9, 5))["sent"] == 1
    assert [m.to[0] for m in mail.outbox][-2:] == ["late@example.com", "after@example.com"]
    assert history(activity) == ["Sent automatically to 2 attendees", "Sent automatically to 1 attendee",
                                 "Sent automatically to 1 attendee"]


def test_without_an_evaluation_form_nothing_is_sent(ongoing, clock) -> None:
    activity, _ = ongoing(one_day=True)
    activity.form_templates.filter(form_type="evaluation").delete()
    assert run(clock, manila(activity.end_date, 16))["sent"] == 0 and history(activity) == []


def test_the_activity_task_sends_them_before_the_end(ongoing, clock) -> None:
    from activities import tasks

    activity, _ = ongoing(one_day=True)
    clock(manila(activity.end_date, 14, 30))
    result = tasks.run_activity_automation()
    activity.refresh_from_db()
    assert result["sent"] == 2 and activity.status.name == "Ongoing"


def test_page_loads_never_send(ongoing, clock, client, director) -> None:
    activity, _ = ongoing(one_day=True)
    clock(manila(activity.end_date, 15))
    client.force_login(director)
    client.get(reverse("activities:detail", args=[activity.pk]) + "?tab=evaluation")
    client.get(reverse("activities:list"))
    assert mail.outbox == []


def test_evaluation_tab_says_the_links_were_sent_automatically(ongoing, clock, client, director) -> None:
    activity, people = ongoing(attendees=3, one_day=True)
    day = activity.end_date
    url = reverse("activities:detail", args=[activity.pk]) + "?tab=evaluation"
    clock(manila(day, 13))
    client.force_login(director)  # after the clock jump: a session older than 8 hours has ended
    html = client.get(url).content.decode()
    assert "data-automatic-send" not in html
    assert f"It is emailed automatically, once per person, from {activity.end_date:%b} {day.day}, {day.year} 2:00 PM" in html
    assert "(3 hours before the end, once the evaluation is open)" in html

    run(clock, manila(day, 14))
    html = client.get(url).content.decode()
    assert f"Evaluation links were sent automatically on {day:%b} {day.day}, {day.year} 2:00 PM to 3 attendees." in html
    assert "You don&#x27;t need to send them." in html
    assert "Resend link" in html and "Resend to all who haven" in html  # staff keep the buttons
    assert "Later," not in html

    extra = Registration.objects.create(activity=activity, name="Late Marked", email="late@example.com",
                                        participant_category=people[0].participant_category,
                                        template=people[0].template)
    services.set_attendance(director, activity, [extra.pk], True, "email", now=manila(day, 15))
    run(clock, manila(day, 15, 5))
    html = client.get(url).content.decode()
    assert f"Later, 1 more attendee got theirs automatically (latest {day:%b} {day.day}, {day.year} 3:05 PM)." in html


def test_no_notice_for_staff_sends_only(ongoing, clock, client, director) -> None:
    activity, people = ongoing(one_day=True)
    clock(manila(activity.end_date, 10))
    services.send_evaluation_links(director, activity)  # queued by staff (window open, before end - 3 h)
    client.force_login(director)
    html = client.get(reverse("activities:detail", args=[activity.pk]) + "?tab=evaluation").content.decode()
    assert "data-automatic-send" not in html and services.automatic_link_sends(activity) == []
