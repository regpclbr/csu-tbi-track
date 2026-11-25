"""ASSUMPTIONS 435 (NAVIGATU UAT): the new table EVALUATION_ATTACHMENT, participant photos (JPG / PNG) and work
outputs (PDF) uploaded with the evaluation. Schema only, no data step; reversible (drops the table; the stored files
under MEDIA_ROOT/evaluation-photos/ are not touched by the migration).
"""

import django.db.models.deletion
import registrations.models
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('registrations', '0013_drop_old_attendance_columns'),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name='EvaluationAttachment',
            fields=[
                ('attachment_id', models.BigAutoField(primary_key=True, serialize=False)),
                ('file_path', models.FileField(max_length=255, upload_to=registrations.models.evaluation_attachment_upload_to)),
                ('file_name', models.CharField(max_length=255)),
                ('file_size_kb', models.PositiveIntegerField()),
                ('content_type', models.CharField(choices=[('image/jpeg', 'JPG photo'), ('image/png', 'PNG picture'), ('application/pdf', 'PDF')], max_length=50)),
                ('use_in_reports', models.BooleanField(default=False)),
                ('marked_at', models.DateTimeField(blank=True, null=True)),
                ('uploaded_at', models.DateTimeField()),
                ('marked_by', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT, related_name='marked_evaluation_attachments', to=settings.AUTH_USER_MODEL)),
                ('submission', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='attachments', to='registrations.evaluationsubmission')),
            ],
            options={
                'db_table': 'evaluation_attachment',
                'ordering': ['attachment_id'],
                'constraints': [models.CheckConstraint(condition=models.Q(('use_in_reports', False), ('marked_at__isnull', False), _connector='OR'), name='evaluation_attachment_marked_when_used')],
            },
        ),
    ]
