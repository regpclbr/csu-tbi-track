"""Automatic status moves (ASSUMPTIONS 346): Scheduled -> Ongoing at the start, Ongoing -> Conducted after the end once
attendance is recorded; Draft never touched; "System" in the audit log; page loads and the 5-minute schedule."""

from datetime import datetime, time, timedelta

import pytest
from django.core import mail
from django.core.management import call_command
from django.urls import reverse
from django.utils import timezone
from django_q.models import Schedule

from activities import services, tasks
from activities.formatting import audit_sentence, changed_by_name
from activities.models import Activity, ActivityAuditLog
from registrations import services as registration_services
from registrations.models import FormType, Registration

pytestmark = pytest.mark.django_db


def manila(day, hour: int, minute: int = 0) -> datetime:
    return timezone.make_aware(datetime.combine(day, time(hour, minute)))


def status_of(activity: Activity) -> str:
    return Activity.objects.select_related("status").get(pk=activity.pk).status.name


def system_rows(activity: Activity) -> list[tuple[str, str]]:
    rows = ActivityAuditLog.objects.filter(activity=activity, changed_by__isnull=True).order_by("log_id")
    return [(row.old_value, row.new_value) for row in rows]


@pytest.fixture
def scheduled(new_activity, move_to) -> Activity:
    """Scheduled, 8:00 AM on the start date to 5:00 PM on the end date (the next day)."""
    return move_to(new_activity, "Scheduled")


def test_activity_start_end_uses_the_times_or_the_whole_days(new_activity) -> None:
    start, end = services.activity_start_end(new_activity)
    assert start == manila(new_activity.start_date, 8) and end == manila(new_activity.end_date, 17)
    Activity.objects.filter(pk=new_activity.pk).update(start_time=None, end_time=None)
    new_activity.refresh_from_db()
    start, end = services.activity_start_end(new_activity)
    assert start == manila(new_activity.start_date, 0)
    assert end == manila(new_activity.end_date + timedelta(days=1), 0)


def test_scheduled_becomes_ongoing_when_the_start_arrives(scheduled) -> None:
    assert services.apply_automatic_statuses(now=manila(scheduled.start_date, 7, 59)) == 0
    assert status_of(scheduled) == "Scheduled"
    assert services.apply_automatic_statuses(now=manila(scheduled.start_date, 8)) == 1
    assert status_of(scheduled) == "Ongoing"
    assert system_rows(scheduled) == [("Scheduled", "Ongoing")]


def test_the_audit_row_is_by_system(scheduled) -> None:
    services.apply_automatic_statuses(now=manila(scheduled.start_date, 9))
    row = ActivityAuditLog.objects.filter(activity=scheduled).order_by("-log_id").first()
    assert row.changed_by is None and changed_by_name(row) == "System"
    assert audit_sentence(row) == f"System changed the status of {scheduled.title} from Scheduled to Ongoing"


def test_ongoing_without_attendance_stays_ongoing_after_the_end(scheduled, director) -> None:
    services.change_status(director, scheduled, "Ongoing")
    assert services.apply_automatic_statuses(now=manila(scheduled.end_date, 17, 1)) == 0
    assert services.apply_automatic_statuses(now=manila(scheduled.end_date + timedelta(days=5), 9)) == 0
    assert status_of(scheduled) == "Ongoing"


def test_ongoing_becomes_conducted_after_the_end_once_attendance_is_recorded(scheduled, register, director) -> None:
    services.change_status(director, scheduled, "Ongoing")
    person = register(scheduled)[0]
    registration_services.set_attendance(director, scheduled, [person.pk], True, "wet_signature")
    assert services.apply_automatic_statuses(now=manila(scheduled.end_date, 16, 59)) == 0  # not ended yet
    assert services.apply_automatic_statuses(now=manila(scheduled.end_date, 17)) == 1
    assert status_of(scheduled) == "Conducted"
    assert system_rows(scheduled) == [("Ongoing", "Conducted")]


