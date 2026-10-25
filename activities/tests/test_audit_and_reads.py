"""Append-only audit log, the Audit Log tab, read services, and value formatting."""

import inspect
from datetime import date, datetime, timedelta
from datetime import timezone as dt_timezone
from decimal import Decimal

import pytest
from django.urls import get_resolver
from django.utils import timezone

from activities import services
from activities.formatting import format_audit_value
from activities.models import ActivityAuditLog, ActivityStatus, AuditLogIsAppendOnly, ParticipantCategory, ResourcePerson

from .conftest import TEST_NOW, at

# --- Append-only audit log ---------------------------------------------------------------


@pytest.mark.django_db
def test_audit_row_cannot_be_changed_or_deleted(new_activity) -> None:
    row = new_activity.audit_logs.get()
    row.new_value = "Conducted"
    with pytest.raises(AuditLogIsAppendOnly):
        row.save()
    with pytest.raises(AuditLogIsAppendOnly):
        row.delete()
    with pytest.raises(AuditLogIsAppendOnly):
        ActivityAuditLog.objects.filter(pk=row.pk).update(new_value="x")
    with pytest.raises(AuditLogIsAppendOnly):
        ActivityAuditLog.objects.all().delete()
    assert new_activity.audit_logs.get().new_value == "Draft"


def test_no_update_or_delete_path_in_code_or_api() -> None:
    """The service module has no function that edits or deletes audit rows, and the only
    audit-log URL is read-only (GET)."""
    functions = [
        name
        for name, function in inspect.getmembers(services, inspect.isfunction)
        if "audit" in name and function.__module__ == services.__name__  # defined there, not imported
    ]
    # get_audit_log and recent_audit_entries (the dashboard's "Recent changes", F1) only read.
    assert sorted(functions) == ["_write_audit", "get_audit_log", "recent_audit_entries"]

    audit_urls = [p for p in get_resolver("activities.urls").url_patterns if "audit" in str(p.pattern)]
    assert [p.name for p in audit_urls] == ["api_audit_log"]


@pytest.mark.django_db
def test_audit_log_api_rejects_writes(client, director, new_activity) -> None:
    client.force_login(director)
    url = f"/activities/api/{new_activity.pk}/audit-log/"
    assert client.post(url).status_code == 405
    assert client.delete(url).status_code == 405


# --- get_audit_log (Audit Log tab) ---------------------------------------------------------


@pytest.mark.django_db
def test_audit_log_newest_first_with_total(director, new_activity) -> None:
    for venue in ["Room 1", "Room 2", "Room 3"]:
        services.update_activity(director, new_activity, {"venue": venue})
    result = services.get_audit_log(new_activity, per_page=2)
    assert result.total == 4 and result.num_pages == 2
    assert [row.new_value for row in result.items] == ["Room 3", "Room 2"]
    assert [row.new_value for row in services.get_audit_log(new_activity, page=2, per_page=2).items] == ["Room 1", "Draft"]


@pytest.mark.django_db
def test_audit_log_filters(director, new_activity) -> None:
    services.update_activity(director, new_activity, {"venue": "Room 1", "max_slots": 40})
    assert [r.field_changed for r in services.get_audit_log(new_activity, field="Venue").items] == ["Venue"]

    today = timezone.localdate()
    assert services.get_audit_log(new_activity, date_from=today, date_to=today).total == 3
    assert services.get_audit_log(new_activity, date_from=today + timedelta(days=1)).total == 0
    assert services.get_audit_log(new_activity, date_to=today - timedelta(days=1)).total == 0


# --- registered_count, slots_remaining, is_registration_open -------------------------------


@pytest.mark.django_db
def test_counts_are_calculated(new_activity, register) -> None:
    register(new_activity, 3)
    assert services.registered_count(new_activity) == 3
    assert services.slots_remaining(new_activity) == 27


@pytest.mark.django_db
def test_registration_open_when_scheduled_before_deadline_with_slots(new_activity, move_to) -> None:
    move_to(new_activity, "Scheduled")
    assert services.is_registration_open(new_activity) is True


@pytest.mark.django_db
@pytest.mark.parametrize("status", ["Draft", "Ongoing", "Conducted"])
def test_registration_closed_unless_scheduled(new_activity, move_to, status) -> None:
    if status != "Draft":
        move_to(new_activity, status)
    assert services.is_registration_open(new_activity) is False


@pytest.mark.django_db
def test_registration_closed_after_deadline(new_activity, move_to) -> None:
    move_to(new_activity, "Scheduled")
    deadline = new_activity.registration_deadline
    assert services.is_registration_open(new_activity, now=deadline) is True  # on the deadline: still open
    assert services.is_registration_open(new_activity, now=deadline + timedelta(seconds=1)) is False


@pytest.mark.django_db
def test_registration_closed_when_full(director, new_activity, move_to, register) -> None:
    services.update_activity(director, new_activity, {"max_slots": 2})
    move_to(new_activity, "Scheduled")
    register(new_activity, 1)
    assert services.is_registration_open(new_activity) is True
    register(new_activity, 1)
    assert services.slots_remaining(new_activity) == 0
    assert services.is_registration_open(new_activity) is False


# --- list_activities, stats, calendar, detail ----------------------------------------------


