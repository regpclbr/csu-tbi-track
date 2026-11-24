"""Migrations registrations/0011-0013 (ASSUMPTIONS 434): each attended registration gets one ATTENDANCE_DAY row with
its old confirmation method (email / wet_signature kept), marker and time; the old columns are then dropped."""

from datetime import datetime, time, timedelta

import pytest
from django.core.management import call_command
from django.db import connection
from django.utils import timezone

from registrations.models import AttendanceDay, Registration


def columns() -> set[str]:
    with connection.cursor() as cursor:
        return {c.name for c in connection.introspection.get_table_description(cursor, "registration")}


@pytest.mark.django_db(transaction=True)
def test_old_attendance_is_copied_to_day_rows(attended, scheduled, director, capsys) -> None:
    ana, ben, cid = attended(3)
    second_day = timezone.make_aware(datetime.combine(scheduled.end_date, time(10, 30)))
    try:
        call_command("migrate", "registrations", "0010", verbosity=0)
        assert "confirmation_method" in columns()
        with connection.cursor() as cursor:
            cursor.execute("UPDATE registration SET confirmation_method = 'email', attendance_marked_at = %s "
                           "WHERE registration_id = %s", [second_day, ana.pk])
            cursor.execute("UPDATE registration SET attendance_marked_at = %s WHERE registration_id = %s",
                           [second_day + timedelta(days=40), ben.pk])  # outside the dates: the start date is used
            cursor.execute("UPDATE registration SET attendance_confirmed = false, confirmation_method = NULL "
                           "WHERE registration_id = %s", [cid.pk])
    finally:
        call_command("migrate", verbosity=0)
    assert "confirmation_method" not in columns() and "attendance_marked_by_id" not in columns()
    assert "2 attended registration(s), 2 attendance day row(s) created" in capsys.readouterr().out
    rows = {row.registration_id: row for row in AttendanceDay.objects.all()}
    assert set(rows) == {ana.pk, ben.pk}
    assert (rows[ana.pk].method, rows[ana.pk].day, rows[ana.pk].checked_in_at) == ("email", scheduled.end_date, second_day)
    assert (rows[ben.pk].method, rows[ben.pk].day, rows[ben.pk].marked_by_id) == ("wet_signature", scheduled.start_date, director.pk)
    assert Registration.objects.get(pk=cid.pk).attendance_days.count() == 0
    assert set(Registration.objects.values_list("registration_source", flat=True)) == {"online"}
