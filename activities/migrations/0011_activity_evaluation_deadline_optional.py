"""ACTIVITY.evaluation_deadline becomes optional (ASSUMPTIONS 423): empty = the evaluation stays open.
Existing rows keep their deadline (no data change). Reversing needs every row to have a deadline again."""

from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("activities", "0010_activity_certificates_prepared"),
    ]

    operations = [
        migrations.AlterField(
            model_name="activity",
            name="evaluation_deadline",
            field=models.DateTimeField(blank=True, null=True),
        ),
    ]
