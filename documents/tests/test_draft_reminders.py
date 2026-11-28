"""Reminders for a Draft's Activity Design and Request Letter (ASSUMPTIONS 417, NAVIGATU: staff are reminded while they
are missing). A Draft created with them (creation_documents_required) gets the existing reminder rule and timing (due 5
days before the start, ASSUMPTIONS 416; "due soon" within DOCUMENT_REMINDER_DAYS_BEFORE days) for those two types only,
to the creator AND every active Director. Other types, older Drafts and the dashboard numbers keep the current rule.
Every test sets the time with the `clock` fixture."""

from datetime import date, datetime

import pytest

from activities import attention
from activities.models import ActivityStatus
from documents import services
from documents.models import DocumentType
from notifications.models import Notification
from reports import metrics

from .conftest import make_file

pytestmark = pytest.mark.django_db

DESIGN, REQUEST, SHEET = "Activity Design", "Request Letter", "Attendance Sheet"
START = date(2026, 12, 7)  # both due on Dec 2 (start - 5)
DUE_SOON = datetime(2026, 11, 30, 9, 0)  # 2 days before the due date: "due soon" (within 3 days)
OVERDUE = datetime(2026, 12, 3, 9, 0)  # the day after the due date
TOO_EARLY = datetime(2026, 11, 25, 9, 0)  # 7 days before the due date: nothing yet


@pytest.fixture
def draft(make_activity, head):
    """Factory: a Draft created by the Incubation Project Head through the New activity form (the flag set)."""

    def make(start: date = START, end: date | None = None, flag: bool = True, status: str = ActivityStatus.DRAFT):
        activity = make_activity(status, start=start, end=end, created_by=head)
        activity.creation_documents_required = flag
        activity.save(update_fields=["creation_documents_required"])
        return activity

    return make


def reminders(activity, kind: str | None = None) -> set[tuple[str, str]]:
    """{(member email, document type name)} of the activity's reminders (of one kind)."""
    rows = Notification.objects.filter(activity=activity, type__in=[kind] if kind else
                                       [Notification.DOCUMENT_DUE_SOON, Notification.DOCUMENT_OVERDUE])
    return set(rows.values_list("member__email", "document_type__name"))


def both_to(*members) -> set[tuple[str, str]]:
    return {(member.email, name) for member in members for name in (DESIGN, REQUEST)}


def test_due_soon_goes_to_the_creator_and_every_director(draft, head, director, second_director, clock) -> None:
    activity = draft()
    clock(DUE_SOON)
    result = services.send_document_reminders()
    assert result["due_soon"] == 6 and result["overdue"] == 0  # 2 types x (creator + 2 Directors)
    assert reminders(activity, Notification.DOCUMENT_DUE_SOON) == both_to(head, director, second_director)
    message = Notification.objects.filter(activity=activity, document_type__name=DESIGN).first().message
    assert message == 'Reminder: the Activity Design for "Pitching Workshop" is due on Dec 2, 2026 and has not been uploaded yet.'


def test_nothing_before_the_reminder_window(draft, clock) -> None:
    activity = draft()
    clock(TOO_EARLY)
    services.send_document_reminders()
    assert reminders(activity) == set()


def test_overdue_after_the_due_date_and_never_twice(draft, head, director, clock) -> None:
    activity = draft()
    clock(OVERDUE)
    assert services.send_document_reminders()["overdue"] == 4
    assert reminders(activity, Notification.DOCUMENT_OVERDUE) == both_to(head, director)
    again = services.send_document_reminders()
    assert again["overdue"] == 0 and again["already_sent"] == 4


