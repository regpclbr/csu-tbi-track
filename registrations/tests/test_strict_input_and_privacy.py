"""Strict true/false values in the registration APIs, the evaluation link's privacy once it is of no
more use, and the placeholder pages at the emailed (page) URLs."""

import json
from datetime import timedelta

import pytest

from registrations import services
from registrations.models import Registration

from .conftest import CERT, evaluation_answers, form_of, opens_at

pytestmark = pytest.mark.django_db


def post_json(client, url: str, data: dict):
    return client.post(url, json.dumps(data), content_type="application/json")


# --- Strict booleans -----------------------------------------------------------------------------------


@pytest.mark.parametrize("value, expected", [(True, True), ("true", True), ("FALSE", False), (False, False)])
def test_attendance_takes_true_or_false(client, director, attended, value, expected) -> None:
    person = attended(1)[0]  # marked attended already, so false unmarks
    client.force_login(director)
    url = f"/registrations/api/activities/{person.activity_id}/attendance/"
    response = post_json(client, url, {"registration_ids": [person.pk], "attended": value, "method": "wet_signature"})
    assert response.status_code == 200
    person.refresh_from_db()
    assert person.attendance_confirmed is expected


@pytest.mark.parametrize("value", [1, 0, "yes", "1", "maybe", [], None])
def test_attendance_refuses_anything_else(client, director, attended, value) -> None:
    person = attended(1)[0]
    client.force_login(director)
    url = f"/registrations/api/activities/{person.activity_id}/attendance/"
    body = {"registration_ids": [person.pk], "method": "email"}
    if value is not None:
        body["attended"] = value
    response = post_json(client, url, body)
    assert response.status_code == 400
    person.refresh_from_db()
    assert person.attendance_confirmed is True  # unchanged ("false" as text used to count as true)


def test_registrations_filter_and_field_flags_are_strict(client, director, scheduled) -> None:
    client.force_login(director)
    base = f"/registrations/api/activities/{scheduled.pk}/registrations/"
    assert client.get(base, {"attended": "false"}).status_code == 200
    assert client.get(base, {"attended": "1"}).json() == {"errors": {"attended": ["attended must be true or false."]}}

    form = form_of(scheduled)
    added = post_json(client, f"/registrations/api/forms/{form.pk}/fields/", {"field_label": "Shirt size", "field_type": "short_text", "is_required": "false"})
    assert added.status_code == 201
    field = form.fields.get(field_label="Shirt size")
    assert field.is_required is False
    assert post_json(client, f"/registrations/api/forms/{form.pk}/fields/", {"field_label": "Age", "field_type": "short_text", "is_required": "no"}).status_code == 400
    assert post_json(client, f"/registrations/api/fields/{field.pk}/", {"is_required": 1}).status_code == 400
    assert post_json(client, f"/registrations/api/fields/{field.pk}/", {"is_required": True}).status_code == 200


# --- Evaluation link privacy ---------------------------------------------------------------------------


def test_open_link_shows_the_suggested_name_and_email(client, attended) -> None:
    person = attended(1)[0]
    page = services.evaluation_page(services.evaluation_token(person), now=opens_at(person.activity))
    assert page["is_open"] is True and page["suggested_certificate_email"] == person.email


def test_before_the_window_the_page_still_shows_the_form(attended) -> None:
    person = attended(1)[0]
    page = services.evaluation_page(services.evaluation_token(person), now=opens_at(person.activity) - timedelta(minutes=1))
    assert page["is_open"] is False and page["name"] == person.name and page["fields"]


def test_submitted_link_shows_only_the_closed_reason(client, attended, monkeypatch) -> None:
    person = attended(1)[0]
    activity = person.activity
    monkeypatch.setattr(services.timezone, "now", lambda: opens_at(activity) + timedelta(hours=1))
    token = services.evaluation_token(person)
    services.submit_evaluation(token, **CERT, answers=evaluation_answers(activity))
    response = client.get(f"/api/evaluate/{token}/")
    assert response.json() == {"evaluation": {"is_open": False, "closed_reason": "You have already submitted your evaluation. Thank you!"}}
    assert person.email not in response.content.decode() and person.name not in response.content.decode()


def test_closed_window_shows_only_the_closed_reason(client, attended, monkeypatch) -> None:
    person = attended(1)[0]
    activity = person.activity
    monkeypatch.setattr(services.timezone, "now", lambda: activity.evaluation_deadline + timedelta(minutes=1))
    page = client.get(f"/api/evaluate/{services.evaluation_token(person)}/").json()["evaluation"]
    assert set(page) == {"is_open", "closed_reason"} and page["closed_reason"].startswith("The evaluation closed on")


# --- Page URLs (frontend phase) --------------------------------------------------------------------------


def test_emailed_page_urls_show_a_placeholder_and_never_the_data(client, scheduled, attended) -> None:
    person = attended(1)[0]
    token = services.evaluation_token(person)
    assert services.evaluation_link(person).endswith(f"/evaluate/{token}/")  # the email links to the page
    for url in (f"/register/{scheduled.pk}/", f"/evaluate/{token}/", "/startups/set-password/anything/", "/register/999999/"):
        response = client.get(url)
        assert response.status_code == 200 and response["Content-Type"].startswith("text/html")
        page = response.content.decode()
        assert "This page is not available yet" in page
        assert scheduled.title not in page and person.name not in page and person.email not in page
        assert client.post(url, "{}", content_type="application/json").status_code == 405
    assert Registration.objects.count() == 1  # the placeholder never registers anyone
