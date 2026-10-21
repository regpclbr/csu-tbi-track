"""Provider and sync: the provider is the only Navigatu access; sync creates/updates, never deletes,
skips email conflicts, has a deactivation safety guard, and changes nothing when run twice."""

import logging
from datetime import timedelta

import pytest
from django.core.management import CommandError, call_command
from django.db import IntegrityError
from django.utils import timezone
from django_q.models import Schedule

from registrations.models import Registration
from startups import services, tasks
from startups.models import Startup
from startups.providers import MockStartupProvider, NavigatuStartupProvider, ProviderNotAvailable, get_provider

from .conftest import STARTUP_PASSWORD, record

pytestmark = pytest.mark.django_db


# --- Provider ----------------------------------------------------------------------------------


def test_default_provider_is_the_mock(settings) -> None:
    assert settings.STARTUP_PROVIDER == "startups.providers.MockStartupProvider"
    assert isinstance(get_provider(), MockStartupProvider)


def test_mock_data_is_invented_demo_data() -> None:
    records = MockStartupProvider().list_startups()
    assert len(records) == 10
    assert all(r.email.endswith("@demo.example.com") for r in records)
    assert {r.maturity_stage for r in records} == {"Ideation", "Validation", "Early Traction", "Scaling"}
    assert sum(not r.is_active for r in records) == 1
    assert len({r.navigatu_id for r in records}) == 10
    assert MockStartupProvider().get_startup("DEMO-NAV-004").name == "Lakbay Data Labs"
    assert MockStartupProvider().get_startup("nope") is None


def test_navigatu_stub_raises_a_clear_error(settings) -> None:
    settings.STARTUP_PROVIDER = "startups.providers.NavigatuStartupProvider"
    with pytest.raises(ProviderNotAvailable, match="not available yet"):
        services.sync_startups()
    with pytest.raises(ProviderNotAvailable):
        NavigatuStartupProvider().get_startup("X")
    assert not Startup.objects.exists()


def test_unknown_provider_setting_is_a_clear_error(settings) -> None:
    settings.STARTUP_PROVIDER = "startups.providers.DoesNotExist"
    with pytest.raises(ProviderNotAvailable, match="Unknown startup provider"):
        get_provider()


def test_sync_uses_the_configured_provider(provider) -> None:
    provider([record(1)])
    services.sync_startups()
    assert Startup.objects.get().navigatu_id == "NAV-1"


def test_only_the_provider_module_reads_navigatu_data() -> None:
    """No other module mentions the mock data file or a provider class directly."""
    from pathlib import Path

    from django.conf import settings

    allowed = {Path("startups/providers.py"), Path("config/settings.py")}
    allowed_dirs = {"tests", "migrations", ".venv", "management"}
    for path in Path(settings.BASE_DIR).rglob("*.py"):
        relative = path.relative_to(settings.BASE_DIR)
        if relative in allowed or allowed_dirs & set(relative.parts):
            continue
        text = path.read_text(encoding="utf-8")
        assert "navigatu_startups.json" not in text, relative
        assert "MockStartupProvider" not in text and "NavigatuStartupProvider" not in text, relative


# --- Create, update, unchanged -------------------------------------------------------------------


def test_sync_creates_startups_without_password(provider) -> None:
    provider([record(1), record(2, is_active=False)])
    now = timezone.now()
    result = services.sync_startups(now=now)
    assert result["created"] == 2 and result["updated"] == 0 and result["deactivated"] == 0
    first = Startup.objects.get(navigatu_id="NAV-1")
    assert first.email == "startup1@demo.example.com" and first.name == "Startup 1"
    assert first.maturity_stage == "Validation" and first.last_assessment_at is not None
    assert first.password is None and first.last_synced_at == now
    assert Startup.objects.get(navigatu_id="NAV-2").is_active is False


def test_sync_twice_changes_nothing(provider) -> None:
    provider([record(1), record(2), record(3)])
    services.sync_startups()
    before = {s.pk: s.updated_at for s in Startup.objects.all()}
    later = timezone.now() + timedelta(hours=1)

    result = services.sync_startups(now=later)
    assert result == {
        "created": 0, "updated": 0, "unchanged": 3, "deactivated": 0, "skipped": 0,
        "deactivation_skipped": False, "deactivation_skipped_reason": "",
    }
    after = {s.pk: s.updated_at for s in Startup.objects.all()}
    assert after == before  # no row was saved
    assert set(Startup.objects.values_list("last_synced_at", flat=True)) == {later}  # but "last checked" is fresh


def test_sync_updates_changed_fields_and_lowercases_email(provider, make_startup) -> None:
    existing = make_startup(email="startup1@demo.example.com", navigatu_id="NAV-1", name="Old name")
    provider([record(1, name="New name", email="Startup1-New@Demo.Example.com", maturity_stage="Scaling")])
    result = services.sync_startups()
    existing.refresh_from_db()
    assert result["updated"] == 1
    assert existing.name == "New name" and existing.email == "startup1-new@demo.example.com"
    assert existing.maturity_stage == "Scaling"