def test_only_the_two_creation_types_never_other_types(draft, head, director, clock) -> None:
    """A Draft whose dates have passed: Drafts get no reminder for the Attendance Sheet (a payment document, counted
    only once Conducted since ASSUMPTIONS 425, so the checklist does not show it overdue either)."""
    activity = draft(start=date(2026, 11, 2), end=date(2026, 11, 3))
    clock(datetime(2026, 11, 21, 9, 0))
    assert services.send_document_reminders()["overdue"] == 4
    assert {name for _, name in reminders(activity)} == {DESIGN, REQUEST}
    sheet = {i.document_type.name: i for i in services.document_checklist(activity)["required"]}[SHEET]
    assert not sheet.is_overdue and not sheet.counts_now


def test_an_uploaded_one_stops_its_reminders(draft, head, director, upload, clock) -> None:
    activity = draft()
    upload(head, activity, make_file("design.pdf"), DocumentType.objects.get(name=DESIGN))  # waiting for review is enough
    clock(OVERDUE)
    services.send_document_reminders()
    assert reminders(activity) == {(head.email, REQUEST), (director.email, REQUEST)}


def test_a_director_who_created_it_gets_one_copy(draft, make_activity, director, clock) -> None:
    activity = make_activity(ActivityStatus.DRAFT, start=START, created_by=director)
    activity.creation_documents_required = True
    activity.save(update_fields=["creation_documents_required"])
    clock(DUE_SOON)
    assert services.send_document_reminders()["due_soon"] == 2
    assert reminders(activity) == both_to(director)


def test_a_deactivated_creator_is_skipped_but_the_director_is_told(draft, head, director, clock) -> None:
    activity = draft()
    head.is_active = False
    head.save(update_fields=["is_active"])
    clock(DUE_SOON)
    result = services.send_document_reminders()
    assert result["skipped_inactive_creator"] == 2 and result["due_soon"] == 2
    assert reminders(activity) == both_to(director)


def test_older_drafts_still_get_no_reminders(draft, clock) -> None:
    """A Draft from the admin or the demo seed (the flag False) keeps the rule before: no reminders."""
    activity = draft(flag=False)
    clock(OVERDUE)
    services.send_document_reminders()
    assert reminders(activity) == set()


def test_scheduled_reminders_still_go_to_the_creator_only(draft, head, director, clock) -> None:
    activity = draft(status=ActivityStatus.SCHEDULED)
    clock(OVERDUE)
    services.send_document_reminders()
    assert reminders(activity) == both_to(head)


def test_after_scheduling_nobody_gets_the_same_reminder_twice(draft, head, director, clock) -> None:
    activity = draft()
    clock(OVERDUE)
    services.send_document_reminders()
    activity.status = ActivityStatus.objects.get(name=ActivityStatus.SCHEDULED)  # e.g. a teammate's admin change
    activity.save(update_fields=["status"])
    assert services.send_document_reminders()["overdue"] == 0  # the creator already has them (unique constraint)


def test_needs_attention_shows_them_on_the_draft(draft, clock) -> None:
    """The activity page and the Activities list (activities.attention: the reminders' own rule) show due soon /
    overdue on a Draft too."""
    activity = draft()
    clock(DUE_SOON)
    texts = [item.text for item in attention.activity_attention(activity)]
    assert f"Required documents due soon and not uploaded: {DESIGN}, {REQUEST}." in texts
    clock(OVERDUE)
    texts = [item.text for item in attention.activity_attention(activity)]
    assert f"Overdue required documents: {DESIGN}, {REQUEST}." in texts
    assert attention.attention_counts([activity])[activity.pk] >= 1


def test_the_dashboard_numbers_count_the_drafts_creation_documents(draft, clock) -> None:
    """ASSUMPTIONS 472 (owner, replaces 417's "Drafts stay out"): the dashboard's overdue count (S3) and its Needs
    attention rows count a flagged Draft's Activity Design and Request Letter, like the reminders."""
    draft()
    clock(OVERDUE)
    filters = metrics.ReportFilters(date(2026, 1, 1), date(2026, 12, 31))
    assert metrics.documents_now(filters)["overdue"] == 2
    rows = metrics.document_attention(filters)
    assert {row["document_type"] for row in rows["overdue"]} == {DESIGN, REQUEST}
