"""Server settings checks for `python manage.py check --deploy` (ASSUMPTIONS 391).

The release step of the hosted review version (deploy/release.sh) runs `check --deploy --fail-level ERROR` before
migrating, so a server with an http:// SITE_URL, a SITE_URL that is not in ALLOWED_HOSTS or a placeholder secret key
never starts. Only with DEBUG off (development is never checked). Registered in accounts.apps.
"""

from urllib.parse import urlsplit

from django.conf import settings
from django.core.checks import Error, Tags, Warning, register

PLACEHOLDER_MARKERS = ("change-me", "changeme", "your-", "example")


@register(Tags.security, deploy=True)
def check_server_settings(app_configs=None, **kwargs) -> list:
    if settings.DEBUG:
        return []
    problems = []
    site = urlsplit(settings.SITE_URL)
    if site.scheme != "https":
        problems.append(Error(
            "SITE_URL must start with https:// on the server: every emailed link and QR code uses it, and the "
            "session cookies only work over HTTPS.", hint="Set SITE_URL=https://<name>.<region>.cloudapp.azure.com in .env.",
            id="csu_tbi.E001",
        ))
    host = (site.hostname or "").lower()
    allowed = [h.lower() for h in settings.ALLOWED_HOSTS]
    if host and host not in allowed and "*" not in allowed:
        problems.append(Error(
            f"SITE_URL's host ({host}) is not in DJANGO_ALLOWED_HOSTS, so every page would answer 400.",
            hint="Add it to DJANGO_ALLOWED_HOSTS in .env.", id="csu_tbi.E002",
        ))
    secret = settings.SECRET_KEY
    if len(secret) < 40 or any(marker in secret.lower() for marker in PLACEHOLDER_MARKERS):
        problems.append(Error(
            "DJANGO_SECRET_KEY is a placeholder or too short (at least 40 characters).",
            hint='Make one with: python -c "import secrets; print(secrets.token_urlsafe(50))"', id="csu_tbi.E003",
        ))
    if getattr(settings, "BEHIND_HTTPS_PROXY", False) and not settings.TRUSTED_PROXY_HEADER:
        problems.append(Warning(
            "BEHIND_HTTPS_PROXY is on but TRUSTED_PROXY_HEADER is empty: every visitor would share the proxy's IP "
            "address in the login lockout and the rate limits.", hint="Set TRUSTED_PROXY_HEADER=X-Forwarded-For.",
            id="csu_tbi.W001",
        ))
    smtp = settings.MAILERS["default"].get("OPTIONS", {})
    if settings.MAILERS["default"]["BACKEND"].endswith("SMTPBackend") and not (smtp.get("username") and smtp.get("password")):
        problems.append(Warning(
            "Emails are sent through SMTP but EMAIL_HOST_USER or EMAIL_HOST_PASSWORD is empty: invitations, evaluation "
            "links and certificates will fail.", hint="Set the Gmail address and its App Password in .env.",
            id="csu_tbi.W002",
        ))
    return problems
