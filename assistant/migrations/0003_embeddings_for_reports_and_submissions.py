"""ASSUMPTIONS 438: the assistant also reads generated reports and approved submissions. DOCUMENT_EMBEDDING and
AI_MESSAGE_SOURCE point to exactly one of DOCUMENT, GENERATED_REPORT, SUBMISSION (document becomes nullable).
Existing rows all have a document, so nothing changes for them. Reversible: going back first deletes the chunks and
citations of reports and submissions (document becomes required again)."""

import django.db.models.deletion
from django.db import migrations, models


def delete_non_document_rows(apps, schema_editor):
    """Reverse only: rows without a document cannot exist in the older schema."""
    apps.get_model("assistant", "AIMessageSource").objects.filter(document__isnull=True).delete()
    apps.get_model("assistant", "DocumentEmbedding").objects.filter(document__isnull=True).delete()


class Migration(migrations.Migration):

    dependencies = [
        ('approvals', '0002_work_assignments'),
        ('assistant', '0002_initial'),
        ('documents', '0010_confirmation_of_attendance'),
        ('reports', '0001_initial'),
    ]

    operations = [
        migrations.AlterModelOptions(
            name='documentembedding',
            options={'ordering': ['document', 'generated_report', 'submission', 'chunk_index']},
        ),
        migrations.RemoveConstraint(
            model_name='documentembedding',
            name='unique_chunk_per_document',
        ),
        migrations.AddField(
            model_name='aimessagesource',
            name='generated_report',
            field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT, related_name='ai_citations', to='reports.generatedreport'),
        ),
        migrations.AddField(
            model_name='aimessagesource',
            name='submission',
            field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT, related_name='ai_citations', to='approvals.submission'),
        ),
        migrations.AddField(
            model_name='documentembedding',
            name='generated_report',
            field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.CASCADE, related_name='embeddings', to='reports.generatedreport'),
        ),
        migrations.AddField(
            model_name='documentembedding',
            name='submission',
            field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.CASCADE, related_name='embeddings', to='approvals.submission'),
        ),
        migrations.AlterField(
            model_name='aimessagesource',
            name='document',
            field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT, related_name='ai_citations', to='documents.document'),
        ),
        migrations.AlterField(
            model_name='documentembedding',
            name='document',
            field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.CASCADE, related_name='embeddings', to='documents.document'),
        ),
        migrations.AddConstraint(
            model_name='aimessagesource',
            constraint=models.CheckConstraint(condition=models.Q(models.Q(('document__isnull', False), ('generated_report__isnull', True), ('submission__isnull', True)), models.Q(('document__isnull', True), ('generated_report__isnull', False), ('submission__isnull', True)), models.Q(('document__isnull', True), ('generated_report__isnull', True), ('submission__isnull', False)), _connector='OR'), name='source_exactly_one_file'),
        ),
        migrations.AddConstraint(
            model_name='documentembedding',
            constraint=models.UniqueConstraint(condition=models.Q(('document__isnull', False)), fields=('document', 'chunk_index'), name='unique_chunk_per_document'),
        ),
        migrations.AddConstraint(
            model_name='documentembedding',
            constraint=models.UniqueConstraint(condition=models.Q(('generated_report__isnull', False)), fields=('generated_report', 'chunk_index'), name='unique_chunk_per_report'),
        ),
        migrations.AddConstraint(
            model_name='documentembedding',
            constraint=models.UniqueConstraint(condition=models.Q(('submission__isnull', False)), fields=('submission', 'chunk_index'), name='unique_chunk_per_submission'),
        ),
        migrations.AddConstraint(
            model_name='documentembedding',
            constraint=models.CheckConstraint(condition=models.Q(models.Q(('document__isnull', False), ('generated_report__isnull', True), ('submission__isnull', True)), models.Q(('document__isnull', True), ('generated_report__isnull', False), ('submission__isnull', True)), models.Q(('document__isnull', True), ('generated_report__isnull', True), ('submission__isnull', False)), _connector='OR'), name='embedding_exactly_one_source'),
        ),
        migrations.RunPython(migrations.RunPython.noop, delete_non_document_rows),
    ]
