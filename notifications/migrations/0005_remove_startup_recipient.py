"""ASSUMPTIONS 458: the startup portal is removed. Notifications addressed to a startup have no recipient any more and
are deleted (they were only shown in the portal); every member notification is kept. NOTIFICATION.member becomes
required and the "exactly one recipient" check goes with NOTIFICATION.startup_id."""

from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


def delete_startup_notifications(apps, schema_editor):
    Notification = apps.get_model("notifications", "Notification")
    deleted, _ = Notification.objects.filter(member__isnull=True).delete()
    if deleted:
        print(f"\n  {deleted} notification(s) addressed to startups were deleted (the portal is removed).")


class Migration(migrations.Migration):

    dependencies = [
        ("notifications", "0004_notification_work_assignment"),
        ("startups", "0002_startup_unique_startup_email_ignoring_case"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.RunPython(delete_startup_notifications, migrations.RunPython.noop),
        migrations.RemoveConstraint(model_name="notification", name="notification_has_exactly_one_recipient"),
        migrations.RemoveField(model_name="notification", name="startup"),
        migrations.AlterField(
            model_name="notification",
            name="member",
            field=models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="notifications",
                                    to=settings.AUTH_USER_MODEL),
        ),
    ]
