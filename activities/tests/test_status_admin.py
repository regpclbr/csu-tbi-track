"""Changing an activity's status in the Django admin: plain-text status, the "Change status"
page with only the allowed next statuses, the list actions, and clear messages."""

import pytest
from django.urls import reverse

from accounts.models import Role
from activities import services

LIST_URL = "/admin/activities/activity/"


def change_url(activity) -> str:
    return reverse("admin:activities_activity_change", args=[activity.pk])


def status_url(activity) -> str:
    return reverse("admin:activities_activity_change_status", args=[activity.pk])


def messages_of(response) -> list[str]:
    return [str(message) for message in response.context["messages"]]


# --- Edit page ------------------------------------------------------------------------


@pytest.mark.django_db
def test_status_is_plain_text_with_a_change_status_button(client, admin_member, new_activity) -> None:
    client.force_login(admin_member)
    html = client.get(change_url(new_activity)).content.decode()
    # No link to the status lookup row (the side menu still links to the statuses list).
    assert f"/admin/activities/activitystatus/{new_activity.status_id}/" not in html
    assert '<div class="readonly">Draft</div>' in html
    assert f'href="{status_url(new_activity)}"' in html
    assert "Change status (now: Draft)" in html


# --- "Change status" page ------------------------------------------------------------


def offered(client, activity) -> list[str]:
    return client.get(status_url(activity)).context["next_statuses"]


@pytest.mark.django_db
def test_page_offers_only_allowed_next_statuses(client, admin_member, new_activity, move_to, register) -> None:
    client.force_login(admin_member)
    assert offered(client, new_activity) == ["Scheduled"]
    move_to(new_activity, "Scheduled")
    assert offered(client, new_activity) == ["Draft", "Ongoing"]
    register(new_activity, 1)
    assert offered(client, new_activity) == ["Ongoing"]  # Draft blocked once someone registered
    move_to_ongoing_and_conducted(admin_member, new_activity)
    response = client.get(status_url(new_activity))
    assert response.context["next_statuses"] == []
    assert "Conducted is final" in response.content.decode()


def move_to_ongoing_and_conducted(member, activity) -> None:
    services.change_status(member, activity, "Ongoing")
    services.change_status(member, activity, "Conducted")


@pytest.mark.django_db
def test_change_status_success_message(client, admin_member, new_activity) -> None:
    client.force_login(admin_member)
    response = client.post(status_url(new_activity), {"status": "Scheduled"}, follow=True)
    assert response.redirect_chain[-1][0] == change_url(new_activity)
    assert messages_of(response) == ['Status of "Pitching Workshop" changed from Draft to Scheduled.']
    new_activity.refresh_from_db()
    assert new_activity.status.name == "Scheduled"
    assert new_activity.audit_logs.filter(field_changed="Status", old_value="Draft", new_value="Scheduled").exists()


@pytest.mark.django_db
def test_change_status_error_message(client, admin_member, new_activity) -> None:
    client.force_login(admin_member)
    response = client.post(status_url(new_activity), {"status": "Conducted"}, follow=True)
    assert messages_of(response) == [
        "Status not changed: Cannot change status from Draft to Conducted. From Draft you can go to Scheduled."
    ]
    new_activity.refresh_from_db()
    assert new_activity.status.name == "Draft"


@pytest.mark.django_db
def test_change_status_page_needs_can_create_activity(client, admin_member, new_activity) -> None:
    Role.objects.filter(role_name="Admin").update(can_create_activity=False)
    client.force_login(admin_member)
    response = client.post(status_url(new_activity), {"status": "Scheduled"})
    assert response.status_code == 403
    new_activity.refresh_from_db()
    assert new_activity.status.name == "Draft"


@pytest.mark.django_db
def test_non_admins_cannot_open_the_page(client, director, new_activity) -> None:
    client.force_login(director)
    response = client.get(status_url(new_activity))
    assert response.status_code == 302  # the admin site sends non-Admins to login


# --- List actions ---------------------------------------------------------------------


@pytest.mark.django_db
def test_action_dropdown_is_on_the_list_page(client, admin_member, new_activity) -> None:
    client.force_login(admin_member)
    html = client.get(LIST_URL).content.decode()
    assert 'name="action"' in html
    for label in ["Change status to Scheduled", "Change status to Ongoing", "Change status to Conducted", "Change status back to Draft"]:
        assert label in html


@pytest.mark.django_db
def test_list_actions_report_success_and_errors(client, admin_member, new_activity, director, valid_data) -> None:
    other = services.create_activity(director, valid_data(title="Second Activity"))
    services.change_status(director, other, "Scheduled")
    client.force_login(admin_member)

    response = client.post(
        LIST_URL, {"action": "move_to_scheduled", "_selected_action": [new_activity.pk, other.pk]}, follow=True
    )
    found = messages_of(response)
    assert 'Status changed to Scheduled for "Pitching Workshop".' in found
    assert any(m.startswith('"Second Activity": Cannot change status from Scheduled to Scheduled') for m in found)