def test_sync_never_touches_the_password(provider, make_startup) -> None:
    existing = make_startup(email="startup1@demo.example.com", navigatu_id="NAV-1", name="Old")
    password_hash = existing.password
    provider([record(1)])
    services.sync_startups()
    existing.refresh_from_db()
    assert existing.password == password_hash and existing.check_password(STARTUP_PASSWORD)


def test_sync_matches_by_email_ignoring_case_and_links_the_navigatu_id(provider, make_startup) -> None:
    existing = make_startup(email="startup1@demo.example.com", navigatu_id=None)
    provider([record(1, email="STARTUP1@demo.example.com")])
    result = services.sync_startups()
    existing.refresh_from_db()
    assert result["created"] == 0 and result["updated"] == 1
    assert existing.navigatu_id == "NAV-1" and Startup.objects.count() == 1


def test_navigatu_id_wins_over_email(provider, make_startup) -> None:
    existing = make_startup(email="old@demo.example.com", navigatu_id="NAV-1")
    provider([record(1, email="new@demo.example.com")])
    services.sync_startups()
    existing.refresh_from_db()
    assert existing.email == "new@demo.example.com" and Startup.objects.count() == 1


def test_inactive_in_navigatu_is_inactive_here_and_can_come_back(provider) -> None:
    provider([record(1), record(2), record(3, is_active=False)])
    services.sync_startups()
    assert not Startup.objects.get(navigatu_id="NAV-3").is_active
    provider([record(1), record(2), record(3)])
    assert services.sync_startups()["updated"] == 1
    assert Startup.objects.get(navigatu_id="NAV-3").is_active


# --- Missing startups: deactivated, never deleted --------------------------------------------------


def test_missing_startup_is_deactivated_not_deleted(provider, open_activity, caplog) -> None:
    provider([record(1), record(2), record(3)])
    services.sync_startups()
    gone = Startup.objects.get(navigatu_id="NAV-3")
    activity = open_activity()
    Registration.objects.create(
        activity=activity, template=activity.form_templates.get(), participant_category=activity.participant_categories.get(),
        startup=gone, name="Rep", email="rep@demo.example.com",
    )

    provider([record(1), record(2)])
    with caplog.at_level(logging.INFO, logger="startups.sync"):
        result = services.sync_startups()
    gone.refresh_from_db()
    assert result["deactivated"] == 1 and result["deactivation_skipped"] is False
    assert gone.is_active is False and Startup.objects.count() == 3
    assert Registration.objects.get().startup == gone  # the registration still points to it
    assert "Deactivated startup" in caplog.text


def test_never_synced_startup_missing_from_navigatu_is_deactivated(provider, make_startup) -> None:
    provider([record(1), record(2), record(3)])
    services.sync_startups()
    local = make_startup(email="local@demo.example.com", navigatu_id=None)
    assert services.sync_startups()["deactivated"] == 1
    local.refresh_from_db()
    assert local.is_active is False


# --- Safety guard ------------------------------------------------------------------------------------


@pytest.fixture
def four_active(provider):
    provider([record(n) for n in range(1, 5)])
    services.sync_startups()
    return provider


def test_empty_provider_list_deactivates_nobody(four_active, caplog) -> None:
    four_active([])
    with caplog.at_level(logging.WARNING, logger="startups.sync"):
        result = services.sync_startups()
    assert result["deactivated"] == 0 and result["deactivation_skipped"] is True
    assert result["deactivation_skipped_reason"] == "Navigatu returned no startups."
    assert Startup.objects.filter(is_active=True).count() == 4
    assert "Deactivation skipped" in caplog.text


def test_more_than_half_missing_is_skipped_but_creates_and_updates_still_happen(four_active) -> None:
    four_active([record(1, name="Renamed"), record(9)])  # 3 of 4 active would go: 75%
    result = services.sync_startups()
    assert result["deactivation_skipped"] is True and "3 of 4 active startups" in result["deactivation_skipped_reason"]
    assert result["deactivated"] == 0 and result["created"] == 1 and result["updated"] == 1
    assert Startup.objects.filter(is_active=True).count() == 5


def test_exactly_half_missing_is_applied(four_active) -> None:
    four_active([record(1), record(2)])  # 2 of 4 = 50%: not more than half
    result = services.sync_startups()
    assert result["deactivation_skipped"] is False and result["deactivated"] == 2


def test_fewer_than_half_missing_is_applied(four_active) -> None:
    four_active([record(1), record(2), record(3)])
    assert services.sync_startups()["deactivated"] == 1


def test_force_overrides_the_guard(four_active) -> None:
    four_active([record(1)])
    assert services.sync_startups()["deactivated"] == 0
    result = services.sync_startups(force=True)
    assert result["deactivated"] == 3 and result["deactivation_skipped"] is False


