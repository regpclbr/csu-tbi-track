"""ASSUMPTIONS 438: DOCUMENT.ai_scan_allowed_by / _at ("Read this scan with AI", one tick per file). Reversible."""

import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('activities', '0015_audit_log_on_behalf_of'),
        ('certificates', '0017_signatory_member'),
        ('documents', '0010_confirmation_of_attendance'),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.AddField(
            model_name='document',
            name='ai_scan_allowed_at',
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name='document',
            name='ai_scan_allowed_by',
            field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT, related_name='+', to=settings.AUTH_USER_MODEL),
        ),
        migrations.AddConstraint(
            model_name='document',
            constraint=models.CheckConstraint(condition=models.Q(models.Q(('ai_scan_allowed_at__isnull', True), ('ai_scan_allowed_by__isnull', True)), models.Q(('ai_scan_allowed_at__isnull', False), ('ai_scan_allowed_by__isnull', False)), _connector='OR'), name='ai_scan_allowed_by_and_at_together'),
        ),
    ]
