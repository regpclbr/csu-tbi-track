"""ASSUMPTIONS 452: ACTIVITY.evaluation_closed_by_hand ("Close the evaluation" by hand, default False = the old rules).
ASSUMPTIONS 451: the fund source TRF is the "Trust Fund", not "Thrust Fund". Guarded: only a row still named "Thrust
Fund" is renamed, so a name the Admin changed in the Django admin is left alone. History rows keep their old text
(the audit log is append-only)."""

from django.db import migrations, models

OLD, NEW = "Thrust Fund", "Trust Fund"


def rename_trust_fund(apps, schema_editor):
    FundSource = apps.get_model("activities", "FundSource")
    FundSource.objects.filter(code="TRF", name=OLD).update(name=NEW)


def undo_rename(apps, schema_editor):
    FundSource = apps.get_model("activities", "FundSource")
    FundSource.objects.filter(code="TRF", name=NEW).update(name=OLD)


class Migration(migrations.Migration):

    dependencies = [
        ("activities", "0015_audit_log_on_behalf_of"),
    ]

    operations = [
        migrations.AddField(
            model_name="activity",
            name="evaluation_closed_by_hand",
            field=models.BooleanField(default=False),
        ),
        migrations.RunPython(rename_trust_fund, undo_rename),
    ]
