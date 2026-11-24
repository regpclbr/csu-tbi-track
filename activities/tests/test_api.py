"""JSON endpoints under /activities/api/: permissions, input errors and responses."""

import json

import pytest

from activities import services
from activities.models import Activity, ResourcePerson

API = "/activities/api/"
FORBIDDEN = {"error": "You do not have permission to create or edit activities."}


def post_json(client, url: str, data: dict | None = None):
    return client.post(url, json.dumps(data or {}), content_type="application/json")


def body(response) -> dict:
    return json.loads(response.content)


@pytest.fixture
def create_payload(valid_data) -> dict:
    """valid_data() as JSON: IDs and ISO strings."""
    data = valid_data()
    return {
        **{key: value for key, value in data.items() if key not in ("activity_type", "participant_categories")},
        "activity_type": data["activity_type"].pk,
        "participant_categories": [category.pk for category in data["participant_categories"]],
        "start_date": data["start_date"].isoformat(),
        "end_date": data["end_date"].isoformat(),
        "registration_deadline": data["registration_deadline"].isoformat(),
        "evaluation_deadline": data["evaluation_deadline"].isoformat(),
        "start_time": data["start_time"].strftime("%H:%M"),
        "end_time": data["end_time"].strftime("%H:%M"),
        "budget_amount": str(data["budget_amount"]),
    }


# --- Create and update -------------------------------------------------------------------


@pytest.mark.django_db
def test_create_via_api(client, director, create_payload) -> None:
    client.force_login(director)
    response = post_json(client, API, create_payload)
    assert response.status_code == 201
    activity = body(response)["activity"]
    assert activity["status"] == "Draft" and activity["created_by"] == director.name
    assert activity["budget_amount"] == "30000.00"


@pytest.mark.django_db
def test_validation_errors_are_400_with_field_messages(client, director, create_payload) -> None:
    client.force_login(director)
    response = post_json(client, API, {**create_payload, "max_slots": 0, "budget_amount": "abc"})
    assert response.status_code == 400
    errors = body(response)["errors"]
    assert "budget_amount" in errors  # cannot be converted (form)
    response = post_json(client, API, {**create_payload, "max_slots": 0})
    assert body(response)["errors"] == {"max_slots": ["Max slots must be at least 1."]}


@pytest.mark.django_db
def test_update_via_api_only_given_fields(client, director, new_activity) -> None:
    client.force_login(director)
    response = post_json(client, f"{API}{new_activity.pk}/update/", {"venue": "Room 101"})
    assert response.status_code == 200
    assert body(response)["activity"]["venue"] == "Room 101"
    assert new_activity.audit_logs.filter(field_changed="Venue").count() == 1


@pytest.mark.django_db
def test_conducted_update_is_400(client, director, new_activity, move_to) -> None:
    move_to(new_activity, "Conducted")
    client.force_login(director)
    response = post_json(client, f"{API}{new_activity.pk}/update/", {"venue": "Room 101"})
    assert response.status_code == 400
    assert "read-only" in body(response)["error"]


# --- Status ------------------------------------------------------------------------------


@pytest.mark.django_db
def test_status_change_via_api(client, director, new_activity) -> None:
    client.force_login(director)
    response = post_json(client, f"{API}{new_activity.pk}/status/", {"status": "Scheduled"})
    assert response.status_code == 200 and body(response)["activity"]["status"] == "Scheduled"

    response = post_json(client, f"{API}{new_activity.pk}/status/", {"status": "Conducted"})
    assert response.status_code == 400
    assert body(response)["error"] == (
        "Cannot change status from Scheduled to Conducted. From Scheduled you can go to Draft or Ongoing."
    )


# --- Resource persons --------------------------------------------------------------------


@pytest.mark.django_db
def test_resource_persons_via_api(client, director, new_activity) -> None:
    client.force_login(director)
    response = post_json(client, f"{API}resource-persons/", {"name": "Dr. Reyes", "email": "reyes@example.com"})
    assert response.status_code == 201
    person_id = body(response)["resource_person"]["resource_person_id"]

    url = f"{API}{new_activity.pk}/resource-persons/"
    assert post_json(client, url, {"resource_person_id": person_id, "topic": "IP"}).status_code == 201
    duplicate = post_json(client, url, {"resource_person_id": person_id})
    assert duplicate.status_code == 400 and "already a resource person" in body(duplicate)["error"]

    assert post_json(client, f"{url}{person_id}/remove/").status_code == 200
    assert not new_activity.resource_person_links.exists()


# --- Permissions: Admin Staff can read but not change -------------------------------------


