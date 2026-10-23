"""The list of activities open for registration: public (/api/activities/open/) and in the startup
portal (/startups/portal/api/activities/, with already_registered). Same rule as the registration page."""

from datetime import timedelta

import pytest
from django.test import Client

from activities import services as activity_services
from activities.models import Activity
from registrations import services
from registrations.models import FormType
from startups.tests.conftest import STARTUP_PASSWORD, make_startup  # noqa: F401

pytestmark = pytest.mark.django_db

PUBLIC_FIELDS = {
    "activity_id", "title", "description", "activity_type", "start_date", "end_date", "time_schedule", "venue",
    "registration_deadline", "slots_remaining", "participant_categories",
}


@pytest.fixture
def make_scheduled(director, valid_data, with_forms, move_to):
    """make_scheduled(title, days=30, **values): a Scheduled activity with both forms."""

    def make(title: str, days: int = 30, **values) -> Activity:
        data = valid_data(title=title, **values)
        start = data["start_date"] + timedelta(days=days - 30)
        data.update(start_date=start, end_date=start, registration_deadline=data["registration_deadline"] + timedelta(days=days - 30),
                    evaluation_deadline=data["evaluation_deadline"] + timedelta(days=days - 30))
        activity = activity_services.create_activity(director, data)
        with_forms(activity)
        return move_to(activity, "Scheduled")

    return make


def titles(response) -> list[str]:
    return [row["title"] for row in response.json()["activities"]]


def test_lists_only_open_activities_soonest_first(client, director, make_scheduled, new_activity, signup, monkeypatch) -> None:
    later, sooner = make_scheduled("Later", days=40), make_scheduled("Sooner", days=20)
    full = make_scheduled("Full", max_slots=1)
    signup(full, "ana@example.com")
    no_form = make_scheduled("No form")
    no_form.form_templates.filter(form_type=FormType.REGISTRATION).delete()
    past_deadline = make_scheduled("Past deadline", days=10)
    monkeypatch.setattr(services.timezone, "now", lambda: past_deadline.registration_deadline + timedelta(minutes=1))
    # new_activity is a Draft: never listed.

    response = client.get("/api/activities/open/")  # public: no login
    assert response.status_code == 200
    assert titles(response) == ["Sooner", "Later"]


def test_only_the_public_fields(client, make_scheduled, signup) -> None:
    activity = make_scheduled("Pitching")
    signup(activity, "ana@example.com")
    [row] = client.get("/api/activities/open/").json()["activities"]
    assert set(row) == PUBLIC_FIELDS
    assert row["slots_remaining"] == activity.max_slots - 1
    assert row["participant_categories"] == [{"participant_category_id": c.pk, "name": c.name} for c in activity.participant_categories.all()]
    text = client.get("/api/activities/open/").content.decode()
    assert "ana@example.com" not in text and "Ana Cruz" not in text  # never who registered
    detail = client.get(f"/api/register/{activity.pk}/").json()["activity"]
    assert {key: detail[key] for key in PUBLIC_FIELDS} == row  # exactly what the registration page shows


def test_the_list_needs_get(client) -> None:
    assert client.post("/api/activities/open/").status_code == 405


def test_startup_portal_list_marks_my_registrations(make_scheduled, make_startup, signup) -> None:
    mine, other = make_scheduled("Mine"), make_scheduled("Other")
    startup = make_startup(email="acme@example.com")
    signup(mine, "ACME@example.com")  # an older public registration with the startup's email counts as mine
    portal = Client()
    assert portal.get("/startups/portal/api/activities/").status_code == 401  # startup login required
    portal.post("/startups/portal/api/login/", {"email": startup.email, "password": STARTUP_PASSWORD}, content_type="application/json")
    rows = {row["title"]: row for row in portal.get("/startups/portal/api/activities/").json()["activities"]}
    assert rows["Mine"]["already_registered"] is True and rows["Other"]["already_registered"] is False
    assert set(rows["Mine"]) == PUBLIC_FIELDS | {"already_registered"}
