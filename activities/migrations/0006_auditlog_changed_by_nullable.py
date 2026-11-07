"""ERD change (2026-11-06): ACTIVITY_AUDIT_LOG.changed_by may be empty = System (an automatic status change).
No data changes: every existing row keeps its member."""

import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("activities", "0005_activity_start_end_time"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.AlterField(
            model_name="activityauditlog",
            name="changed_by",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.PROTECT,
                related_name="activity_changes",
                to=settings.AUTH_USER_MODEL,
            ),
        ),
    ]
