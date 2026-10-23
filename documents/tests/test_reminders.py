"""Reminders: due soon, overdue, skips, never twice (database constraint), the commands and the schedule."""

import logging
from datetime import date, timedelta

import pytest
from django.core.management import call_command
from django.db import IntegrityError
from django.utils import timezone
from django_q.models import Schedule

from activities.models import ActivityStatus
from documents import services, tasks
from documents.models import DocumentType
from notifications.models import Notification

from .conftest import at

pytestmark = pytest.mark.django_db

START = date(2026, 11, 10)  # Invitation Letter due Nov 3, Activity Design Oct 31, Request Letter Nov 5


def reminders(kind: str | None = None):
    queryset = Notification.objects.filter(type__in=Notification.REMINDER_TYPES)
    return queryset.filter(type=kind) if kind else queryset


def types_of(kind: str) -> set[str]:
    return set(reminders(kind).values_list("document_type__name", flat=True))


@pytest.fixture
def activity(make_activity):
    return make_activity(start=START, end=START + timedelta(days=1))


# --- Due soon ---------------------------------------------------------------------------------------


def test_due_soon_within_the_window(activity, director) -> None:
    # Oct 31: Activity Design due today (0 days), Invitation Letter in 3 days, Request Letter in 5.
    result = services.send_document_reminders(now=at(date(2026, 10, 31)))
    assert types_of(Notification.DOCUMENT_DUE_SOON) == {"Activity Design", "Invitation Letter"}
    assert result["due_soon"] == 2 and result["overdue"] == 0
    reminder = reminders(Notification.DOCUMENT_DUE_SOON).get(document_type__name="Invitation Letter")
    assert reminder.member == director and reminder.activity == activity
    assert "Nov 3, 2026" in reminder.message and not reminder.is_read


def test_due_soon_window_comes_from_the_setting(activity, settings) -> None:
    settings.DOCUMENT_REMINDER_DAYS_BEFORE = 5
    services.send_document_reminders(now=at(date(2026, 10, 31)))
    assert "Request Letter" in types_of(Notification.DOCUMENT_DUE_SOON)


def test_no_due_soon_once_a_document_is_uploaded(activity, upload, director) -> None:
    upload(director, activity)  # Invitation Letter, still For Review
    services.send_document_reminders(now=at(date(2026, 10, 31)))
    assert "Invitation Letter" not in types_of(Notification.DOCUMENT_DUE_SOON)


# --- Overdue ----------------------------------------------------------------------------------------


def test_overdue_when_past_due_and_not_approved(activity, upload, director, head) -> None:
    upload(director, activity)  # Invitation Letter For Review: still overdue
    approved = upload(director, activity, document_type=DocumentType.objects.get(name="Activity Design"))
    services.approve_document(head, approved)

    result = services.send_document_reminders(now=at(date(2026, 11, 4)))  # day after the invitation's due date
    assert types_of(Notification.DOCUMENT_OVERDUE) == {"Invitation Letter"}
    assert result["overdue"] == 1
    assert "was due on Nov 3, 2026" in reminders(Notification.DOCUMENT_OVERDUE).get().message


def test_not_overdue_on_the_due_date_itself(activity) -> None:
    services.send_document_reminders(now=at(date(2026, 11, 3), 23))
    assert "Invitation Letter" not in types_of(Notification.DOCUMENT_OVERDUE)
    assert "Invitation Letter" in types_of(Notification.DOCUMENT_DUE_SOON)


def test_due_soon_and_overdue_are_separate_reminders(activity) -> None:
    services.send_document_reminders(now=at(date(2026, 11, 1)))
    services.send_document_reminders(now=at(date(2026, 11, 4)))
    invitation = reminders().filter(document_type__name="Invitation Letter")
    assert sorted(invitation.values_list("type", flat=True)) == [Notification.DOCUMENT_DUE_SOON, Notification.DOCUMENT_OVERDUE]


# --- Skips ----------------------------------------------------------------------------------------


def test_draft_activities_are_skipped(make_activity) -> None:
    make_activity(status=ActivityStatus.DRAFT, start=START)
    assert services.send_document_reminders(now=at(date(2026, 11, 30)))["overdue"] == 0
    assert not reminders().exists()


@pytest.mark.parametrize("status", [ActivityStatus.ONGOING, ActivityStatus.CONDUCTED])
def test_other_statuses_get_reminders(make_activity, status) -> None:
    make_activity(status=status, start=START)
    services.send_document_reminders(now=at(date(2026, 11, 30)))
    assert reminders(Notification.DOCUMENT_OVERDUE).count() == 5


def test_inactive_types_are_skipped(activity) -> None:
    DocumentType.objects.filter(name="Invitation Letter").update(is_active=False)
    services.send_document_reminders(now=at(date(2026, 11, 30)))
    assert "Invitation Letter" not in types_of(Notification.DOCUMENT_OVERDUE)
    assert len(types_of(Notification.DOCUMENT_OVERDUE)) == 4


