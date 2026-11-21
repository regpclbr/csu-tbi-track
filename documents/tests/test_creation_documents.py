"""The Activity Design and the Request Letter exist when an activity is created (ASSUMPTIONS 405, NAVIGATU): on
activities created with them (Activity.creation_documents_required) they are needed before Scheduled (uploaded is
enough), never overdue once uploaded, and the only one of a type cannot be deleted outside Draft. Activities created
before (the flag False) keep the current rules. Every upload waits for the Director (item 2 of the batch)."""

from datetime import date, timedelta

import pytest
from django.core import mail

from activities import attention
from activities import services as activity_services
from activities.models import ActivityStatus
from documents import services
from documents.models import Document, DocumentType
from notifications.models import Notification
from reports import metrics

from .conftest import TEST_NOW, make_file

pytestmark = pytest.mark.django_db

DESIGN, REQUEST = "Activity Design", "Request Letter"
SOON = date(2026, 10, 23)  # starts 3 days after TEST_NOW: both types are past their due date (start - 5, ASSUMPTIONS 416)


def type_named(name: str) -> DocumentType:
    return DocumentType.objects.get(name=name)


@pytest.fixture
def new_activity(make_activity):
    """Factory: an activity created under the new rule (the flag set, as create_activity_with_documents does)."""

    def make(status: str = ActivityStatus.DRAFT, start: date = SOON, flag: bool = True, **kwargs):
        activity = make_activity(status, start=start, **kwargs)
        activity.creation_documents_required = flag
        activity.save(update_fields=["creation_documents_required"])
        return activity

    return make


def add(upload, member, activity, name: str) -> Document:
    return upload(member, activity, make_file(f"{name}.pdf"), type_named(name))


def required_by_name(activity) -> dict:
    return {item.document_type.name: item for item in services.document_checklist(activity, TEST_NOW)["required"]}


# --- Which types --------------------------------------------------------------------------------------------------


def test_the_two_types_from_the_setting(roles) -> None:
    assert sorted(services.creation_required_types().values_list("name", flat=True)) == [DESIGN, REQUEST]


def test_a_deactivated_or_optional_type_drops_out(roles) -> None:
    DocumentType.objects.filter(name=REQUEST).update(is_active=False)
    DocumentType.objects.filter(name=DESIGN).update(is_required=False)
    assert not services.creation_required_types().exists()


def test_existing_and_seeded_activities_keep_the_old_rules(make_activity) -> None:
    assert make_activity().creation_documents_required is False  # the migration's default for every existing row


# --- Draft -> Scheduled ---------------------------------------------------------------------------------------------


def test_scheduling_needs_both_uploaded(new_activity, director, upload) -> None:
    activity = new_activity(start=date(2026, 12, 1))
    with pytest.raises(activity_services.ActivityError) as refused:
        activity_services.change_status(director, activity, ActivityStatus.SCHEDULED)
    assert str(refused.value) == ("Upload the Activity Design and the Request Letter before scheduling this activity "
                                  "(Documents tab).")
    add(upload, director, activity, DESIGN)
    with pytest.raises(activity_services.ActivityError) as refused:
        activity_services.change_status(director, activity, ActivityStatus.SCHEDULED)
    assert str(refused.value) == "Upload the Request Letter before scheduling this activity (Documents tab)."
    activity.refresh_from_db()
    assert activity.status.name == ActivityStatus.DRAFT and activity.audit_logs.count() == 0


def test_uploaded_is_enough_waiting_or_returned(new_activity, director, head, upload) -> None:
    activity = new_activity(start=date(2026, 12, 1))
    add(upload, head, activity, DESIGN)  # waiting for the Director
    returned = add(upload, head, activity, REQUEST)
    services.return_document(director, returned, "Wrong date", now=TEST_NOW)
    activity_services.change_status(director, activity, ActivityStatus.SCHEDULED)
    activity.refresh_from_db()
    assert activity.status.name == ActivityStatus.SCHEDULED


def test_old_drafts_schedule_without_them(new_activity, director) -> None:
    activity = new_activity(start=date(2026, 12, 1), flag=False)
    activity_services.change_status(director, activity, ActivityStatus.SCHEDULED)
    activity.refresh_from_db()
    assert activity.status.name == ActivityStatus.SCHEDULED


def test_certificate_evidence_does_not_count(new_activity, director, upload, certificate_for) -> None:
    activity = new_activity(start=date(2026, 12, 1))
    certificate = certificate_for(activity)
    for name in (DESIGN, REQUEST):
        services.upload_document(director, activity, type_named(name), make_file(), related_certificate=certificate)
    assert services.missing_creation_types(activity) == [DESIGN, REQUEST]


# --- Never overdue once uploaded ------------------------------------------------------------------------------------


