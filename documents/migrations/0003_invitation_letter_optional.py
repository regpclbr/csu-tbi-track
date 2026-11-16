"""Data only (ASSUMPTIONS 386): the Invitation Letter is no longer a required document.

Changes ONE row of DOCUMENT_TYPE, the one named "Invitation Letter": is_required -> False, due_reference and
due_offset_days -> empty (like the other optional types). No document, file, activity or notification is touched:
uploaded Invitation Letters stay and are listed under "Other documents". Nothing happens when the row does not exist
(a new database: seed_data creates it as optional). The reverse puts back the old rule (required, 7 days before the
start date). No schema change.
"""

from django.db import migrations

NAME = "Invitation Letter"


def make_optional(apps, schema_editor):
    DocumentType = apps.get_model("documents", "DocumentType")
    DocumentType.objects.filter(name=NAME).update(is_required=False, due_reference=None, due_offset_days=None)


def make_required_again(apps, schema_editor):
    DocumentType = apps.get_model("documents", "DocumentType")
    DocumentType.objects.filter(name=NAME).update(is_required=True, due_reference="start_date", due_offset_days=-7)


class Migration(migrations.Migration):
    dependencies = [("documents", "0002_alter_document_file_path")]

    operations = [migrations.RunPython(make_optional, make_required_again)]
