"""Strict request values: members_api refuses non-text values (400, not 500), and true/false query
values elsewhere accept only true or false."""

import json

import pytest

from config.parsing import parse_bool

pytestmark = pytest.mark.django_db


def post_json(client, url: str, data: dict):
    return client.post(url, json.dumps(data), content_type="application/json")


@pytest.mark.parametrize(
    "body",
    [
        {"name": 123, "email": "a@example.com", "role_id": 1},
        {"name": "Ana", "email": ["a@example.com"], "role_id": 1},
        {"name": "Ana", "email": "a@example.com", "role_id": "abc"},
        {"name": "Ana", "email": "a@example.com", "role_id": True},
        {"name": "Ana", "email": "a@example.com", "role_id": 1.5},
    ],
)
def test_create_member_refuses_values_of_the_wrong_kind(client, admin_member, body) -> None:
    client.force_login(admin_member)
    response = post_json(client, "/accounts/api/members/", body)
    assert response.status_code == 400 and "error" in response.json()


def test_change_role_refuses_a_role_id_that_is_not_a_number(client, admin_member, make_member) -> None:
    member = make_member("Director")
    client.force_login(admin_member)
    for role_id in ("abc", None, [1]):
        response = post_json(client, f"/accounts/api/members/{member.pk}/role/", {"role_id": role_id})
        assert response.status_code == 400 and response.json() == {"error": "role_id must be a number."}


def test_parse_bool() -> None:
    from django.core.exceptions import ValidationError

    assert [parse_bool(v, "x") for v in (True, False, "true", "False", " TRUE ")] == [True, False, True, False, True]
    assert parse_bool(None, "x") is None and parse_bool("", "x", default=False) is False
    for bad in (1, 0, "1", "yes", "no", "on", [], {}):
        with pytest.raises(ValidationError):
            parse_bool(bad, "x")


def test_notification_unread_filter_is_strict(client, admin_member) -> None:
    client.force_login(admin_member)
    assert client.get("/notifications/api/", {"unread": "true"}).status_code == 200
    assert client.get("/notifications/api/", {"unread": "1"}).json() == {"errors": {"unread": ["unread must be true or false."]}}


def test_document_filters_are_strict(client, admin_member) -> None:
    client.force_login(admin_member)
    assert client.get("/documents/api/", {"searchable": "false"}).status_code == 200
    assert client.get("/documents/api/", {"searchable": "yes"}).status_code == 400
