"""The startup portal is removed (ASSUMPTIONS 458): migrations registrations/0015, notifications/0005 and startups/0003.
Registrations made through the portal are KEPT as online registrations (nothing deleted, every other value unchanged);
notifications addressed to a startup are deleted (no recipient any more); every member notification stays; the
STARTUP table and both columns are gone. The app's pages, URLs and the startup report section are gone too."""

import pytest
from django.core.management import call_command
from django.db import connection
from django.urls import NoReverseMatch, reverse

from notifications.models import Notification
from registrations.models import Registration


def tables() -> set[str]:
    with connection.cursor() as cursor:
        return set(connection.introspection.table_names(cursor))


def columns(table: str) -> set[str]:
    with connection.cursor() as cursor:
        return {c.name for c in connection.introspection.get_table_description(cursor, table)}


@pytest.mark.django_db(transaction=True)
def test_portal_registrations_become_online_and_nothing_else_is_lost(attended, scheduled, director, capsys) -> None:
    ana, ben = attended(2)
    Notification.objects.create(member=director, message="For the Director", type="test")
    try:
        call_command("migrate", "registrations", "0014", verbosity=0)
        call_command("migrate", "notifications", "0004", verbosity=0)
        assert "startup" in tables() and "startup_id" in columns("registration")
        with connection.cursor() as cursor:
            cursor.execute("INSERT INTO startup (navigatu_id, name, email, contact_number, is_active, created_at, "
                           "updated_at) VALUES ('NV-1', 'Kape Surigao', 'kape@example.com', '', true, now(), now()) "
                           "RETURNING startup_id")
            startup_id = cursor.fetchone()[0]
            cursor.execute("UPDATE registration SET startup_id = %s, registration_source = 'portal' "
                           "WHERE registration_id = %s", [startup_id, ana.pk])
            cursor.execute("INSERT INTO notification (startup_id, message, type, created_at, is_read) "
                           "VALUES (%s, 'For the startup', 'test', now(), false)", [startup_id])
    finally:
        call_command("migrate", verbosity=0)
    out = capsys.readouterr().out
    assert "1 registration(s) made through the startup portal are now online registrations." in out
    assert "1 notification(s) addressed to startups were deleted" in out
    assert "startup" not in tables() and "startup_id" not in columns("registration")
    assert "startup_id" not in columns("notification")
    ana_after = Registration.objects.get(pk=ana.pk)
    assert ana_after.registration_source == "online" and ana_after.email == ana.email
    assert ana_after.attendance_confirmed and ana_after.attendance_days.count() == ana.attendance_days.count()
    assert Registration.objects.filter(pk__in=[ana.pk, ben.pk]).count() == 2
    assert list(Notification.objects.values_list("message", flat=True)) == ["For the Director"]


@pytest.mark.django_db
def test_the_portal_and_the_startups_pages_are_gone(client, director) -> None:
    for name in ("startups:list", "startups:portal_login_page", "startup_set_password_done", "api_startup_set_password"):
        with pytest.raises(NoReverseMatch):
            reverse(name)
    assert client.get("/startups/portal/").status_code == 404
    client.force_login(director)
    assert "Startups" not in client.get(reverse("home")).content.decode().split("<main")[0]  # not in the sidebar
    assert "startup portal" not in client.get(reverse("accounts:login")).content.decode().lower()
    assert set(Registration.Source.values) == {"online", "walk_in"}


@pytest.mark.django_db
def test_the_startups_report_section_and_number_are_gone(roles) -> None:
    from reports import content, metrics

    assert "startups" not in content.SECTIONS
    assert "startups" not in content.REPORT_TYPES["participation"].default_sections
    assert content.ordered_sections(["summary", "startups"]) == ["summary"]  # older report rows that list it
    assert "startups_engaged" not in metrics.period_summary(metrics.ReportFilters(
        __import__("datetime").date(2026, 1, 1), __import__("datetime").date(2026, 12, 31)))
