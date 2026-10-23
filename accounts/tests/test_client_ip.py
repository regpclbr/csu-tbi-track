"""config.network.client_ip(): REMOTE_ADDR, or the trusted proxy header when TRUSTED_PROXY_HEADER is
set. The staff login lockout and the public registration limit both use it."""

import pytest
from django.core.cache import cache
from django.test import RequestFactory
from django.urls import reverse

from accounts import services
from config.network import client_ip

factory = RequestFactory()


def request(**meta):
    return factory.get("/", REMOTE_ADDR="10.0.0.1", **meta)


def test_remote_addr_by_default(settings) -> None:
    settings.TRUSTED_PROXY_HEADER = ""
    assert client_ip(request(HTTP_X_FORWARDED_FOR="203.0.113.5")) == "10.0.0.1"  # the header is not trusted


@pytest.mark.parametrize(
    "header, meta, expected",
    [
        ("X-Forwarded-For", {"HTTP_X_FORWARDED_FOR": "203.0.113.5"}, "203.0.113.5"),
        # Addresses before the last one come from the client and may be made up: the last one is used.
        ("X-Forwarded-For", {"HTTP_X_FORWARDED_FOR": "1.2.3.4, 203.0.113.5"}, "203.0.113.5"),
        ("x-real-ip", {"HTTP_X_REAL_IP": " 2001:db8::1 "}, "2001:db8::1"),
        ("X-Forwarded-For", {}, "10.0.0.1"),  # missing: REMOTE_ADDR
        ("X-Forwarded-For", {"HTTP_X_FORWARDED_FOR": "not-an-ip"}, "10.0.0.1"),  # malformed: REMOTE_ADDR
        ("X-Forwarded-For", {"HTTP_X_FORWARDED_FOR": " , "}, "10.0.0.1"),
    ],
)
def test_trusted_proxy_header(settings, header, meta, expected) -> None:
    settings.TRUSTED_PROXY_HEADER = header
    assert client_ip(request(**meta)) == expected


def test_login_lockout_uses_the_same_function() -> None:
    assert services.client_ip is client_ip


@pytest.mark.django_db
def test_login_lockout_per_ip_behind_a_proxy(client, make_member, settings) -> None:
    """Behind a proxy everyone shares REMOTE_ADDR; with the header set, only the guessing client is locked."""
    settings.TRUSTED_PROXY_HEADER = "X-Forwarded-For"
    settings.LOGIN_MAX_FAILED_ATTEMPTS_PER_IP = 3
    cache.clear()
    member = make_member("Director", email="director@example.com")
    login = reverse("accounts:login")
    for number in range(3):
        client.post(login, {"email": f"guess{number}@example.com", "password": "x"}, REMOTE_ADDR="10.0.0.1", HTTP_X_FORWARDED_FOR="203.0.113.5")
    locked = client.post(login, {"email": member.email, "password": "Strong-Pass-2026!"}, REMOTE_ADDR="10.0.0.1", HTTP_X_FORWARDED_FOR="203.0.113.5")
    assert "Too many failed attempts" in locked.content.decode()
    other = client.post(login, {"email": member.email, "password": "Strong-Pass-2026!"}, REMOTE_ADDR="10.0.0.1", HTTP_X_FORWARDED_FOR="198.51.100.7")
    assert other.status_code == 302  # another person behind the same proxy logs in
    cache.clear()
