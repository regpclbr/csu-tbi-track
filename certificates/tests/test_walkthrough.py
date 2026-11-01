"""The demo walkthrough command runs the whole flow through the real services (as documented)."""

from datetime import datetime, time

import pytest
from django.core import mail
from django.core.management import CommandError, call_command
from django.utils import timezone

from activities.models import Activity
from certificates.models import Certificate
from registrations.models import Registration

pytestmark = pytest.mark.django_db


def run(*args, **options) -> None:
    call_command("certificate_walkthrough", *args, **options)


def test_refuses_without_debug(admin_member, settings) -> None:
    settings.DEBUG = False
    with pytest.raises(CommandError, match="demo-only"):
        run("status", "1")


def test_whole_flow(admin_member, director, defaults, settings, tmp_path, run_tasks, django_capture_on_commit_callbacks, capsys) -> None:
    settings.DEBUG = True
    call_command("make_sample_certificate_images", str(tmp_path))
    with django_capture_on_commit_callbacks(execute=True):
        run("design", str(tmp_path))
        run("activity")
    activity_id = Activity.objects.get().pk
    ana, ben, carla = Registration.objects.order_by("pk")

    with django_capture_on_commit_callbacks(execute=True):
        run("generate", str(activity_id))
        run("evaluate", str(ben.pk), name="Benedict Walkthrough", email="ben.personal@example.com")  # BEFORE approval
        run("preview", str(Certificate.objects.get(registration=ana).pk), str(tmp_path / "preview.pdf"))
        run("review", str(activity_id))
        run("sign", str(activity_id), "director")
        run("sign", str(activity_id), "vp")
    assert not [m for m in mail.outbox if m.subject.startswith("Your Certificate")]  # not approved yet

    with django_capture_on_commit_callbacks(execute=True):
        run("approve", str(activity_id))
    sent = [m for m in mail.outbox if m.subject.startswith("Your Certificate")]
    assert [m.to for m in sent] == [["ben.personal@example.com"]]  # sent right after approval

    with django_capture_on_commit_callbacks(execute=True):
        run("evaluate", str(ana.pk), name="Ana Maria Walkthrough", email="ana.personal@example.com")  # AFTER approval
    sent = [m for m in mail.outbox if m.subject.startswith("Your Certificate")]
    assert [m.to for m in sent] == [["ben.personal@example.com"], ["ana.personal@example.com"]]
    assert not Certificate.objects.filter(registration=carla).exists()  # did not attend

    run("handover", str(activity_id))
    run("save-pdf", str(Certificate.objects.get(registration=ana).pk), str(tmp_path / "ana.pdf"))
    assert (tmp_path / "ana.pdf").read_bytes().startswith(b"%PDF-") and b"Ana Maria Walkthrough" in (tmp_path / "ana.pdf").read_bytes()
    run("status", str(activity_id))
    out = capsys.readouterr().out
    assert out.count("Issued") >= 3 and "sent=yes" in out
    with pytest.raises(CommandError, match="Wrong arguments"):
        run("sign", str(activity_id))


@pytest.mark.parametrize("moment", [time(23, 30), time(23, 59, 59), time(0, 0, 30)], ids=["23:30", "23:59:59", "00:00:30"])
def test_activity_step_works_at_any_time_of_day(director, defaults, settings, clock, moment) -> None:
    """(Acts as the Director: since ASSUMPTIONS 339 only "design" defaults to the System Administrator.)
    The activity starts today and its registration deadline must be on that date. "now + 1 hour" was tomorrow
    after 23:00 Manila time and the step failed; at 23:59:59 the clock even passes midnight during the step."""
    settings.DEBUG = True
    clock(datetime.combine(datetime(2026, 11, 20).date(), moment))
    run("activity")
    activity = Activity.objects.get()
    assert activity.status.name == "Conducted" and activity.registrations.count() == 3
    assert activity.start_date == datetime(2026, 11, 20).date()
    assert timezone.localtime(activity.registration_deadline).date() == activity.start_date
