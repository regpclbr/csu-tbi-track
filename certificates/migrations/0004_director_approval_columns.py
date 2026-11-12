"""Certificates approved by the Director, part 2 (ASSUMPTIONS 357): rename reviewed_by to approved_by, remove
reviewed_at, director_signed_at and vp_signed_at, and keep only the statuses Generated / Approved / Issued.
The data was moved in 0003 (a separate transaction, see there)."""

from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("certificates", "0003_director_approval"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.RenameField(model_name="certificate", old_name="reviewed_by", new_name="approved_by"),
        migrations.AlterField(
            model_name="certificate",
            name="approved_by",
            field=models.ForeignKey(
                blank=True, null=True, on_delete=models.deletion.PROTECT,
                related_name="approved_certificates", to=settings.AUTH_USER_MODEL,
            ),
        ),
        migrations.RemoveField(model_name="certificate", name="reviewed_at"),
        migrations.RemoveField(model_name="certificate", name="director_signed_at"),
        migrations.RemoveField(model_name="certificate", name="vp_signed_at"),
        migrations.AlterField(
            model_name="certificate",
            name="status",
            field=models.CharField(
                choices=[("generated", "Waiting for approval"), ("approved", "Approved"), ("issued", "Issued")],
                default="generated", max_length=20,
            ),
        ),
    ]
