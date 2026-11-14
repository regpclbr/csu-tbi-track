"""The certificate wording, step 3 of 3: every certificate has its wording (0013 filled them), so the column becomes
required (ASSUMPTIONS 370). A separate migration: PostgreSQL refuses ALTER TABLE in the transaction that just updated
the rows (pending trigger events)."""

import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("certificates", "0013_certificate_text_data")]

    operations = [
        migrations.AlterField(
            model_name="certificate",
            name="certificate_text",
            field=models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name="certificates",
                                    to="certificates.certificatetext"),
        ),
    ]
