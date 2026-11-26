"""The hosted client review version (ASSUMPTIONS 391–397, docs/DEPLOY.md): server settings from environment variables,
the `check --deploy` checks, /healthz/, /robots.txt, the noindex header, the "Review version" label and the log filter
that hides personal tokens."""

import json
import logging
import os
import subprocess
import sys
from pathlib import Path

import pytest
from django.core.checks import Error, Warning
from django.urls import reverse

from config import checks
from config.logfilters import HIDDEN, HideTokensFilter, hide_tokens

BASE_DIR = Path(__file__).resolve().parents[2]
GOOD_SECRET = "k" * 30 + "Zq8-Yt3_Wv1xPn5rLm2s"  # long, no placeholder words


def settings_with_env(**env: str) -> dict:
    """Import config.settings in a fresh Python with these environment variables (load_dotenv never overrides them)
    and return a few computed values. Nothing connects to a database."""
    script = (
        "import json; from django.conf import settings; "
        "print(json.dumps({'proxy': getattr(settings, 'SECURE_PROXY_SSL_HEADER', None), "
        "'hsts': getattr(settings, 'SECURE_HSTS_SECONDS', 0), "
        "'csrf': settings.CSRF_TRUSTED_ORIGINS, 'workers': settings.Q_CLUSTER['workers'], "
        "'recycle': settings.Q_CLUSTER['recycle'], "
        "'label': settings.REVIEW_VERSION_LABEL, 'nosniff': settings.SECURE_CONTENT_TYPE_NOSNIFF}))"
    )
    full_env = {**os.environ, "DJANGO_SETTINGS_MODULE": "config.settings", "DJANGO_SECRET_KEY": GOOD_SECRET, **env}
    result = subprocess.run([sys.executable, "-c", script], cwd=BASE_DIR, env=full_env, capture_output=True, text=True,
                            timeout=60, check=True)
    return json.loads(result.stdout.strip().splitlines()[-1])


# --- Settings from the environment ------------------------------------------------------------------------------


def test_server_environment_turns_on_the_proxy_settings() -> None:
    values = settings_with_env(
        BEHIND_HTTPS_PROXY="True", SITE_URL="https://tbi-review.southeastasia.cloudapp.azure.com",
        Q_CLUSTER_WORKERS="1", REVIEW_VERSION_LABEL="Review version",
    )
    assert values["proxy"] == ["HTTP_X_FORWARDED_PROTO", "https"]
    assert values["hsts"] == 3600  # short, no includeSubDomains (shared Azure domain)
    assert values["csrf"] == ["https://tbi-review.southeastasia.cloudapp.azure.com"]
    assert values["workers"] == 1 and values["recycle"] == 50
    assert values["label"] == "Review version"
    assert values["nosniff"] is True


def test_development_defaults_trust_no_proxy_header() -> None:
    values = settings_with_env(BEHIND_HTTPS_PROXY="False", SITE_URL="http://127.0.0.1:8000", Q_CLUSTER_WORKERS="",
                               REVIEW_VERSION_LABEL="")
    assert values["proxy"] is None and values["hsts"] == 0
    assert values["csrf"] == []
    assert values["workers"] == 2
    assert values["label"] == ""


# --- check --deploy ---------------------------------------------------------------------------------------------


@pytest.fixture
def server(settings):
    """Valid server settings; each test breaks one."""
    settings.DEBUG = False
    settings.SITE_URL = "https://tbi-review.southeastasia.cloudapp.azure.com"
    settings.ALLOWED_HOSTS = ["tbi-review.southeastasia.cloudapp.azure.com"]
    settings.SECRET_KEY = GOOD_SECRET
    settings.BEHIND_HTTPS_PROXY = True
    settings.TRUSTED_PROXY_HEADER = "X-Forwarded-For"
    settings.MAILERS = {"default": {"BACKEND": "config.mail.GuardedSMTPBackend",
                                    "OPTIONS": {"username": "tbi@gmail.com", "password": "app-password"}}}
    return settings


def ids(problems) -> list[str]:
    return [problem.id for problem in problems]


def test_valid_server_settings_pass(server) -> None:
    assert checks.check_server_settings() == []


def test_development_is_never_checked(server) -> None:
    server.DEBUG = True
    server.SITE_URL = "http://127.0.0.1:8000"
    assert checks.check_server_settings() == []


def test_http_site_url_is_an_error(server) -> None:
    server.SITE_URL = "http://tbi-review.southeastasia.cloudapp.azure.com"
    problems = checks.check_server_settings()
    assert ids(problems) == ["csu_tbi.E001"] and isinstance(problems[0], Error)


def test_site_url_host_must_be_allowed(server) -> None:
    server.ALLOWED_HOSTS = ["localhost"]
    assert ids(checks.check_server_settings()) == ["csu_tbi.E002"]


@pytest.mark.parametrize("secret", ["change-me-to-a-long-random-string", "short"])
def test_placeholder_or_short_secret_is_an_error(server, secret) -> None:
    server.SECRET_KEY = secret
    assert "csu_tbi.E003" in ids(checks.check_server_settings())


