# ASSUMPTIONS 420: "Prepare the certificates now" (who and when). Two nullable columns; existing rows stay empty
# (= not prepared, the flow before item 420). No data step.

import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('activities', '0009_activity_creation_documents_required'),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.AddField(
            model_name='activity',
            name='certificates_prepared_by',
            field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT,
                                    related_name='prepared_activity_certificates', to=settings.AUTH_USER_MODEL),
        ),
        migrations.AddField(
            model_name='activity',
            name='certificates_prepared_at',
            field=models.DateTimeField(blank=True, null=True),
        ),
    ]
