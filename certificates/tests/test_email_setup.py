"""Email settings and commands: console in DEBUG unless real sending is on, the demo-address guard,
send_test_email and retry_certificates. No real email is ever sent (no_real_smtp in the root conftest)."""

import importlib

import pytest
from django.core import mail
from django.core.mail import EmailMessage
from django.core.management import CommandError, call_command

from config import mail as config_mail


@pytest.fixture
def reload_settings(monkeypatch):
    """Factory: reload config.settings with these environment variables and return the module."""
    import config.settings

    def load(**env):
        for key, value in env.items():
            monkeypatch.setenv(key, value)
        return importlib.reload(config.settings)

    yield load
    monkeypatch.undo()  # restore the environment first, then the module
    importlib.reload(config.settings)


def test_console_while_debug_unless_real_sending(reload_settings) -> None:
    debug = reload_settings(DJANGO_DEBUG="True", EMAIL_REAL_SENDING="False")
    assert debug.MAILERS["default"]["BACKEND"] == "config.mail.ConsoleEmailBackend"
    assert debug.MAILERS["smtp"]["BACKEND"] == "config.mail.GuardedSMTPBackend"
    real = reload_settings(DJANGO_DEBUG="True", EMAIL_REAL_SENDING="True")
    assert real.MAILERS["default"]["BACKEND"] == "config.mail.GuardedSMTPBackend"
    production = reload_settings(DJANGO_DEBUG="False", EMAIL_REAL_SENDING="False")
    assert production.MAILERS["default"]["BACKEND"] == "config.mail.GuardedSMTPBackend"


def test_smtp_settings_come_from_env(reload_settings) -> None:
    loaded = reload_settings(EMAIL_HOST="smtp.example.com", EMAIL_PORT="2525", EMAIL_HOST_USER="me@example.com",
                             EMAIL_HOST_PASSWORD="app-password", EMAIL_USE_TLS="False")
    assert loaded.SMTP_MAILER["OPTIONS"] == {
        "host": "smtp.example.com", "port": 2525, "username": "me@example.com", "password": "app-password",
        "use_tls": False, "timeout": 30,
    }


@pytest.mark.parametrize(
    "address, blocked",
    [("a@demo.example.com", True), ("A@DEMO.Example.com", True), ("x@sub.demo.example.com", True),
     ("Name <a@demo.example.com>", True), ("a@example.com", False), ("a@notdemo.example.com", False)],
)
def test_blocked_addresses(address, blocked) -> None:
    assert config_mail.is_blocked_address(address) is blocked


def _guarded_send(monkeypatch) -> tuple[int, list]:
    from django.core.mail.backends.smtp import EmailBackend as SMTPBackend

    sent = []
    monkeypatch.setattr(SMTPBackend, "send_messages", lambda self, messages: sent.extend(messages) or len(messages))
    backend = config_mail.GuardedSMTPBackend(alias="test", host="localhost", port=25)
    messages = [
        EmailMessage("Only demo", "secret link x", "from@example.com", ["a@demo.example.com"]),
        EmailMessage("Mixed", "x", "from@example.com", ["real@example.com", "b@demo.example.com"]),
        EmailMessage("Real", "x", "from@example.com", ["c@example.com"]),
    ]
    return backend.send_messages(messages), sent


def test_guard_sends_real_recipients_and_prints_demo_ones_in_debug(monkeypatch, capsys, settings) -> None:
    settings.DEBUG = True
    count, sent = _guarded_send(monkeypatch)
    assert count == 3
    assert [(m.subject, m.to) for m in sent] == [("Mixed", ["real@example.com"]), ("Real", ["c@example.com"])]
    out = capsys.readouterr().out
    assert "Subject: Only demo" in out and "Subject: Mixed" in out and "Subject: Real" not in out


def test_guard_never_prints_demo_emails_without_debug(monkeypatch, capsys, caplog, settings) -> None:
    settings.DEBUG = False
    with caplog.at_level("INFO", logger="config.mail"):
        count, sent = _guarded_send(monkeypatch)
    assert count == 3  # still handled: the demo-only email counts as done
    assert [(m.subject, m.to) for m in sent] == [("Mixed", ["real@example.com"]), ("Real", ["c@example.com"])]
    assert capsys.readouterr().out == ""  # nothing printed
    assert "2 email(s) to a blocked demo domain were not sent." in caplog.text
    assert "secret link" not in caplog.text and "demo.example.com" not in caplog.text  # no body, no address


def test_console_summarizes_attachments() -> None:
    import io

    stream = io.StringIO()
    message = EmailMessage("Certificate", "Hello", "from@example.com", ["a@example.com"])
    message.attach("TBI-2026-00001.pdf", b"%PDF-1.7 " + b"x" * 5000, "application/pdf")
    config_mail.ConsoleEmailBackend(stream=stream, alias="test").send_messages([message])
    out = stream.getvalue()
    assert "[attachment TBI-2026-00001.pdf, application/pdf, 5,009 bytes - not printed]" in out
    assert "Hello" in out and len(out) < 2000  # no base64 dump


# --- send_test_email ------------------------------------------------------------------------------


def test_send_test_email_default_mailer(capsys, settings) -> None:
    call_command("send_test_email", "me@example.com")
    out = capsys.readouterr().out
    assert "Mailer: default" in out and "Sent to me@example.com." in out
    assert [m.to for m in mail.outbox] == [["me@example.com"]]


def test_send_test_email_smtp_never_prints_the_password(capsys, settings, monkeypatch) -> None:
    from django.core.mail.backends.smtp import EmailBackend as SMTPBackend

    sent = []
    monkeypatch.setattr(SMTPBackend, "send_messages", lambda self, messages: sent.extend(messages) or len(messages))
    settings.MAILERS = {
        **settings.MAILERS,
        "smtp": {
            "BACKEND": "config.mail.GuardedSMTPBackend",
            "OPTIONS": {"host": "smtp.gmail.com", "port": 587, "username": "me@gmail.com", "password": "SECRET-app-pass", "use_tls": True},
        },
    }
    call_command("send_test_email", "me@gmail.com", "--smtp")
    out = capsys.readouterr().out
    assert "Mailer: smtp" in out
    assert "SMTP server: smtp.gmail.com:587  TLS: True  user: me@gmail.com  password: set (never shown)" in out
    assert "SECRET-app-pass" not in out
    assert [m.to for m in sent] == [["me@gmail.com"]]


def test_send_test_email_reports_failures(monkeypatch) -> None:
    def fail(self, fail_silently=False, **kwargs):
        raise ConnectionRefusedError("[WinError 10061] No connection could be made")

    monkeypatch.setattr(EmailMessage, "send", fail)
    with pytest.raises(CommandError, match="Sending failed: ConnectionRefusedError"):
        call_command("send_test_email", "me@example.com")


def test_send_test_email_refuses_a_bad_address() -> None:
    with pytest.raises(CommandError, match="Not a valid email address"):
        call_command("send_test_email", "not-an-address")


@pytest.mark.django_db
def test_retry_certificates_command(capsys) -> None:
    call_command("retry_certificates")
    assert "PDFs rendered/issued: 0 (failed: 0). Emails sent: 0 (failed: 0)." in capsys.readouterr().out


def test_env_example_lists_the_email_settings() -> None:
    from django.conf import settings

    text = (settings.BASE_DIR / ".env.example").read_text(encoding="utf-8")
    for name in ("EMAIL_HOST", "EMAIL_PORT", "EMAIL_HOST_USER", "EMAIL_HOST_PASSWORD", "EMAIL_USE_TLS", "EMAIL_REAL_SENDING"):
        assert f"{name}=" in text
