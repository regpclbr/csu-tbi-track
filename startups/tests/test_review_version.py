"""The hosted review version's startup settings: the Navigatu sync can be turned off (STARTUP_SYNC_ENABLED, ASSUMPTIONS
393) and create_review_startup makes one TEST startup (ASSUMPTIONS 396)."""

from io import StringIO

import pytest
from django.core import mail
from django.core.management import CommandError, call_command
from django_q.models import Schedule

from accounts.system_status import schedule_status
from config.schedules import SCHEDULES
from startups import tasks
from startups.models import Startup
from startups.services import StartupError, create_review_startup, startup_from_password_token

pytestmark = pytest.mark.django_db

SYNC = "Daily Navigatu startup sync"


# --- STARTUP_SYNC_ENABLED ------------------------------------------------------------------------------------------


def test_setup_schedules_leaves_out_the_sync_when_turned_off(settings) -> None:
    settings.STARTUP_SYNC_ENABLED = False
    out = StringIO()
    call_command("setup_schedules", stdout=out)
    assert not Schedule.objects.filter(name=SYNC).exists()
    assert Schedule.objects.count() == len(SCHEDULES) - 1
    assert "Turned off" in out.getvalue()


def test_turning_it_off_removes_an_existing_sync_schedule(settings) -> None:
    call_command("setup_schedules", stdout=StringIO())
    assert Schedule.objects.filter(name=SYNC).exists()
    settings.STARTUP_SYNC_ENABLED = False
    call_command("setup_schedules", stdout=StringIO())
    assert not Schedule.objects.filter(name=SYNC).exists()
    call_command("setup_schedules", stdout=StringIO())  # still safe to rerun
    assert Schedule.objects.count() == len(SCHEDULES) - 1


def test_turned_on_by_default_keeps_every_schedule(settings) -> None:
    assert settings.STARTUP_SYNC_ENABLED is True
    call_command("setup_schedules", stdout=StringIO())
    assert Schedule.objects.filter(name=SYNC).count() == 1


def test_the_task_does_nothing_when_turned_off(settings, monkeypatch) -> None:
    settings.STARTUP_SYNC_ENABLED = False
    monkeypatch.setattr(tasks.services, "sync_startups", lambda **kwargs: pytest.fail("must not sync"))
    assert tasks.run_startup_sync() == {"skipped": "STARTUP_SYNC_ENABLED is False"}
    assert not Startup.objects.exists()


def test_the_manual_command_refuses_when_turned_off(settings) -> None:
    settings.STARTUP_SYNC_ENABLED = False
    with pytest.raises(CommandError, match="turned off"):
        call_command("sync_startups", "--force", stdout=StringIO())
    assert not Startup.objects.exists()


def test_system_status_shows_it_as_turned_off_not_missing(settings) -> None:
    settings.STARTUP_SYNC_ENABLED = False
    call_command("setup_schedules", stdout=StringIO())
    item = schedule_status()
    assert item.tone == "ok", item.summary
    assert item.summary == f"All {len(SCHEDULES) - 1} scheduled tasks are set up."
    row = next(row for row in item.rows if row["name"] == SYNC)
    assert row["last_result"] == "Turned off (STARTUP_SYNC_ENABLED=False)"


# --- create_review_startup -----------------------------------------------------------------------------------------


def test_creates_one_test_startup_and_emails_its_link(settings) -> None:
    out = StringIO()
    call_command("create_review_startup", "--email", "Owner@Example.com", stdout=out)
    startup = Startup.objects.get()
    assert startup.name == "TEST Startup (review)"
    assert startup.email == "owner@example.com" and startup.is_active
    assert startup.navigatu_id is None and startup.password is None
    assert len(mail.outbox) == 1 and mail.outbox[0].to == ["owner@example.com"]
    link = next(line for line in mail.outbox[0].body.splitlines() if "/startups/set-password/" in line)
    assert link.startswith(settings.SITE_URL + "/startups/set-password/")
    startup_from_password_token(link.rstrip("/").rsplit("/", 1)[1])  # the link works
    assert "/startups/set-password/" not in out.getvalue()  # never printed


def test_the_name_always_starts_with_test() -> None:
    assert create_review_startup(email="a@example.com", name="Kape Caraga").name == "TEST Kape Caraga"
    assert create_review_startup(email="b@example.com", name="test pilot").name == "test pilot"


def test_an_email_already_used_is_refused() -> None:
    Startup.objects.create(name="Real", email="real@example.com")
    with pytest.raises(CommandError, match="already exists"):
        call_command("create_review_startup", "--email", "REAL@example.com", stdout=StringIO())
    assert Startup.objects.count() == 1


def test_a_bad_email_is_refused() -> None:
    with pytest.raises(StartupError, match="valid email"):
        create_review_startup(email="nope")


def test_nothing_is_saved_when_the_email_fails(monkeypatch) -> None:
    from startups import emails

    def broken(*args, **kwargs):
        raise OSError("SMTP down")

    monkeypatch.setattr(emails, "send_set_password_email", broken)
    with pytest.raises(CommandError, match="could not be sent"):
        call_command("create_review_startup", "--email", "x@example.com", stdout=StringIO())
    assert not Startup.objects.exists()
