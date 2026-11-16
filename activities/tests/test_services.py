"""Create, edit, validation, status moves, resource persons and the audit log (activities/services.py)."""

import itertools
from datetime import date, timedelta
from decimal import Decimal

import pytest
from django.core.exceptions import PermissionDenied, ValidationError

from activities import services
from activities.models import Activity, ActivityAuditLog, ActivityType, ParticipantCategory, ResourcePerson

from .conftest import TEST_NOW, at


def audit_rows(activity: Activity) -> list[tuple[str, str, str]]:
    """(field_changed, old_value, new_value), oldest first."""
    return list(activity.audit_logs.order_by("log_id").values_list("field_changed", "old_value", "new_value"))


def error_for(excinfo, field: str) -> str:
    return " ".join(excinfo.value.message_dict[field])


# --- Create ------------------------------------------------------------------------------


@pytest.mark.django_db
def test_create_starts_as_draft_with_one_audit_row(director, valid_data) -> None:
    activity = services.create_activity(director, valid_data())
    assert activity.status.name == "Draft"
    assert activity.created_by == director
    assert audit_rows(activity) == [("Status", "", "Draft")]
    assert activity.audit_logs.get().changed_by == director


@pytest.mark.django_db
def test_description_is_optional(director, valid_data) -> None:
    data = valid_data()
    del data["description"]
    assert services.create_activity(director, data).description is None


@pytest.mark.django_db
@pytest.mark.parametrize("missing", ["title", "activity_type", "start_date", "budget_amount"])
def test_required_fields(director, valid_data, missing) -> None:
    data = valid_data()
    del data[missing]
    with pytest.raises(ValidationError) as excinfo:
        services.create_activity(director, data)
    assert error_for(excinfo, missing) == "This field is required."


@pytest.mark.django_db
def test_unknown_fields_are_refused(director, valid_data) -> None:
    with pytest.raises(services.ActivityError, match="status"):
        services.create_activity(director, valid_data(status="Conducted"))


# --- Validation rules --------------------------------------------------------------------


@pytest.mark.django_db
def test_end_date_on_or_after_start_date(director, valid_data) -> None:
    data = valid_data()
    with pytest.raises(ValidationError) as excinfo:
        services.create_activity(director, valid_data(end_date=data["start_date"] - timedelta(days=1)))
    assert error_for(excinfo, "end_date") == "End date must be on or after the start date."
    services.create_activity(director, valid_data(end_date=data["start_date"]))  # same day is fine


@pytest.mark.django_db
def test_registration_deadline_on_or_before_start_date(director, valid_data) -> None:
    start = valid_data()["start_date"]
    with pytest.raises(ValidationError) as excinfo:
        services.create_activity(director, valid_data(registration_deadline=at(start + timedelta(days=1), 8)))
    assert error_for(excinfo, "registration_deadline") == "Registration deadline must be on or before the start date."
    services.create_activity(director, valid_data(registration_deadline=at(start, 7)))  # on the start date is fine


@pytest.mark.django_db
def test_evaluation_deadline_on_or_after_end_date(director, valid_data) -> None:
    end = valid_data()["end_date"]
    with pytest.raises(ValidationError) as excinfo:
        services.create_activity(director, valid_data(evaluation_deadline=at(end - timedelta(days=1))))
    assert error_for(excinfo, "evaluation_deadline") == "Evaluation deadline must be on or after the end date."
    services.create_activity(director, valid_data(evaluation_deadline=at(end, 23)))  # on the end date is fine


@pytest.mark.django_db
@pytest.mark.parametrize("slots", [0, -5])
def test_max_slots_at_least_one(director, valid_data, slots) -> None:
    with pytest.raises(ValidationError) as excinfo:
        services.create_activity(director, valid_data(max_slots=slots))
    assert error_for(excinfo, "max_slots") == "Max slots must be at least 1."


@pytest.mark.django_db
def test_max_slots_not_below_registrations(director, new_activity, register) -> None:
    register(new_activity, 5)
    with pytest.raises(ValidationError) as excinfo:
        services.update_activity(director, new_activity, {"max_slots": 4})
    assert error_for(excinfo, "max_slots") == "Max slots cannot be lower than the 5 people already registered."
    services.update_activity(director, new_activity, {"max_slots": 5})  # equal is fine
    assert new_activity.max_slots == 5


@pytest.mark.django_db
def test_budget_zero_or_more(director, valid_data) -> None:
    with pytest.raises(ValidationError) as excinfo:
        services.create_activity(director, valid_data(budget_amount=Decimal("-0.01")))
    assert error_for(excinfo, "budget_amount") == "Budget amount must be zero or more."
    assert services.create_activity(director, valid_data(budget_amount=Decimal("0"))).budget_amount == 0


@pytest.mark.django_db
def test_activity_type_must_be_active(director, valid_data) -> None:
    ActivityType.objects.filter(name="Forum").update(is_active=False)
    with pytest.raises(ValidationError) as excinfo:
        services.create_activity(director, valid_data(activity_type=ActivityType.objects.get(name="Forum")))
    assert error_for(excinfo, "activity_type") == 'Activity type "Forum" is inactive. Choose an active type.'