def test_uploaded_at_creation_is_never_overdue(new_activity, director, head, upload) -> None:
    """Created 3 days before the start: both are past their due dates at once, but they are waiting for the Director,
    not overdue. The checklist, reminders, Needs attention and the reports' SQL agree."""
    activity = new_activity(ActivityStatus.SCHEDULED)
    add(upload, head, activity, DESIGN)
    services.return_document(director, add(upload, head, activity, REQUEST), "Sign it", now=TEST_NOW)
    items = required_by_name(activity)
    assert (items[DESIGN].state, items[DESIGN].is_overdue) == ("For Review", False)
    assert (items[REQUEST].state, items[REQUEST].is_overdue) == ("For Review", False)

    assert services.send_document_reminders(TEST_NOW)["overdue"] == 0
    texts = [item.text for item in attention.activity_attention(activity, TEST_NOW)]
    assert not any(DESIGN in text or REQUEST in text for text in texts)
    assert "1 document waiting for approval." in texts and "1 document returned to the uploader." in texts

    filters = metrics.ReportFilters(date(2026, 1, 1), date(2026, 12, 31))
    assert metrics.documents_now(filters, TEST_NOW)["overdue"] == 0
    assert metrics.document_attention(filters, TEST_NOW)["overdue"] == []
    sql = metrics.document_compliance(activity.__class__.objects.filter(pk=activity.pk), TEST_NOW)["activities"][0]
    assert {i["document_type"]: i["is_overdue"] for i in sql["items"] if i["document_type"] in (DESIGN, REQUEST)} == {
        DESIGN: False, REQUEST: False}


def test_old_activities_keep_the_current_overdue_rule(new_activity, head, upload) -> None:
    activity = new_activity(ActivityStatus.SCHEDULED, flag=False)
    add(upload, head, activity, DESIGN)
    items = required_by_name(activity)
    assert (items[DESIGN].state, items[DESIGN].is_overdue) == ("For Review", True)
    assert (items[REQUEST].state, items[REQUEST].is_overdue) == ("Missing", True)
    assert services.send_document_reminders(TEST_NOW)["overdue"] == 2


def test_a_new_draft_without_them_shows_missing_and_overdue(new_activity) -> None:
    """A Draft created without them (the New activity form's default since ASSUMPTIONS 417, or the JSON API) shows
    Missing / overdue and gets their reminders (to the creator and the Director; details in test_draft_reminders)."""
    activity = new_activity(ActivityStatus.DRAFT)
    items = required_by_name(activity)
    assert (items[DESIGN].state, items[DESIGN].is_overdue) == ("Missing", True)
    assert services.send_document_reminders(TEST_NOW)["overdue"] == 2  # the creator is the Director: one copy each


def test_other_required_types_are_unchanged(new_activity) -> None:
    activity = new_activity(ActivityStatus.ONGOING, start=date(2026, 10, 1), end=date(2026, 10, 2))
    sheet = required_by_name(activity)["Attendance Sheet"]
    assert (sheet.state, sheet.is_overdue) == ("Missing", True)


# --- The only Activity Design / Request Letter is never deleted outside Draft ------------------------------------------


def test_the_only_one_cannot_be_deleted_once_scheduled(new_activity, head, upload) -> None:
    activity = new_activity(ActivityStatus.SCHEDULED, start=date(2026, 12, 1))
    design = add(upload, head, activity, DESIGN)
    actions = services.allowed_actions(head, Document.objects.select_related("activity", "document_type", "status").get(pk=design.pk))
    assert actions["delete"] is False and actions["replace"] is True
    with pytest.raises(services.DocumentError) as refused:
        services.delete_document(head, design)
    assert str(refused.value) == ("This is the activity's only Activity Design and the activity is Scheduled. "
                                  "Replace the file instead.")
    assert Document.objects.filter(pk=design.pk).exists()
    services.replace_document_file(head, design, make_file("v2.pdf"), now=TEST_NOW)  # replacing stays allowed


def test_a_second_copy_or_a_draft_or_an_old_activity_can_be_deleted(new_activity, head, upload) -> None:
    scheduled = new_activity(ActivityStatus.SCHEDULED, start=date(2026, 12, 1))
    first = add(upload, head, scheduled, DESIGN)
    add(upload, head, scheduled, DESIGN)
    services.delete_document(head, first)
    draft = new_activity(ActivityStatus.DRAFT, start=date(2026, 12, 1), title="Draft")
    services.delete_document(head, add(upload, head, draft, DESIGN))
    old = new_activity(ActivityStatus.SCHEDULED, start=date(2026, 12, 1), flag=False, title="Old")
    services.delete_document(head, add(upload, head, old, DESIGN))
    assert Document.objects.count() == 1


# --- Every upload waits for the Director (item 2) -------------------------------------------------------------------


def test_every_upload_notifies_the_director_in_app_only(new_activity, director, head, upload) -> None:
    activity = new_activity(ActivityStatus.SCHEDULED, start=date(2026, 12, 1))
    for name in (DESIGN, REQUEST, "Invitation Letter"):
        add(upload, head, activity, name)
    waiting = Notification.objects.filter(member=director, type=Notification.DOCUMENT_WAITING_FOR_REVIEW)
    assert waiting.count() == 3 and mail.outbox == []
    assert set(services.review_queue(director)) == set(activity.documents.all())


def test_the_directors_own_upload_waits_in_their_queue_and_they_may_approve_it(new_activity, director, upload) -> None:
    """Owner decision 2026-11-18 (ASSUMPTIONS 344, 406): no notification to themselves, but it is in their review queue
    and in Needs attention, and they may approve it (the only approver by default)."""
    activity = new_activity(ActivityStatus.SCHEDULED, start=date(2026, 12, 1))
    own = add(upload, director, activity, DESIGN)
    assert not Notification.objects.filter(type=Notification.DOCUMENT_WAITING_FOR_REVIEW).exists()
    assert list(services.review_queue(director)) == [own]
    assert "1 document waiting for approval." in [i.text for i in attention.activity_attention(activity, TEST_NOW)]
    services.approve_document(director, own, now=TEST_NOW)
    own.refresh_from_db()
    assert own.review_state == Document.APPROVED and own.reviewed_by == director
