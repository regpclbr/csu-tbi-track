"""Different signatories per activity, step 1 of 3: the schema (ASSUMPTIONS 363, 364).

ERD changes:
- CERTIFICATE_SIGNATORY becomes a list of people: `position` is free text (max 255, e.g. "University President"),
  the "one active per position" rule is removed, new `default_slot` (left / right, nullable, at most one row per slot:
  who a new activity starts with).
- New table ACTIVITY_SIGNATORY (activity, signatory, slot left / right; one signatory per slot per activity; the same
  person cannot fill both slots).
- CERTIFICATE: new `left_signatory` and `right_signatory` (filled by 0007, made required by 0008, which also removes
  `vp_signatory` and `director_signatory`); new `reissued_at` (datetime, nullable).

`vp_signatory` / `director_signatory` become nullable here (not in 0008) so the three migrations can be reversed:
going back fills them again in 0007's reverse before this one makes them required again.
"""

import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("activities", "0006_auditlog_changed_by_nullable"),
        ("certificates", "0005_signature_image_optional"),
    ]

    operations = [
        migrations.RemoveConstraint(model_name="certificatesignatory", name="one_active_signatory_per_position"),
        migrations.AlterField(
            model_name="certificatesignatory",
            name="position",
            field=models.CharField(max_length=255),
        ),
        migrations.AddField(
            model_name="certificatesignatory",
            name="default_slot",
            field=models.CharField(blank=True, choices=[("left", "Left"), ("right", "Right")], max_length=10, null=True),
        ),
        migrations.AddConstraint(
            model_name="certificatesignatory",
            constraint=models.UniqueConstraint(
                condition=models.Q(default_slot__isnull=False), fields=("default_slot",), name="one_default_signatory_per_slot"
            ),
        ),
        migrations.AddConstraint(
            model_name="certificatesignatory",
            constraint=models.CheckConstraint(
                condition=models.Q(default_slot__isnull=True) | models.Q(default_slot__in=["left", "right"]),
                name="signatory_default_slot_valid",
            ),
        ),
        migrations.CreateModel(
            name="ActivitySignatory",
            fields=[
                ("activity_signatory_id", models.BigAutoField(primary_key=True, serialize=False)),
                ("slot", models.CharField(choices=[("left", "Left"), ("right", "Right")], max_length=10)),
                ("activity", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="signatory_slots",
                                               to="activities.activity")),
                ("signatory", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name="activity_slots",
                                                to="certificates.certificatesignatory")),
            ],
            options={
                "db_table": "activity_signatory",
                "verbose_name_plural": "activity signatories",
                "constraints": [
                    models.UniqueConstraint(fields=("activity", "slot"), name="one_signatory_per_activity_slot"),
                    models.UniqueConstraint(fields=("activity", "signatory"), name="signatory_once_per_activity"),
                    models.CheckConstraint(condition=models.Q(slot__in=["left", "right"]), name="activity_signatory_slot_valid"),
                ],
            },
        ),
        migrations.AddField(
            model_name="certificate",
            name="left_signatory",
            field=models.ForeignKey(null=True, on_delete=django.db.models.deletion.PROTECT,
                                    related_name="left_signed_certificates", to="certificates.certificatesignatory"),
        ),
        migrations.AddField(
            model_name="certificate",
            name="right_signatory",
            field=models.ForeignKey(null=True, on_delete=django.db.models.deletion.PROTECT,
                                    related_name="right_signed_certificates", to="certificates.certificatesignatory"),
        ),
        migrations.AlterField(
            model_name="certificate",
            name="vp_signatory",
            field=models.ForeignKey(null=True, on_delete=django.db.models.deletion.PROTECT,
                                    related_name="vp_signed_certificates", to="certificates.certificatesignatory"),
        ),
        migrations.AlterField(
            model_name="certificate",
            name="director_signatory",
            field=models.ForeignKey(null=True, on_delete=django.db.models.deletion.PROTECT,
                                    related_name="director_signed_certificates", to="certificates.certificatesignatory"),
        ),
        migrations.AddField(
            model_name="certificate",
            name="reissued_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
    ]