@pytest.mark.django_db
def test_activity_with_a_type_deactivated_later_can_still_be_edited(director, new_activity) -> None:
    ActivityType.objects.filter(pk=new_activity.activity_type_id).update(is_active=False)
    services.update_activity(director, new_activity, {"venue": "Room 101"})
    assert new_activity.venue == "Room 101"


@pytest.mark.django_db
def test_several_errors_at_once(director, valid_data) -> None:
    with pytest.raises(ValidationError) as excinfo:
        services.create_activity(director, valid_data(max_slots=0, budget_amount=Decimal("-1")))
    assert set(excinfo.value.message_dict) == {"max_slots", "budget_amount"}


# --- Update and the audit log ------------------------------------------------------------


@pytest.mark.django_db
def test_update_writes_one_readable_row_per_changed_field(director, valid_data) -> None:
    activity = services.create_activity(
        director,
        valid_data(
            start_date=date(2026, 11, 15),
            end_date=date(2026, 11, 16),
            registration_deadline=at(date(2026, 11, 13), 17),
            evaluation_deadline=at(date(2026, 11, 23), 17),
        ),
        now=TEST_NOW,
    )
    services.update_activity(
        director,
        activity,
        {
            "venue": "Room 101",
            "activity_type": ActivityType.objects.get(name="Seminar"),
            "participant_categories": [ParticipantCategory.objects.get(name="MSMEs")],
            "end_date": date(2026, 11, 18),
            "evaluation_deadline": at(date(2026, 11, 25), 9),
            "budget_amount": Decimal("45000.50"),
            "max_slots": 40,
            "title": activity.title,  # unchanged: no row
        },
        now=TEST_NOW,
    )
    assert sorted(audit_rows(activity)[1:]) == sorted(
        [
            ("Venue", "CSU Hall", "Room 101"),
            ("Activity Type", "Workshop", "Seminar"),  # names, never IDs
            ("Participant Categories", "Incubatees", "MSMEs"),
            ("End Date", "Nov 16, 2026", "Nov 18, 2026"),
            ("Evaluation Deadline", "Nov 23, 2026 5:00 PM", "Nov 25, 2026 9:00 AM"),
            ("Budget Amount", "₱30,000.00", "₱45,000.50"),
            ("Max Slots", "30", "40"),
        ]
    )
    assert all(row.changed_by == director for row in activity.audit_logs.all())


@pytest.mark.django_db
def test_update_with_no_real_change_writes_nothing(director, new_activity) -> None:
    services.update_activity(director, new_activity, {"venue": "CSU Hall", "budget_amount": Decimal("30000")})
    assert len(audit_rows(new_activity)) == 1


@pytest.mark.django_db
def test_update_saves_only_changed_fields(director, new_activity) -> None:
    Activity.objects.filter(pk=new_activity.pk).update(title="Changed elsewhere")  # simulates another edit
    services.update_activity(director, new_activity, {"venue": "Room 101"})
    new_activity.refresh_from_db()
    assert (new_activity.title, new_activity.venue) == ("Changed elsewhere", "Room 101")


@pytest.mark.django_db
def test_edit_and_audit_roll_back_together(director, new_activity, monkeypatch) -> None:
    """If writing any audit row fails, the edit is not saved either."""
    real_create = ActivityAuditLog.objects.create
    calls = itertools.count()

    def fail_on_second_row(**kwargs):
        if next(calls) == 1:
            raise RuntimeError("database error")
        return real_create(**kwargs)

    monkeypatch.setattr(ActivityAuditLog.objects, "create", fail_on_second_row)
    with pytest.raises(RuntimeError):
        services.update_activity(director, new_activity, {"venue": "Room 101", "max_slots": 40})

    new_activity.refresh_from_db()
    assert (new_activity.venue, new_activity.max_slots) == ("CSU Hall", 30)
    assert len(audit_rows(new_activity)) == 1  # only the creation row


@pytest.mark.django_db
def test_conducted_activity_is_read_only(director, new_activity, move_to) -> None:
    move_to(new_activity, "Conducted")
    with pytest.raises(services.ActivityError, match="Conducted activity is read-only"):
        services.update_activity(director, new_activity, {"venue": "Room 101"})


@pytest.mark.django_db
def test_ongoing_activity_can_be_edited(director, new_activity, move_to) -> None:
    move_to(new_activity, "Ongoing")
    services.update_activity(director, new_activity, {"venue": "Room 101"})
    assert new_activity.venue == "Room 101"


# --- Permissions -------------------------------------------------------------------------


