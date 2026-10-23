"""Sessions end after 8 hours and when the browser closes; cookies are HTTPS-only when DEBUG is off."""

import importlib

import pytest

from conftest import STRONG_PASSWORD


@pytest.fixture
def reload_settings(monkeypatch):
    """Factory: reload config.settings with these environment variables and return the module."""
    import config.settings

    def load(**env):
        for key, value in env.items():
            monkeypatch.setenv(key, value)
        return importlib.reload(config.settings)

    yield load
    monkeypatch.undo()
    importlib.reload(config.settings)


def test_sessions_last_8_hours_and_end_with_the_browser(reload_settings) -> None:
    loaded = reload_settings()
    assert loaded.SESSION_COOKIE_AGE == 8 * 60 * 60
    assert loaded.SESSION_EXPIRE_AT_BROWSER_CLOSE is True


def test_secure_cookies_switch_on_without_debug(reload_settings) -> None:
    production = reload_settings(DJANGO_DEBUG="False")
    assert production.SESSION_COOKIE_SECURE is True and production.CSRF_COOKIE_SECURE is True
    development = reload_settings(DJANGO_DEBUG="True")
    assert development.SESSION_COOKIE_SECURE is False and development.CSRF_COOKIE_SECURE is False


@pytest.mark.django_db
def test_the_login_cookie_is_a_browser_session_cookie(client, make_member) -> None:
    member = make_member("Director")
    response = client.post("/accounts/login/", {"email": member.email, "password": STRONG_PASSWORD})
    cookie = response.cookies["sessionid"]
    assert cookie["expires"] == "" and cookie["max-age"] == ""  # gone when the browser closes
    assert client.session.get_expiry_age() == 8 * 60 * 60  # and at most 8 hours on the server
