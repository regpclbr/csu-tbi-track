"""ASSUMPTIONS 458: the startup portal is removed. Registrations made through it are KEPT as ordinary online
registrations (name, email, answers, attendance, evaluation and certificate unchanged); only registration_source
"portal" becomes "online" and the link to the STARTUP row goes. Nothing is deleted."""

from django.db import migrations, models


def portal_to_online(apps, schema_editor):
    Registration = apps.get_model("registrations", "Registration")
    changed = Registration.objects.filter(registration_source="portal").update(registration_source="online")
    if changed:
        print(f"\n  {changed} registration(s) made through the startup portal are now online registrations.")


class Migration(migrations.Migration):

    dependencies = [
        ("registrations", "0014_evaluation_attachment"),
        ("startups", "0002_startup_unique_startup_email_ignoring_case"),
    ]

    operations = [
        migrations.RunPython(portal_to_online, migrations.RunPython.noop),
        migrations.RemoveField(model_name="registration", name="startup"),
        migrations.AlterField(
            model_name="registration",
            name="registration_source",
            field=models.CharField(choices=[("online", "Online"), ("walk_in", "Walk-in")], default="online", max_length=20),
        ),
    ]