@pytest.fixture
def three_activities(director, valid_data, move_to, register):
    """Seminar (Nov 2026, 3 registered), Workshop (Dec 2026, Scheduled), Training (Jan 2027, Faculty)."""
    from activities.models import ActivityType, ParticipantCategory

    def make(title, type_name, start, category="Incubatees"):
        return services.create_activity(
            director,
            valid_data(
                title=title,
                activity_type=ActivityType.objects.get(name=type_name),
                participant_categories=[ParticipantCategory.objects.get(name=category)],
                start_date=start,
                end_date=start + timedelta(days=1),
                registration_deadline=at(start - timedelta(days=1)),
                evaluation_deadline=at(start + timedelta(days=8)),
            ),
            now=TEST_NOW,
        )

    seminar = make("IP Seminar", "Seminar", date(2026, 11, 10))
    register(seminar, 3)
    workshop = make("Product Workshop", "Workshop", date(2026, 12, 5))
    move_to(workshop, "Scheduled")
    training = make("Market Training", "Training", date(2027, 1, 20), category="Faculty")
    return seminar, workshop, training


@pytest.mark.django_db
def test_list_search_filter_sort(three_activities) -> None:
    seminar, workshop, training = three_activities
    titles = lambda result: [a.title for a in result.items]  # noqa: E731

    assert titles(services.list_activities()) == ["Market Training", "Product Workshop", "IP Seminar"]  # newest first
    assert titles(services.list_activities({"sort": "title"})) == ["IP Seminar", "Market Training", "Product Workshop"]
    assert titles(services.list_activities({"search": "work"})) == ["Product Workshop"]
    assert titles(services.list_activities({"activity_type": seminar.activity_type_id})) == ["IP Seminar"]
    assert titles(services.list_activities({"status": workshop.status_id})) == ["Product Workshop"]
    faculty = ParticipantCategory.objects.get(name="Faculty")
    assert titles(services.list_activities({"participant_category": faculty.pk})) == ["Market Training"]
    assert titles(services.list_activities({"date_from": date(2026, 12, 1), "date_to": date(2026, 12, 31)})) == ["Product Workshop"]
    assert titles(services.list_activities({"sort": "-registered"}))[0] == "IP Seminar"


@pytest.mark.django_db
def test_list_rows_have_registered_count(three_activities) -> None:
    rows = {a.title: a.registered for a in services.list_activities().items}
    assert rows == {"IP Seminar": 3, "Product Workshop": 0, "Market Training": 0}


@pytest.mark.django_db
def test_list_rejects_unknown_sort() -> None:
    with pytest.raises(services.ActivityError, match="Unknown sort"):
        services.list_activities({"sort": "budget_amount"})


@pytest.mark.django_db
def test_activity_stats(three_activities, director, move_to) -> None:
    seminar, workshop, _ = three_activities
    move_to(seminar, "Conducted")
    november = timezone.make_aware(datetime(2026, 11, 20, 12, 0))
    stats = services.activity_stats(now=november)
    assert stats == {
        "total": 3,
        "conducted_this_month": 1,  # the seminar ended in November
        "open_for_registration": 1,  # the Scheduled workshop, deadline Dec 4
        "conducted_total": 1,
    }


@pytest.mark.django_db
def test_activities_between_for_calendar(three_activities) -> None:
    titles = [a.title for a in services.activities_between(date(2026, 11, 1), date(2026, 12, 31))]
    assert titles == ["IP Seminar", "Product Workshop"]


@pytest.mark.django_db
def test_activity_detail(director, new_activity, register) -> None:
    person = ResourcePerson.objects.create(name="Dr. Reyes", email="reyes@example.com")
    services.add_resource_person(director, new_activity, person, "Pitching")
    register(new_activity, 4)

    detail = services.get_activity_detail(new_activity)
    assert detail["created_by"] == director.name
    assert (detail["registered_count"], detail["slots_remaining"], detail["max_slots"]) == (4, 26, 30)
    assert detail["status"] == "Draft" and detail["allowed_status_moves"] == ["Scheduled"]
    assert detail["registration_deadline"] == new_activity.registration_deadline
    assert detail["evaluation_deadline"] == new_activity.evaluation_deadline
    assert detail["resource_persons"] == [
        {"resource_person_id": person.pk, "name": "Dr. Reyes", "email": "reyes@example.com", "contact_number": "", "topic": "Pitching"}
    ]


# --- Formatting ---------------------------------------------------------------------------


@pytest.mark.django_db
def test_format_audit_value(roles) -> None:
    assert format_audit_value(date(2026, 10, 5)) == "Oct 5, 2026"
    assert format_audit_value(timezone.make_aware(datetime(2026, 10, 15, 17, 0))) == "Oct 15, 2026 5:00 PM"
    assert format_audit_value(timezone.make_aware(datetime(2026, 10, 15, 0, 30))) == "Oct 15, 2026 12:30 AM"
    assert format_audit_value(Decimal("30000")) == "₱30,000.00"
    assert format_audit_value(ActivityStatus.objects.get(name="Draft")) == "Draft"
    assert format_audit_value("8:00 AM - 5:00 PM") == "8:00 AM - 5:00 PM"
    assert format_audit_value(None) == "" and format_audit_value("") == ""
    assert format_audit_value(40) == "40"


def test_utc_datetimes_are_shown_in_manila_time() -> None:
    utc_9am = datetime(2026, 10, 15, 9, 0, tzinfo=dt_timezone.utc)
    assert format_audit_value(utc_9am) == "Oct 15, 2026 5:00 PM"  # Manila is UTC+8
