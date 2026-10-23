"""Fields the frontend needs: activity_type_id in the list and detail, the form IDs in the detail,
and the staff lookups endpoint (/api/lookups/)."""

import pytest

from accounts.seed import load_default_form_fields
from activities.models import ActivityType
from documents.models import DocumentType
from registrations import services as registration_services
from registrations.models import FormType

pytestmark = pytest.mark.django_db


def test_list_and_detail_have_the_activity_type_id(client, director, new_activity) -> None:
    client.force_login(director)
    [row] = client.get("/activities/api/").json()["activities"]
    assert row["activity_type_id"] == new_activity.activity_type_id and row["activity_type"] == "Workshop"
    detail = client.get(f"/activities/api/{new_activity.pk}/").json()["activity"]
    assert detail["activity_type_id"] == new_activity.activity_type_id


def test_detail_has_the_form_ids_or_null(client, director, new_activity) -> None:
    client.force_login(director)
    url = f"/activities/api/{new_activity.pk}/"
    detail = client.get(url).json()["activity"]
    assert detail["registration_form_id"] is None and detail["evaluation_form_id"] is None

    load_default_form_fields()
    registration = registration_services.create_form(director, new_activity, FormType.REGISTRATION)
    detail = client.get(url).json()["activity"]
    assert (detail["registration_form_id"], detail["evaluation_form_id"]) == (registration.pk, None)
    evaluation = registration_services.create_form(director, new_activity, FormType.EVALUATION)
    assert client.get(url).json()["activity"]["evaluation_form_id"] == evaluation.pk
    assert client.get(f"/registrations/api/forms/{evaluation.pk}/").status_code == 200  # the ID opens the form


def test_lookups_list_id_and_name(client, director) -> None:
    ActivityType.objects.filter(name="Workshop").update(is_active=False)
    DocumentType.objects.filter(name="Request Letter").update(is_active=False)
    client.force_login(director)
    data = client.get("/api/lookups/").json()
    assert set(data) == {"activity_types", "participant_categories", "document_types", "activity_statuses", "document_statuses", "roles"}
    for rows in data.values():
        assert rows and all(set(row) == {"id", "name"} for row in rows)
    names = {key: [row["name"] for row in rows] for key, rows in data.items()}
    assert "Workshop" not in names["activity_types"] and "Training" in names["activity_types"]  # active only
    assert "Request Letter" not in names["document_types"] and "Invitation Letter" in names["document_types"]
    assert names["participant_categories"] == ["Faculty", "Incubatees", "MSMEs"]
    assert names["activity_statuses"] == ["Draft", "Scheduled", "Ongoing", "Conducted"]
    assert names["document_statuses"] == ["For Review", "Approved"]
    assert "Admin" in names["roles"] and len(names["roles"]) == 5


def test_lookups_need_a_staff_login(client) -> None:
    assert client.get("/api/lookups/").status_code == 302
    assert client.post("/api/lookups/").status_code == 302
