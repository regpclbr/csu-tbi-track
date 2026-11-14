"""Different signatories per activity, step 3 of 3 (ASSUMPTIONS 363): left_signatory and right_signatory become
required, vp_signatory and director_signatory are removed. A separate migration because PostgreSQL refuses to ALTER a
table in the same transaction as 0007's row updates (pending deferred foreign-key checks; same as 0003/0004)."""

import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("certificates", "0007_signatory_data")]

    operations = [
        migrations.RemoveField(model_name="certificate", name="vp_signatory"),
        migrations.RemoveField(model_name="certificate", name="director_signatory"),
        migrations.AlterField(
            model_name="certificate",
            name="left_signatory",
            field=models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name="left_signed_certificates",
                                    to="certificates.certificatesignatory"),
        ),
        migrations.AlterField(
            model_name="certificate",
            name="right_signatory",
            field=models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name="right_signed_certificates",
                                    to="certificates.certificatesignatory"),
        ),
    ]
