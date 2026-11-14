"""Titles on the certificate, step 3 of 3 (ASSUMPTIONS 365): left/right_signatory_title become required. A separate
migration because PostgreSQL refuses to ALTER a table in the same transaction as 0010's row updates."""

from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("certificates", "0010_titles_versions_data")]

    operations = [
        migrations.AlterField(model_name="certificate", name="left_signatory_title", field=models.CharField(max_length=255)),
        migrations.AlterField(model_name="certificate", name="right_signatory_title", field=models.CharField(max_length=255)),
    ]
