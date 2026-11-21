"""ROLE.can_manage_signatories (ASSUMPTIONS 418): a new flag, False for every existing role (db_default). The data step
in 0010 turns it on for the role named "Director"."""

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("accounts", "0008_delete_activities_flag_data"),
    ]

    operations = [
        migrations.AddField(
            model_name="role",
            name="can_manage_signatories",
            field=models.BooleanField(db_default=False, default=False),
        ),
    ]
