"""The JSON API answers in JSON: 401 when not logged in (never a redirect to the login page), 403
during the first-sign-in password change, 404 and 500 as JSON. HTML URLs keep redirecting."""

import pytest
from django.test import Client

from activities import services as activity_services
from config.views import NOT_FOUND, SERVER_ERROR

pytestmark = pytest.mark.django_db

LOGIN_REQUIRED = {"error": "Login required."}
API_URLS = [
    "/api/lookups/",
    "/activities/api/",
    "/accounts/api/members/",
    "/documents/api/",
    "/certificates/api/",
    "/reports/api/dashboard/",
    "/assistant/api/chats/",
    "/notifications/api/",
]


@pytest.mark.parametrize("url", API_URLS)
def test_not_logged_in_gets_401_json(client, url) -> None:
    response = client.get(url)
    assert response.status_code == 401 and response.json() == LOGIN_REQUIRED
    assert "Location" not in response


def test_a_post_without_login_is_401_too(client) -> None:
    response = client.post("/notifications/api/read-all/")
    assert response.status_code == 401 and response.json() == LOGIN_REQUIRED


def test_html_urls_still_redirect_to_the_login_page(client) -> None:
    for url in ("/", "/documents/1/download/", "/reports/1/download/", "/accounts/password/change/"):
        response = client.get(url)
        assert response.status_code == 302 and "/accounts/login/" in response.url


def test_a_deactivated_member_gets_401(client, make_member) -> None:
    member = make_member("Director")
    client.force_login(member)
    member.is_active = False
    member.save(update_fields=["is_active"])
    assert client.get("/activities/api/").status_code == 401


def test_first_sign_in_api_calls_get_403_json(client, make_member) -> None:
    client.force_login(make_member("Director", must_change_password=True))
    response = client.get("/activities/api/")
    assert response.status_code == 403 and response.json() == {"error": "Change your password first (first sign-in)."}
    assert client.get("/").url == "/accounts/password/change/"  # pages still redirect


def test_404_is_json_for_the_api(client, director) -> None:
    client.force_login(director)
    unknown_url = client.get("/api/no-such-endpoint/")
    assert unknown_url.status_code == 404 and unknown_url.json() == {"error": NOT_FOUND}  # no URL list
    missing_row = client.get("/activities/api/999999/")
    assert missing_row.status_code == 404 and set(missing_row.json()) == {"error"}
    page = client.get("/no-such-page/")
    assert page.status_code == 404 and page["Content-Type"].startswith("text/html")


def test_500_is_json_for_the_api_and_never_shows_the_error(director, monkeypatch) -> None:
    def broken():
        raise RuntimeError("secret detail")

    monkeypatch.setattr(activity_services, "activity_stats", broken)
    client = Client(raise_request_exception=False)
    client.force_login(director)
    response = client.get("/activities/api/stats/")
    assert response.status_code == 500 and response.json() == {"error": SERVER_ERROR}
    assert "secret detail" not in response.content.decode()


@pytest.fixture
def director(make_member):
    return make_member("Director")
