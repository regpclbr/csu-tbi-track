"""Activity objectives (ASSUMPTIONS 428): optional, one per line (lines trimmed, empty lines dropped), set on the activity
form and the JSON API through create_activity / update_activity, logged in History, shown on the Overview."""

import json

import pytest
from django.core.exceptions import ValidationError
from django.test import Client
from django.urls import reverse

from activities import services
from activities.models import ActivityAuditLog

pytestmark = pytest.mark.django_db

TYPED = "  Teach pitching basics  \n\n   Connect startups   with   mentors\n"
CLEAN = "Teach pitching basics\nConnect startups with mentors"


def test_optional_and_tidied_on_create(director, valid_data) -> None:
    assert services.create_activity(director, valid_data()).objectives is None
    activity = services.create_activity(director, valid_data(title="With objectives", objectives=TYPED))
    assert activity.objectives == CLEAN
    assert services.objective_lines(activity) == ["Teach pitching basics", "Connect startups with mentors"]
    assert services.create_activity(director, valid_data(title="Blank", objectives=" \n ")).objectives is None


def test_an_edit_is_logged_in_history(director, new_activity) -> None:
    services.update_activity(director, new_activity, {"objectives": TYPED})
    row = ActivityAuditLog.objects.get(activity=new_activity, field_changed="Objectives")
    assert row.new_value.startswith("Teach pitching basics") and row.changed_by == director
    services.update_activity(director, new_activity, {"objectives": CLEAN + "\n"})  # same after tidying: no new row
    assert ActivityAuditLog.objects.filter(activity=new_activity, field_changed="Objectives").count() == 1
    services.update_activity(director, new_activity, {"objectives": ""})
    new_activity.refresh_from_db()
    assert new_activity.objectives is None


def test_too_long_is_refused(director, new_activity) -> None:
    with pytest.raises(ValidationError) as refused:
        services.update_activity(director, new_activity, {"objectives": "x" * (services.OBJECTIVES_MAX + 1)})
    assert "objectives" in refused.value.message_dict


def test_form_overview_and_api(director, new_activity) -> None:
    client = Client()
    client.force_login(director)
    services.update_activity(director, new_activity, {"objectives": CLEAN})
    edit = client.get(reverse("activities:edit", args=[new_activity.pk])).content.decode()
    assert 'name="objectives"' in edit and "Teach pitching basics" in edit
    assert 'name="objectives"' in client.get(reverse("activities:create")).content.decode()
    overview = client.get(reverse("activities:detail", args=[new_activity.pk])).content.decode()
    block = overview.split('id="activity-objectives"')[1].split("</ul>")[0]
    assert "<li>Teach pitching basics</li>" in block and "<li>Connect startups with mentors</li>" in block
    detail = client.get(reverse("activities:api_detail", args=[new_activity.pk])).json()
    assert detail["activity"]["objectives"] == ["Teach pitching basics", "Connect startups with mentors"]
    response = client.post(reverse("activities:api_update", args=[new_activity.pk]),
                           json.dumps({"objectives": "Only one"}), content_type="application/json")
    assert response.status_code == 200
    new_activity.refresh_from_db()
    assert new_activity.objectives == "Only one"
