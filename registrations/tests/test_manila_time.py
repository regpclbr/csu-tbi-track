"""Every API shows date-times in Manila time with the offset, e.g. "2026-10-26T18:00:00+08:00"
(ASSUMPTIONS item 79), never UTC ("...Z")."""

import json
import re
from datetime import datetime, timedelta
from datetime import timezone as dt_timezone

import pytest
from django.utils import timezone

from activities import services as activity_services
from config.json import JsonResponse
from registrations import services

from .conftest import CERT, evaluation_answers, opens_at

DATETIME = re.compile(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?(Z|[+-]\d{2}:\d{2})")


def offsets(response) -> set[str]:
    """The time-zone offsets of every date-time in a JSON response."""
    return {match.group(1) for match in DATETIME.finditer(response.content.decode())}


def test_encoder_converts_utc_to_manila() -> None:
    utc_10am = datetime(2026, 10, 26, 10, 0, tzinfo=dt_timezone.utc)
    body = json.loads(JsonResponse({"at": utc_10am}).content)
    assert body == {"at": "2026-10-26T18:00:00+08:00"}


@pytest.mark.django_db
def test_public_register_shows_manila_time(client, scheduled) -> None:
    response = client.get(f"/api/register/{scheduled.pk}/")
    deadline = json.loads(response.content)["activity"]["registration_deadline"]
    assert deadline == timezone.localtime(scheduled.registration_deadline).isoformat()
    assert deadline.endswith("+08:00")
    assert offsets(response) == {"+08:00"}


@pytest.mark.django_db
def test_evaluation_page_has_no_utc_times(client, attended, monkeypatch) -> None:
    person = attended(1)[0]
    activity = person.activity
    monkeypatch.setattr(services.timezone, "now", lambda: opens_at(activity) - timedelta(hours=1))
    response = client.get(f"/api/evaluate/{services.evaluation_token(person)}/")
    page = json.loads(response.content)["evaluation"]
    assert page["closed_reason"].startswith("The evaluation opens on")  # written in Manila time
    assert "Z" not in offsets(response) and "+00:00" not in offsets(response)


@pytest.mark.django_db
def test_staff_apis_show_manila_time(client, director, scheduled, signup) -> None:
    signup(scheduled, "ana@example.com")
    activity_services.update_activity(director, scheduled, {"venue": "Room 101"})
    client.force_login(director)
    for url in [
        f"/registrations/api/activities/{scheduled.pk}/registrations/",
        f"/activities/api/{scheduled.pk}/",
        f"/activities/api/{scheduled.pk}/audit-log/",
    ]:  # (the activity list rows hold only dates, no date-times)
        response = client.get(url)
        assert response.status_code == 200, url
        found = offsets(response)
        assert found and found <= {"+08:00"}, (url, found)


@pytest.mark.django_db
def test_member_api_shows_manila_time(client, admin_member) -> None:
    client.force_login(admin_member)  # sets last_login
    response = client.get("/accounts/api/members/")
    assert offsets(response) == {"+08:00"}


@pytest.mark.django_db
def test_submitted_evaluation_flow_still_works(client, attended, monkeypatch) -> None:
    person = attended(1)[0]
    monkeypatch.setattr(services.timezone, "now", lambda: opens_at(person.activity) + timedelta(hours=1))
    url = f"/api/evaluate/{services.evaluation_token(person)}/"
    payload = {**CERT, "answers": evaluation_answers(person.activity)}
    assert client.post(url, json.dumps(payload), content_type="application/json").status_code == 201
