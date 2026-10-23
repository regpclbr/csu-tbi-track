"""Step 9: certificate_name and certificate_email become required (NOT NULL), as in the ERD.

0004 filled every existing evaluation. This checks again that none is empty; if one is, it stops
with a clear message and changes nothing.
"""

from django.db import migrations, models
from django.db.models import Q


def check_all_filled(apps, schema_editor) -> None:
    EvaluationSubmission = apps.get_model("registrations", "EvaluationSubmission")
    empty = EvaluationSubmission.objects.filter(Q(certificate_name__isnull=True) | Q(certificate_email__isnull=True)).count()
    print(f"\n    Evaluation submissions without a certificate name or email: {empty}.")
    if empty:
        raise RuntimeError(f"{empty} evaluation submission(s) have no certificate name or email; nothing was changed.")


class Migration(migrations.Migration):
    dependencies = [("registrations", "0004_evaluation_certificate_name_email")]

    operations = [
        migrations.RunPython(check_all_filled, migrations.RunPython.noop),
        migrations.AlterField(
            model_name="evaluationsubmission",
            name="certificate_name",
            field=models.CharField(max_length=150),
        ),
        migrations.AlterField(
            model_name="evaluationsubmission",
            name="certificate_email",
            field=models.EmailField(max_length=254),
        ),
    ]
