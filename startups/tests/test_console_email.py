"""The development console shows long links on one line, so they can be copied."""

import io

from django.core.mail import EmailMessage

from config.mail import ConsoleEmailBackend

LONG_LINK = "http://127.0.0.1:8000/startups/set-password/" + "x" * 120 + "/"


def test_console_backend_keeps_long_links_on_one_line() -> None:
    stream = io.StringIO()
    backend = ConsoleEmailBackend(stream=stream)
    backend.send_messages([EmailMessage("Subject", f"Open this link:\n\n{LONG_LINK}\n", "a@example.com", ["b@example.com"])])
    output = stream.getvalue()
    assert LONG_LINK in output.splitlines()
    assert "quoted-printable" not in output.lower()


def test_console_backend_is_the_development_default() -> None:
    from config import settings as project_settings  # tests themselves run with an in-memory backend

    assert project_settings.MAILERS["default"]["BACKEND"] == "config.mail.ConsoleEmailBackend"
