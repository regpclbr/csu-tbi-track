"""Data, part 2 of 3 (ASSUMPTIONS 434). Runs before 0013 drops the old columns.

1. registration_source: "portal" for registrations made by a logged-in startup (startup_id set), otherwise it stays
   "online" (0011's default). Nobody was a walk-in before.
2. Every registration marked as attended gets ONE ATTENDANCE_DAY row (the old data knows no days):
   - day = the Manila date of attendance_marked_at when that date is within the activity's start_date..end_date,
     otherwise the activity's start_date;
   - method = the old confirmation_method (email / wet_signature; "wet_signature" when it was empty), so the old value
     is kept (owner decision 2026-11-24: email and wet_signature exist only on these copied rows);
   - marked_by = attendance_marked_by; checked_in_at and created_at = attendance_marked_at (the registration's
     updated_at when that was empty).
   The migration prints the counts and stops (rolling everything back) if the number of day rows does not equal the
   number of attended registrations.
Nothing else changes: attendance_confirmed keeps its value (it now means "attended at least one day").

Reverse: the old columns (restored by reversing 0013) are filled back from the FIRST day row of each registration
(methods "qr" / "staff" become "wet_signature"), then every day row is deleted and the source goes back to online.
"""

from zoneinfo import ZoneInfo

from django.db import migrations

MANILA = ZoneInfo("Asia/Manila")
OLD_METHODS = {"email", "wet_signature"}


class CopyMismatch(Exception):
    pass


def forwards(apps, schema_editor):
    Registration = apps.get_model("registrations", "Registration")
    AttendanceDay = apps.get_model("registrations", "AttendanceDay")

    portal = Registration.objects.filter(startup__isnull=False).update(registration_source="portal")
    attended = Registration.objects.filter(attendance_confirmed=True).select_related("activity")
    expected = attended.count()
    rows = []
    for registration in attended.iterator():
        activity = registration.activity
        moment = registration.attendance_marked_at or registration.updated_at
        local_day = moment.astimezone(MANILA).date()
        day = local_day if activity.start_date <= local_day <= activity.end_date else activity.start_date
        method = registration.confirmation_method if registration.confirmation_method in OLD_METHODS else "wet_signature"
        rows.append(AttendanceDay(
            registration_id=registration.pk, day=day, checked_in_at=moment, created_at=moment, method=method,
            marked_by_id=registration.attendance_marked_by_id,
        ))
    AttendanceDay.objects.bulk_create(rows)
    copied = AttendanceDay.objects.count()
    print(f"\n  registrations/0012: {portal} registration(s) marked as from the startup portal; "
          f"{expected} attended registration(s), {copied} attendance day row(s) created.")
    if copied != expected:
        raise CopyMismatch(f"Expected {expected} attendance day rows, found {copied}. Nothing was changed.")


def backwards(apps, schema_editor):
    Registration = apps.get_model("registrations", "Registration")
    AttendanceDay = apps.get_model("registrations", "AttendanceDay")
    for registration in Registration.objects.filter(attendance_days__isnull=False).distinct().iterator():
        first = AttendanceDay.objects.filter(registration_id=registration.pk).order_by("day", "checked_in_at").first()
        Registration.objects.filter(pk=registration.pk).update(
            attendance_confirmed=True,
            confirmation_method=first.method if first.method in OLD_METHODS else "wet_signature",
            attendance_marked_at=first.created_at, attendance_marked_by_id=first.marked_by_id,
        )
    AttendanceDay.objects.all().delete()
    Registration.objects.update(registration_source="online")


class Migration(migrations.Migration):
    dependencies = [("registrations", "0011_attendance_days_signatures")]

    operations = [migrations.RunPython(forwards, backwards)]