def test_deactivated_creator_is_skipped_and_logged(activity, director, caplog) -> None:
    director.is_active = False
    director.save()
    with caplog.at_level(logging.WARNING, logger="documents.reminders"):
        result = services.send_document_reminders(now=at(date(2026, 11, 30)))
    assert not reminders().exists()
    assert result["skipped_inactive_creator"] == 5
    assert f"activity {activity.pk}" in caplog.text and "deactivated" in caplog.text


# --- Never twice ------------------------------------------------------------------------------------


def test_running_twice_creates_nothing_new(activity) -> None:
    first = services.send_document_reminders(now=at(date(2026, 11, 30)))
    count = Notification.objects.count()
    second = services.send_document_reminders(now=at(date(2026, 11, 30)))
    assert first["overdue"] == 5
    assert second["overdue"] == 0 and second["due_soon"] == 0 and second["already_sent"] == 5
    assert Notification.objects.count() == count


def test_database_refuses_a_duplicate_reminder(activity, director, doc_type) -> None:
    values = {"member": director, "activity": activity, "document_type": doc_type, "type": Notification.DOCUMENT_OVERDUE}
    Notification.objects.create(message="first", **values)
    with pytest.raises(IntegrityError):
        Notification.objects.create(message="second", **values)


def test_returned_notifications_are_not_limited(activity, director, doc_type) -> None:
    values = {"member": director, "activity": activity, "document_type": doc_type, "type": Notification.DOCUMENT_RETURNED}
    Notification.objects.create(message="first", **values)
    Notification.objects.create(message="second", **values)
    assert Notification.objects.count() == 2


def test_reminders_for_each_creator_separately(make_activity, head) -> None:
    make_activity(start=START)
    make_activity(start=START, created_by=head, title="Another")
    services.send_document_reminders(now=at(date(2026, 11, 30)))
    assert reminders().filter(member=head).count() == 5 and reminders().count() == 10


# --- Commands, task and schedule --------------------------------------------------------------------


def test_send_document_reminders_command(make_activity, capsys) -> None:
    make_activity(start=timezone.localdate() - timedelta(days=30))  # everything overdue today
    call_command("send_document_reminders")
    assert "overdue: 5 new" in capsys.readouterr().out
    call_command("send_document_reminders")
    assert "overdue: 0 new" in capsys.readouterr().out and reminders().count() == 5


def test_task_runs_the_service(make_activity) -> None:
    make_activity(start=timezone.localdate() - timedelta(days=30))
    assert tasks.run_document_reminders()["overdue"] == 5


def test_setup_schedules_twice_creates_one_schedule() -> None:
    call_command("setup_schedules")
    call_command("setup_schedules")
    schedule = Schedule.objects.get(name=tasks.REMINDER_SCHEDULE_NAME)
    assert Schedule.objects.filter(name=tasks.REMINDER_SCHEDULE_NAME).count() == 1
    assert Schedule.objects.count() == 4  # plus the startup sync (Step 7), indexing (Step 8), certificate retry (Step 9)
    assert schedule.func == "documents.tasks.run_document_reminders"
    assert schedule.schedule_type == Schedule.DAILY and schedule.repeats == -1
    local = timezone.localtime(schedule.next_run)
    assert (local.hour, local.minute) == (8, 0)
    assert schedule.next_run > timezone.now()


def test_setup_schedules_keeps_the_next_run_and_fixes_a_wrong_time() -> None:
    reminders = Schedule.objects.filter(name=tasks.REMINDER_SCHEDULE_NAME)
    call_command("setup_schedules")
    first_run = reminders.get().next_run
    call_command("setup_schedules")
    assert reminders.get().next_run == first_run  # unchanged on a second run

    reminders.update(next_run=at(date(2026, 12, 1), 15))  # someone changed it to 3 PM
    call_command("setup_schedules")
    assert timezone.localtime(reminders.get().next_run).hour == 8


def test_setup_schedules_removes_duplicates_with_its_name() -> None:
    for _ in range(2):
        Schedule.objects.create(name=tasks.REMINDER_SCHEDULE_NAME, func=tasks.REMINDER_TASK, schedule_type=Schedule.DAILY)
    call_command("setup_schedules")
    assert Schedule.objects.filter(name=tasks.REMINDER_SCHEDULE_NAME).count() == 1


@pytest.mark.parametrize(
    "now_hour, expected_day",
    [(7, 20), (8, 21), (15, 21)],  # before 8:00 -> today; at or after 8:00 -> tomorrow
)
def test_next_reminder_run(now_hour, expected_day) -> None:
    run = timezone.localtime(tasks.next_reminder_run(at(date(2026, 10, 20), now_hour)))
    assert (run.day, run.hour, run.minute) == (expected_day, 8, 0)
