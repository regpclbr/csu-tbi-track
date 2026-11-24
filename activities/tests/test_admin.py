"""The Django admin edits activities through the same services, so the audit log is never skipped."""

import pytest
from django.contrib.admin.sites import site
from django.test import RequestFactory

from accounts.models import TBIMember
from activities import services
from activities.models import Activity, ActivityResourcePerson, ResourcePerson

URL = "/admin/activities/activity/"


@pytest.mark.django_db
def test_activity_admin_has_no_delete(admin_role, activity) -> None:
    admin_user = TBIMember.objects.create_user("admin@example.com", "Admin", admin_role, "pass-12345")
    request = RequestFactory().get(URL)
    request.user = admin_user
    model_admin = site._registry[Activity]

    assert model_admin.has_delete_permission(request, activity) is False
    assert "delete_selected" not in model_admin.get_actions(request)


def form_data(activity: Activity | None = None, values: dict | None = None, links: list | None = None) -> dict:
    """POST data for the admin add/change form (date-times are split into date and time)."""
    values = values or services.activity_values(activity)
    data = {
        "title": values["title"],
        "description": values["description"] or "",
        "activity_type": values["activity_type"].pk,
        "participant_categories": sorted(category.pk for category in values["participant_categories"]),
        "start_date": values["start_date"].isoformat(),
        "end_date": values["end_date"].isoformat(),
        "start_time": values["start_time"].strftime("%H:%M"), "end_time": values["end_time"].strftime("%H:%M"),
        "venue": values["venue"],
        "max_slots": values["max_slots"],
        "registration_deadline_0": values["registration_deadline"].strftime("%Y-%m-%d"),
        "registration_deadline_1": "17:00:00",
        "evaluation_deadline_0": values["evaluation_deadline"].strftime("%Y-%m-%d"),
        "evaluation_deadline_1": "17:00:00",
        "fund_source": values["fund_source"].pk,
    }
    if activity is not None:  # the change page has the resource-person inline
        links = links or []
        data.update(
            {
                "resource_person_links-TOTAL_FORMS": len(links),
                "resource_person_links-INITIAL_FORMS": sum(1 for link in links if link.get("pk")),
                "resource_person_links-MIN_NUM_FORMS": 0,
                "resource_person_links-MAX_NUM_FORMS": 1000,
                # the view-only budget lines inline (ASSUMPTIONS 433): its management form only
                "budget_lines-TOTAL_FORMS": 0, "budget_lines-INITIAL_FORMS": 0,
                "budget_lines-MIN_NUM_FORMS": 0, "budget_lines-MAX_NUM_FORMS": 1000,
            }
        )
        for number, link in enumerate(links):
            prefix = f"resource_person_links-{number}-"
            data[prefix + "activity"] = activity.pk
            data[prefix + "activity_resource_person_id"] = link.get("pk", "")
            data[prefix + "resource_person"] = link.get("resource_person", "")
            data[prefix + "topic"] = link.get("topic", "")
            if link.get("delete"):
                data[prefix + "DELETE"] = "on"
    return data


@pytest.mark.django_db
def test_admin_add_goes_through_service(client, both_flags_member, valid_data) -> None:
    client.force_login(both_flags_member)
    response = client.post(f"{URL}add/", form_data(values=valid_data()))
    assert response.status_code == 302
    activity = Activity.objects.get()
    assert activity.status.name == "Draft" and activity.created_by == both_flags_member
    assert list(activity.audit_logs.values_list("field_changed", "new_value")) == [("Status", "Draft")]


@pytest.mark.django_db
def test_admin_edit_writes_audit(client, both_flags_member, new_activity) -> None:
    client.force_login(both_flags_member)
    data = form_data(new_activity) | {"venue": "Room 101"}
    response = client.post(f"{URL}{new_activity.pk}/change/", data)
    assert response.status_code == 302
    row = new_activity.audit_logs.get(field_changed="Venue")
    assert (row.old_value, row.new_value, row.changed_by) == ("CSU Hall", "Room 101", both_flags_member)


@pytest.mark.django_db
def test_admin_shows_validation_errors(client, both_flags_member, new_activity, register) -> None:
    register(new_activity, 3)
    client.force_login(both_flags_member)
    response = client.post(f"{URL}{new_activity.pk}/change/", form_data(new_activity) | {"max_slots": 2})
    assert response.status_code == 200
    assert "Max slots cannot be lower than the 3 people already registered." in response.content.decode()
    new_activity.refresh_from_db()
    assert new_activity.max_slots == 30


@pytest.mark.django_db
def test_admin_conducted_activity_is_read_only(admin_member, new_activity, move_to, rf) -> None:
    move_to(new_activity, "Conducted")
    request = rf.get(URL)
    request.user = admin_member
    readonly = site._registry[Activity].get_readonly_fields(request, new_activity)
    assert set(services.EDITABLE_FIELDS) <= set(readonly)


@pytest.mark.django_db
def test_admin_status_actions_use_the_rules(client, both_flags_member, new_activity) -> None:
    client.force_login(both_flags_member)
    client.post(URL, {"action": "move_to_conducted", "_selected_action": [new_activity.pk]})  # Draft -> Conducted: refused
    new_activity.refresh_from_db()
    assert new_activity.status.name == "Draft"

    client.post(URL, {"action": "move_to_scheduled", "_selected_action": [new_activity.pk]})
    new_activity.refresh_from_db()
    assert new_activity.status.name == "Scheduled"
    assert new_activity.audit_logs.filter(field_changed="Status", new_value="Scheduled").exists()


@pytest.mark.django_db
def test_admin_resource_person_inline_is_audited(client, both_flags_member, new_activity) -> None:
    person = ResourcePerson.objects.create(name="Dr. Reyes")
    client.force_login(both_flags_member)

    response = client.post(
        f"{URL}{new_activity.pk}/change/",
        form_data(new_activity, links=[{"resource_person": person.pk, "topic": "IP"}]),
    )
    assert response.status_code == 302
    link = ActivityResourcePerson.objects.get(activity=new_activity)
    assert new_activity.audit_logs.filter(field_changed="Resource Person Added", new_value="Dr. Reyes").exists()

    response = client.post(
        f"{URL}{new_activity.pk}/change/",
        form_data(new_activity, links=[{"pk": link.pk, "resource_person": person.pk, "topic": "IP", "delete": True}]),
    )
    assert response.status_code == 302
    assert not ActivityResourcePerson.objects.filter(activity=new_activity).exists()
    assert new_activity.audit_logs.filter(field_changed="Resource Person Removed", old_value="Dr. Reyes").exists()


@pytest.mark.django_db
def test_admin_resource_person_links_are_view_only(admin_member, rf) -> None:
    request = rf.get("/admin/activities/activityresourceperson/")
    request.user = admin_member
    model_admin = site._registry[ActivityResourcePerson]
    assert not model_admin.has_add_permission(request)
    assert not model_admin.has_change_permission(request)
    assert not model_admin.has_delete_permission(request)
