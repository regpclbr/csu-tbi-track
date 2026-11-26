"""ASSUMPTIONS 458: the startup portal, the Navigatu sync and the STARTUP table are removed (a Chapter 5
recommendation). Runs after registrations/0015 and notifications/0005 removed the columns pointing here. Also removes
the daily sync schedule row of Django-Q2 if it exists (setup_schedules no longer creates it)."""

from django.db import migrations


def remove_sync_schedule(apps, schema_editor):
    try:
        Schedule = apps.get_model("django_q", "Schedule")
    except LookupError:
        return
    Schedule.objects.filter(func="startups.tasks.run_startup_sync").delete()


class Migration(migrations.Migration):

    dependencies = [
        ("startups", "0002_startup_unique_startup_email_ignoring_case"),
        ("registrations", "0015_remove_startup_portal"),
        ("notifications", "0005_remove_startup_recipient"),
        ("django_q", "__latest__"),
    ]

    operations = [
        migrations.RunPython(remove_sync_schedule, migrations.RunPython.noop),
        migrations.DeleteModel(name="Startup"),
    ]
