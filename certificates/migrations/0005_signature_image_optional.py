"""CERTIFICATE_SIGNATORY.signature_image_path becomes nullable (ASSUMPTIONS 361): a signatory can be added by name and
the signature image added later, on the activity's Certificates tab or in the Admin Console. Certificates cannot be
approved while one of their signatories has no image. Going back fails if a signatory still has no image."""

import certificates.models
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("certificates", "0004_director_approval_columns")]

    operations = [
        migrations.AlterField(
            model_name="certificatesignatory",
            name="signature_image_path",
            field=models.ImageField(blank=True, max_length=255, null=True, upload_to=certificates.models.signature_upload_to),
        ),
    ]