@pytest.mark.django_db
def test_view_only_members_gets_403_on_every_change(client, view_only, new_activity, create_payload) -> None:
    person = ResourcePerson.objects.create(name="Dr. Reyes")
    client.force_login(view_only)
    attempts = [
        (API, create_payload),
        (f"{API}{new_activity.pk}/update/", {"venue": "Room 101"}),
        (f"{API}{new_activity.pk}/status/", {"status": "Scheduled"}),
        (f"{API}resource-persons/", {"name": "New Person"}),
        (f"{API}{new_activity.pk}/resource-persons/", {"resource_person_id": person.pk}),
        (f"{API}{new_activity.pk}/resource-persons/{person.pk}/remove/", {}),
    ]
    for url, data in attempts:
        response = post_json(client, url, data)
        assert response.status_code == 403, url
        assert body(response) == FORBIDDEN
    assert Activity.objects.count() == 1 and new_activity.audit_logs.count() == 1


@pytest.mark.django_db
def test_any_staff_member_can_read(client, view_only, new_activity) -> None:
    client.force_login(view_only)
    for url in [
        API,
        f"{API}stats/",
        f"{API}calendar/?start=2026-01-01&end=2027-12-31",
        f"{API}{new_activity.pk}/",
        f"{API}{new_activity.pk}/audit-log/",
        f"{API}resource-persons/",
    ]:
        assert client.get(url).status_code == 200, url


@pytest.mark.django_db
def test_not_logged_in_is_sent_to_login(client, new_activity) -> None:
    for url in [API, f"{API}{new_activity.pk}/", f"{API}{new_activity.pk}/update/"]:
        response = client.get(url) if not url.endswith("update/") else post_json(client, url)
        assert response.status_code == 401 and response.json() == {"error": "Login required."}


# --- Read responses -----------------------------------------------------------------------


@pytest.mark.django_db
def test_list_rows_show_slots(client, director, new_activity, register) -> None:
    register(new_activity, 18)
    client.force_login(director)
    response = client.get(API, {"search": "pitch", "sort": "title"})
    row = body(response)["activities"][0]
    assert (row["registered_count"], row["max_slots"], row["slots"]) == (18, 30, "18/30")
    assert body(response)["total"] == 1


@pytest.mark.django_db
def test_bad_query_parameters_are_400(client, director) -> None:
    client.force_login(director)
    assert client.get(API, {"date_from": "15/10/2026"}).status_code == 400
    assert client.get(API, {"status": "Draft"}).status_code == 400  # IDs only
    assert client.get(API, {"sort": "nope"}).status_code == 400
    assert client.get(f"{API}calendar/").status_code == 400


@pytest.mark.django_db
def test_audit_log_endpoint(client, director, new_activity) -> None:
    services.update_activity(director, new_activity, {"venue": "Room 101"})
    client.force_login(director)
    rows = body(client.get(f"{API}{new_activity.pk}/audit-log/", {"field": "Venue"}))
    assert rows["total"] == 1
    assert rows["audit_log"][0] | {"changed_at": None} == {
        "field_changed": "Venue",
        "old_value": "CSU Hall",
        "new_value": "Room 101",
        "changed_by": director.name,
        "changed_at": None,
    }


# --- Creation documents (ASSUMPTIONS 405) --------------------------------------------------------------------------


@pytest.mark.django_db
def test_an_api_draft_needs_both_creation_documents_before_scheduled(client, director, create_payload) -> None:
    """The JSON API creates a Draft without files but under the new rule: it cannot be Scheduled until the Activity
    Design and the Request Letter are uploaded (Documents tab or API)."""
    from django.core.files.uploadedfile import SimpleUploadedFile

    from conftest import CREATION_PDF
    from documents.services import creation_required_types, upload_document

    client.force_login(director)
    activity = Activity.objects.get(pk=body(post_json(client, API, create_payload))["activity"]["activity_id"])
    assert activity.creation_documents_required is True
    response = post_json(client, f"{API}{activity.pk}/status/", {"status": "Scheduled"})
    assert response.status_code == 400
    assert body(response)["error"] == ("Upload the Activity Design and the Request Letter before scheduling this activity "
                                       "(Documents tab).")
    for document_type in creation_required_types():
        upload_document(director, activity, document_type, SimpleUploadedFile("a.pdf", CREATION_PDF))
    activity.refresh_from_db()  # ASSUMPTIONS 431: the second upload scheduled it by itself
    assert activity.status.name == "Scheduled"


@pytest.mark.django_db
def test_the_flag_cannot_be_set_or_changed_through_the_api(client, director, create_payload, new_activity) -> None:
    client.force_login(director)
    response = post_json(client, f"{API}{new_activity.pk}/update/", {"creation_documents_required": True})
    assert response.status_code == 400
    new_activity.refresh_from_db()
    assert new_activity.creation_documents_required is False
