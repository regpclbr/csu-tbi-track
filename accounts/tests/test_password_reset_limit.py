"""The "forgot password" form is rate limited (shared limiter, client_ip()): 5 per email and 20 per
IP per 60 minutes. Over a limit the page looks exactly the same, but no email is sent."""

import pytest
from django.core import mail
from django.core.cache import cache

pytestmark = pytest.mark.django_db

FORGOT = "/accounts/password/forgot/"
SENT = "/accounts/password/forgot/sent/"


@pytest.fixture(autouse=True)
def fresh_counters(db):
    cache.clear()
    yield
    cache.clear()


def ask(client, email: str, ip: str = "203.0.113.5", forwarded_for: str | None = None):
    headers = {"HTTP_X_FORWARDED_FOR": forwarded_for} if forwarded_for else {}
    return client.post(FORGOT, {"email": email}, REMOTE_ADDR=ip, **headers)


def test_the_default_limits(settings) -> None:
    from config import settings as project_settings  # the module itself, not test overrides

    assert project_settings.PASSWORD_RESET_MAX_REQUESTS_PER_EMAIL == 5
    assert project_settings.PASSWORD_RESET_MAX_REQUESTS_PER_IP == 20
    assert project_settings.PASSWORD_RESET_RATE_WINDOW_MINUTES == 60


def test_one_email_gets_at_most_5_links_per_hour(client, make_member, caplog) -> None:
    make_member("Director", email="ana@example.com")
    for _ in range(5):
        response = ask(client, "ana@example.com")
        assert response.status_code == 302 and response.url == SENT
    assert len(mail.outbox) == 5

    with caplog.at_level("WARNING", logger="accounts.security"):
        limited = ask(client, "ANA@example.com ")  # the same email, ignoring case and spaces
    assert limited.status_code == 302 and limited.url == SENT  # exactly the same answer...
    assert len(mail.outbox) == 5  # ...but no email
    assert "Forgot-password rate limit reached (ip=203.0.113.5, by email): no email sent." in caplog.text
    assert "ana@example.com" not in caplog.text.lower()  # never the email


def test_the_limited_page_is_the_same_page(client, make_member) -> None:
    make_member("Director", email="ana@example.com")
    normal = ask(client, "ana@example.com")
    for _ in range(5):
        ask(client, "ana@example.com")
    limited = ask(client, "ana@example.com")
    assert (limited.status_code, limited.url) == (normal.status_code, normal.url)
    assert client.get(limited.url).content == client.get(normal.url).content


def test_one_ip_address_is_limited_to_20_requests(client, make_member) -> None:
    make_member("Director", email="ana@example.com")
    for number in range(20):  # 20 different (unknown) emails from one IP: all count
        assert ask(client, f"guess{number}@example.com").url == SENT
    assert ask(client, "ana@example.com").url == SENT  # the 21st: same page, no email
    assert mail.outbox == []
    assert ask(client, "ana@example.com", ip="198.51.100.7").url == SENT  # another IP is not blocked
    assert len(mail.outbox) == 1 and mail.outbox[0].to == ["ana@example.com"]


def test_unknown_emails_count_too_and_reveal_nothing(client) -> None:
    for _ in range(6):
        response = ask(client, "nobody@example.com")
        assert response.status_code == 302 and response.url == SENT
    assert mail.outbox == []


def test_the_limit_uses_the_trusted_proxy_header(client, make_member, settings) -> None:
    settings.TRUSTED_PROXY_HEADER = "X-Forwarded-For"
    settings.PASSWORD_RESET_MAX_REQUESTS_PER_IP = 2
    make_member("Director", email="ana@example.com")
    make_member("Admin Staff", email="ben@example.com")
    proxy = "10.0.0.1"  # every request comes from the proxy
    ask(client, "x1@example.com", ip=proxy, forwarded_for="203.0.113.5")
    ask(client, "x2@example.com", ip=proxy, forwarded_for="203.0.113.5")
    ask(client, "ana@example.com", ip=proxy, forwarded_for="203.0.113.5")  # over the limit for this client
    ask(client, "ben@example.com", ip=proxy, forwarded_for="198.51.100.7")  # another client behind the proxy
    assert [m.to for m in mail.outbox] == [["ben@example.com"]]


def test_the_window_ends(client, make_member) -> None:
    make_member("Director", email="ana@example.com")
    for _ in range(6):
        ask(client, "ana@example.com")
    assert len(mail.outbox) == 5
    cache.clear()  # what the cache does when the 60 minutes have passed
    ask(client, "ana@example.com")
    assert len(mail.outbox) == 6


def test_registration_and_forgot_password_counters_are_separate(client, make_member, settings) -> None:
    """Both use the shared limiter, but with their own scope: one never uses up the other."""
    from config.ratelimit import count_attempt

    for _ in range(settings.REGISTRATION_MAX_ATTEMPTS_PER_EMAIL + 1):
        count_attempt("registration", "email", "ana@example.com", settings.REGISTRATION_MAX_ATTEMPTS_PER_EMAIL, 60)
    make_member("Director", email="ana@example.com")
    ask(client, "ana@example.com")
    assert len(mail.outbox) == 1