def test_ratio_comes_from_the_setting(four_active, settings) -> None:
    settings.STARTUP_SYNC_MAX_DEACTIVATE_RATIO = 0.25
    four_active([record(1), record(2)])
    assert services.sync_startups()["deactivation_skipped"] is True


def test_command_force_flag(four_active, capsys) -> None:
    four_active([record(1)])
    call_command("sync_startups")
    out = capsys.readouterr().out
    assert "deactivated: 0" in out and "Deactivation skipped (safety guard)" in out and "--force" in out
    call_command("sync_startups", "--force")
    assert "deactivated: 3" in capsys.readouterr().out
    assert Startup.objects.filter(is_active=True).count() == 1


def test_scheduled_task_never_forces(four_active) -> None:
    four_active([])
    assert tasks.run_startup_sync()["deactivation_skipped"] is True
    assert Startup.objects.filter(is_active=True).count() == 4


# --- Conflicts and bad records ------------------------------------------------------------------------


def test_two_navigatu_records_with_the_same_email_are_skipped(provider, caplog) -> None:
    provider([record(1), record(2, email="STARTUP1@demo.example.com"), record(3)])
    with caplog.at_level(logging.WARNING, logger="startups.sync"):
        result = services.sync_startups()
    assert result["skipped"] == 2 and result["created"] == 1
    assert list(Startup.objects.values_list("navigatu_id", flat=True)) == ["NAV-3"]
    assert "Nothing was merged" in caplog.text


def test_email_belonging_to_another_row_is_skipped(provider, make_startup) -> None:
    first = make_startup(email="startup1@demo.example.com", navigatu_id="NAV-1", name="One")
    second = make_startup(email="startup2@demo.example.com", navigatu_id="NAV-2", name="Two")
    # NAV-1 asks for startup2's current email (owned by another row); NAV-2 moves to a new email.
    provider([record(1, email="startup2@demo.example.com", name="One renamed"), record(2, email="two-new@demo.example.com", name="Two")])
    result = services.sync_startups()
    first.refresh_from_db()
    second.refresh_from_db()
    assert result["skipped"] == 1
    assert first.name == "One" and first.email == "startup1@demo.example.com" and first.is_active  # untouched
    assert second.email == "two-new@demo.example.com"  # NAV-2 itself was updated normally


def test_email_match_with_a_different_navigatu_id_is_skipped(provider, make_startup) -> None:
    owner = make_startup(email="startup1@demo.example.com", navigatu_id="NAV-OTHER", name="Owner")
    provider([record(1)])
    result = services.sync_startups()
    owner.refresh_from_db()
    assert result["skipped"] == 1 and result["created"] == 0 and result["deactivated"] == 0
    assert owner.navigatu_id == "NAV-OTHER" and owner.is_active  # protected: not deactivated either


@pytest.mark.parametrize("bad", [{"email": "not-an-email"}, {"name": "  "}, {"navigatu_id": ""}, {"contact_number": "1" * 31}])
def test_unusable_records_are_skipped(provider, bad) -> None:
    provider([record(1, **bad), record(2)])
    result = services.sync_startups()
    assert result["skipped"] == 1 and result["created"] == 1


def test_emails_are_unique_ignoring_case_in_the_database(make_startup) -> None:
    make_startup(email="kalinaw@demo.example.com")
    with pytest.raises(IntegrityError):
        Startup.objects.create(name="Copy", email="KALINAW@demo.example.com")


def test_api_turns_a_missing_provider_into_503(client, admin_member, settings) -> None:
    settings.STARTUP_PROVIDER = "startups.providers.NavigatuStartupProvider"
    client.force_login(admin_member)
    response = client.post("/startups/api/sync/")
    assert response.status_code == 503 and "not available yet" in response.json()["error"]


def test_sync_command_reports_a_missing_provider(settings) -> None:
    settings.STARTUP_PROVIDER = "startups.providers.NavigatuStartupProvider"
    with pytest.raises(CommandError, match="not available yet"):
        call_command("sync_startups")


# --- Schedule and seed_demo -------------------------------------------------------------------------------


def test_setup_schedules_adds_the_daily_sync_once() -> None:
    call_command("setup_schedules")
    call_command("setup_schedules")
    sync = Schedule.objects.get(name=tasks.SYNC_SCHEDULE_NAME)
    assert Schedule.objects.filter(name=tasks.SYNC_SCHEDULE_NAME).count() == 1
    assert sync.func == tasks.SYNC_TASK and sync.schedule_type == Schedule.DAILY and sync.repeats == -1
    local = timezone.localtime(sync.next_run)
    assert (local.hour, local.minute) == (7, 0)


def test_seed_demo_syncs_mock_startups_twice_safely(settings, tmp_path, make_startup) -> None:
    settings.DEBUG = True
    real = make_startup(email="real@example.org", navigatu_id="REAL-1")  # not demo data
    call_command("seed_demo")
    call_command("seed_demo")
    assert Startup.objects.filter(email__endswith="@demo.example.com").count() == 10
    real.refresh_from_db()
    assert real.is_active  # seed_demo's mock sync never deactivates non-demo startups