def test_proxy_without_trusted_header_and_smtp_without_password_are_warnings(server) -> None:
    server.TRUSTED_PROXY_HEADER = ""
    server.MAILERS = {"default": {"BACKEND": "config.mail.GuardedSMTPBackend", "OPTIONS": {"username": "", "password": ""}}}
    problems = checks.check_server_settings()
    assert ids(problems) == ["csu_tbi.W001", "csu_tbi.W002"]
    assert all(isinstance(problem, Warning) for problem in problems)


def test_the_check_is_registered_for_check_deploy() -> None:
    from django.core.checks.registry import registry

    assert checks.check_server_settings in registry.get_checks(include_deployment_checks=True)
    assert checks.check_server_settings not in registry.get_checks(include_deployment_checks=False)


# --- /healthz/, /robots.txt, noindex -----------------------------------------------------------------------------


@pytest.mark.django_db
def test_healthz_answers_ok_without_login_or_cookie(client) -> None:
    response = client.get("/healthz/")
    assert response.status_code == 200
    assert response.content == b"ok\n"
    assert not response.cookies
    assert "no-cache" in response["Cache-Control"] or "max-age=0" in response["Cache-Control"]


@pytest.mark.django_db
def test_healthz_reports_a_database_problem_without_details(client, monkeypatch) -> None:
    from config import views

    class Broken:
        def cursor(self):
            raise RuntimeError("secret connection detail")

    monkeypatch.setattr(views, "connection", Broken())
    response = client.get("/healthz/")
    assert response.status_code == 503
    assert response.content == b"database unavailable\n"


def test_healthz_refuses_post(client) -> None:
    assert client.post("/healthz/").status_code == 405


def test_robots_txt_disallows_everything(client) -> None:
    response = client.get("/robots.txt")
    assert response.status_code == 200
    assert response["Content-Type"].startswith("text/plain")
    assert response.content.decode() == "User-agent: *\nDisallow: /\n"


@pytest.mark.django_db
@pytest.mark.parametrize("url", ["/accounts/login/", "/register/", "/robots.txt", "/healthz/", "/privacy/"])
def test_every_response_says_noindex(client, url) -> None:
    assert client.get(url)["X-Robots-Tag"] == "noindex, nofollow"


# --- Review version label ---------------------------------------------------------------------------------------


@pytest.mark.django_db
def test_login_page_shows_the_review_label_when_set(client, settings) -> None:
    settings.REVIEW_VERSION_LABEL = "Review version"
    assert "Review version" in client.get(reverse("accounts:login")).content.decode()


@pytest.mark.django_db
def test_no_label_in_development(client, settings) -> None:
    settings.REVIEW_VERSION_LABEL = ""
    assert "Review version" not in client.get(reverse("accounts:login")).content.decode()


@pytest.mark.django_db
def test_staff_pages_show_the_review_label(client, settings, admin_member) -> None:
    settings.REVIEW_VERSION_LABEL = "Review version"
    client.force_login(admin_member)
    text = client.get(reverse("accounts:profile")).content.decode()
    assert "Review version" in text


@pytest.mark.django_db
def test_public_pages_do_not_show_the_staff_label(client, settings) -> None:
    settings.REVIEW_VERSION_LABEL = "Review version"
    assert "Review version" not in client.get(reverse("register_list")).content.decode()


# --- Tokens never reach the logs --------------------------------------------------------------------------------


@pytest.mark.parametrize("path, expected", [
    ("Not Found: /evaluate/eyJyIjo1fQ:1tXyz:abc123/", f"Not Found: /evaluate/{HIDDEN}/"),
    ("Bad Request: /api/evaluate/eyJyIjo1fQ:1tXyz:abc123/", f"Bad Request: /api/evaluate/{HIDDEN}/"),
    ("Not Found: /startups/set-password/eyJzIjoxfQ:1t:sig/", f"Not Found: /startups/set-password/{HIDDEN}/"),
    ("Not Found: /api/startups/set-password/eyJzIjoxfQ:1t:sig/", f"Not Found: /api/startups/set-password/{HIDDEN}/"),
    ("Not Found: /accounts/password/reset/MQ/cx1-abc123/", f"Not Found: /accounts/password/reset/{HIDDEN}/{HIDDEN}/"),
    ("Not Found: /evaluate/abc/thanks/", f"Not Found: /evaluate/{HIDDEN}/thanks/"),
])
def test_tokens_are_hidden(path, expected) -> None:
    assert hide_tokens(path) == expected


@pytest.mark.parametrize("text", [
    "Not Found: /startups/set-password/done/",
    "Not Found: /accounts/password/reset/done/",
    "Not Found: /activities/12/",
    "GET /evaluation/7/ answered",  # the general evaluation link has no token
])
def test_other_paths_are_unchanged(text) -> None:
    assert hide_tokens(text) == text


def test_the_filter_rewrites_records_with_arguments() -> None:
    record = logging.LogRecord("django.request", logging.WARNING, __file__, 1, "Not Found: %s", ("/evaluate/secret-token/",), None)
    assert HideTokensFilter().filter(record) is True
    assert record.getMessage() == f"Not Found: /evaluate/{HIDDEN}/"
    assert "secret-token" not in record.getMessage()


def test_the_console_handler_uses_the_filter(settings) -> None:
    assert "hide_tokens" in settings.LOGGING["handlers"]["console"]["filters"]