@pytest.mark.django_db
def test_view_only_members_cannot_create_or_change_activities(view_only, new_activity, valid_data) -> None:
    person = ResourcePerson.objects.create(name="Dr. Reyes")
    attempts = [
        lambda: services.create_activity(view_only, valid_data()),
        lambda: services.update_activity(view_only, new_activity, {"venue": "Room 101"}),
        lambda: services.change_status(view_only, new_activity, "Scheduled"),
        lambda: services.add_resource_person(view_only, new_activity, person),
        lambda: services.remove_resource_person(view_only, new_activity, person),
        lambda: services.create_resource_person(view_only, "New Person"),
    ]
    for attempt in attempts:
        with pytest.raises(PermissionDenied, match="create or edit activities"):
            attempt()
    assert len(audit_rows(new_activity)) == 1


# --- Status moves ------------------------------------------------------------------------

ALLOWED = [("Draft", "Scheduled"), ("Scheduled", "Ongoing"), ("Ongoing", "Conducted"), ("Scheduled", "Draft")]
ALL = ["Draft", "Scheduled", "Ongoing", "Conducted"]
BLOCKED = [(a, b) for a in ALL for b in ALL if (a, b) not in ALLOWED]


def put_in_status(activity, status_name, move_to) -> None:
    if status_name != "Draft":
        move_to(activity, status_name)


@pytest.mark.django_db
@pytest.mark.parametrize("start, target", ALLOWED)
def test_allowed_status_moves(director, new_activity, move_to, start, target) -> None:
    put_in_status(new_activity, start, move_to)
    services.change_status(director, new_activity, target)
    assert new_activity.status.name == target
    assert audit_rows(new_activity)[-1] == ("Status", start, target)


@pytest.mark.django_db
@pytest.mark.parametrize("start, target", BLOCKED)
def test_blocked_status_moves(director, new_activity, move_to, start, target) -> None:
    put_in_status(new_activity, start, move_to)
    rows_before = len(audit_rows(new_activity))
    with pytest.raises(services.ActivityError):
        services.change_status(director, new_activity, target)
    new_activity.refresh_from_db()
    assert new_activity.status.name == start
    assert len(audit_rows(new_activity)) == rows_before


@pytest.mark.django_db
@pytest.mark.parametrize("target", ALL)
def test_conducted_is_final(director, new_activity, move_to, target) -> None:
    move_to(new_activity, "Conducted")
    with pytest.raises(services.ActivityError, match="Conducted activity is final"):
        services.change_status(director, new_activity, target)


@pytest.mark.django_db
def test_scheduled_back_to_draft_blocked_with_registrations(director, new_activity, move_to, register) -> None:
    move_to(new_activity, "Scheduled")
    register(new_activity, 2)
    with pytest.raises(services.ActivityError, match="2 people have already registered"):
        services.change_status(director, new_activity, "Draft")


@pytest.mark.django_db
def test_unknown_status(director, new_activity) -> None:
    with pytest.raises(services.ActivityError, match="Unknown status"):
        services.change_status(director, new_activity, "Cancelled")


# --- Resource persons --------------------------------------------------------------------


@pytest.mark.django_db
def test_add_and_remove_resource_person_are_audited(director, new_activity) -> None:
    person = services.create_resource_person(director, "Dr. Liza Marquez", "Liza@Example.com", "0917")
    assert person.email == "liza@example.com"

    services.add_resource_person(director, new_activity, person, "Pitch deck essentials")
    assert new_activity.resource_person_links.get().topic == "Pitch deck essentials"
    services.remove_resource_person(director, new_activity, person)
    assert not new_activity.resource_person_links.exists()

    assert audit_rows(new_activity)[1:] == [
        ("Resource Person Added", "", "Dr. Liza Marquez"),
        ("Resource Person Removed", "Dr. Liza Marquez", ""),
    ]


@pytest.mark.django_db
def test_resource_person_linked_only_once(director, new_activity) -> None:
    person = ResourcePerson.objects.create(name="Dr. Reyes")
    services.add_resource_person(director, new_activity, person)
    with pytest.raises(services.ActivityError, match="already a resource person"):
        services.add_resource_person(director, new_activity, person, "Another topic")


@pytest.mark.django_db
def test_remove_resource_person_not_linked(director, new_activity) -> None:
    with pytest.raises(services.ActivityError, match="is not a resource person"):
        services.remove_resource_person(director, new_activity, ResourcePerson.objects.create(name="Dr. Reyes"))


@pytest.mark.django_db
def test_resource_persons_locked_when_conducted(director, new_activity, move_to) -> None:
    person = ResourcePerson.objects.create(name="Dr. Reyes")
    services.add_resource_person(director, new_activity, person)
    move_to(new_activity, "Conducted")
    with pytest.raises(services.ActivityError, match="read-only"):
        services.add_resource_person(director, new_activity, ResourcePerson.objects.create(name="Other"))
    with pytest.raises(services.ActivityError, match="read-only"):
        services.remove_resource_person(director, new_activity, person)


@pytest.mark.django_db
def test_create_resource_person_rules(director) -> None:
    services.create_resource_person(director, "Dr. Reyes", "reyes@example.com")
    with pytest.raises(services.ActivityError, match="already exists"):
        services.create_resource_person(director, "Someone", "REYES@example.com")
    with pytest.raises(ValidationError):
        services.create_resource_person(director, "   ")
