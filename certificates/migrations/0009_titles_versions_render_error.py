"""Titles on the certificate, versions of a person, PDF render errors, step 1 of 3 (ASSUMPTIONS 365-368): new columns.
The printed titles start nullable; 0010 fills them and 0011 makes them required (separate migrations because
PostgreSQL refuses to ALTER a table in the same transaction as row updates with pending deferred foreign-key checks;
same as 0003/0004 and 0006-0008)."""

import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("certificates", "0008_signatory_columns")]

    operations = [
        migrations.AddField(
            model_name="activitysignatory",
            name="position_on_certificate",
            field=models.CharField(blank=True, max_length=255, null=True),
        ),
        migrations.AddField(
            model_name="certificatesignatory",
            name="previous_version",
            field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT,
                                    related_name="next_versions", to="certificates.certificatesignatory"),
        ),
        migrations.AddField(model_name="certificate", name="left_signatory_title",
                            field=models.CharField(max_length=255, null=True)),
        migrations.AddField(model_name="certificate", name="right_signatory_title",
                            field=models.CharField(max_length=255, null=True)),
        migrations.AddField(model_name="certificate", name="render_failed_at",
                            field=models.DateTimeField(blank=True, null=True)),
        migrations.AddField(model_name="certificate", name="render_error",
                            field=models.CharField(blank=True, max_length=255, null=True)),
    ]
