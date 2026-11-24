"""ASSUMPTIONS 432: DOCUMENT_TYPE.needs_review (default False for every type: uploads are approved copies)."""

from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("documents", "0007_accomplishment_report_inactive")]

    operations = [
        migrations.AddField(
            model_name="documenttype",
            name="needs_review",
            field=models.BooleanField(
                default=False,
                help_text="Off: uploads of this type are approved copies (saved as Approved). On: the Director reviews them."),
        ),
    ]