def test_two_step_catch_up_writes_two_rows(scheduled, register) -> None:
    person = register(scheduled)[0]
    Registration.objects.filter(pk=person.pk).update(attendance_confirmed=True, confirmation_method="email")
    assert services.apply_automatic_statuses(now=manila(scheduled.end_date + timedelta(days=1), 9)) == 2
    assert status_of(scheduled) == "Conducted"
    assert system_rows(scheduled) == [("Scheduled", "Ongoing"), ("Ongoing", "Conducted")]


def test_draft_is_never_touched(new_activity) -> None:
    assert services.apply_automatic_statuses(now=manila(new_activity.end_date + timedelta(days=30), 9)) == 0
    assert status_of(new_activity) == "Draft"
    assert services.refresh_status(new_activity, now=manila(new_activity.end_date + timedelta(days=30), 9)) is False


def test_conducted_is_never_touched_and_running_twice_changes_nothing(scheduled, register) -> None:
    Registration.objects.filter(pk=register(scheduled)[0].pk).update(attendance_confirmed=True, confirmation_method="email")
    later = manila(scheduled.end_date + timedelta(days=2), 9)
    services.apply_automatic_statuses(now=later)
    assert services.apply_automatic_statuses(now=later) == 0
    assert len(system_rows(scheduled)) == 2


def test_staff_can_still_conduct_by_hand_without_attendance(scheduled, director) -> None:
    services.change_status(director, scheduled, "Ongoing")
    services.change_status(director, scheduled, "Conducted")
    assert status_of(scheduled) == "Conducted"


def test_refresh_status_moves_one_activity_and_reloads_it(scheduled) -> None:
    assert services.refresh_status(scheduled, now=manila(scheduled.start_date, 8, 30)) is True
    assert scheduled.status.name == "Ongoing"
    assert services.refresh_status(scheduled, now=manila(scheduled.start_date, 8, 31)) is False


def test_activity_page_load_refreshes_the_status_without_emails(client, director, scheduled, register, clock) -> None:
    registration_services.create_form(director, scheduled, FormType.EVALUATION)
    person = register(scheduled)[0]
    Registration.objects.filter(pk=person.pk).update(attendance_confirmed=True, confirmation_method="email")
    clock(datetime.combine(scheduled.end_date + timedelta(days=1), time(9)))
    client.force_login(director)
    response = client.get(reverse("activities:detail", args=[scheduled.pk]))
    assert response.status_code == 200
    assert status_of(scheduled) == "Conducted"
    assert mail.outbox == []  # page loads never send emails
    assert Registration.objects.get(pk=person.pk).evaluation_link_sent_at is None


def test_activities_list_load_refreshes_statuses(client, director, scheduled, clock) -> None:
    clock(datetime.combine(scheduled.start_date, time(10)))
    client.force_login(director)
    assert client.get(reverse("activities:list")).status_code == 200
    assert status_of(scheduled) == "Ongoing"


def test_history_shows_system(client, director, scheduled) -> None:
    services.apply_automatic_statuses(now=manila(scheduled.start_date, 9))
    client.force_login(director)
    html = client.get(reverse("activities:detail", args=[scheduled.pk]) + "?tab=history").content.decode()
    assert "System changed the status of" in html


def test_setup_schedules_adds_the_five_minute_job() -> None:
    call_command("setup_schedules")
    call_command("setup_schedules")
    schedule = Schedule.objects.get(name="Activity statuses and evaluation links")
    assert Schedule.objects.filter(name=schedule.name).count() == 1
    assert schedule.func == "activities.tasks.run_activity_automation"
    assert schedule.schedule_type == Schedule.MINUTES and schedule.minutes == 5 and schedule.repeats == -1


def test_the_task_moves_statuses(scheduled, clock) -> None:
    clock(datetime.combine(scheduled.start_date, time(9)))
    result = tasks.run_activity_automation()
    assert result["status_changes"] == 1 and status_of(scheduled) == "Ongoing"
