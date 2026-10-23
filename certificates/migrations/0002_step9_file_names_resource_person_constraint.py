"""Step 9: stored file names for templates and signatures (no database change: upload_to only),
and one Certificate of Appreciation per resource person per activity.

Before adding the constraint it checks for duplicates; if any exist it stops with their IDs and
changes nothing. The development database had 0 certificates.
"""

from django.db import migrations, models
from django.db.models import Count, Q

import certificates.models


def check_no_duplicates(apps, schema_editor) -> None:
    Certificate = apps.get_model("certificates", "Certificate")
    duplicates = list(
        Certificate.objects.filter(resource_person__isnull=False)
        .values("activity_id", "resource_person_id")
        .annotate(n=Count("pk"))
        .filter(n__gt=1)
    )
    print(f"\n    Certificates: {Certificate.objects.count()}. Duplicate resource-person certificates: {len(duplicates)}.")
    if duplicates:
        pairs = ", ".join(f"activity {d['activity_id']} / resource person {d['resource_person_id']}" for d in duplicates)
        raise RuntimeError(f"Duplicate Certificates of Appreciation ({pairs}). Nothing was changed.")


class Migration(migrations.Migration):
    dependencies = [
        ("activities", "0004_activity_participant_categories"),
        ("certificates", "0001_initial"),
        ("registrations", "0005_evaluation_certificate_fields_required"),
    ]

    operations = [
        migrations.AlterField(
            model_name="certificatetemplate",
            name="file_path",
            field=models.FileField(max_length=255, upload_to=certificates.models.template_upload_to),
        ),
        migrations.AlterField(
            model_name="certificatesignatory",
            name="signature_image_path",
            field=models.ImageField(max_length=255, upload_to=certificates.models.signature_upload_to),
        ),
        migrations.RunPython(check_no_duplicates, migrations.RunPython.noop),
        migrations.AddConstraint(
            model_name="certificate",
            constraint=models.UniqueConstraint(
                condition=Q(resource_person__isnull=False),
                fields=("activity", "resource_person"),
                name="one_certificate_per_resource_person_per_activity",
            ),
        ),
    ]
