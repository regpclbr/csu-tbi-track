"""Data only (ASSUMPTIONS 386): the Accomplishment Report is no longer a required document (like the Invitation Letter,
migration 0003).

Changes ONE row of DOCUMENT_TYPE, the one named "Accomplishment Report": is_required -> False, due_reference and
due_offset_days -> empty. No document, file, activity or notification is touched: uploaded Accomplishment Reports stay
and are listed under "Other documents". Nothing happens when the row does not exist (a new database: seed_data creates
it as optional). The reverse puts back the old rule (required, 7 days after the end date). No schema change.
"""

from django.db import migrations

NAME = "Accomplishment Report"


def make_optional(apps, schema_editor):
    DocumentType = apps.get_model("documents", "DocumentType")
    DocumentType.objects.filter(name=NAME).update(is_required=False, due_reference=None, due_offset_days=None)


def make_required_again(apps, schema_editor):
    DocumentType = apps.get_model("documents", "DocumentType")
    DocumentType.objects.filter(name=NAME).update(is_required=True, due_reference="end_date", due_offset_days=7)


class Migration(migrations.Migration):
    dependencies = [("documents", "0003_invitation_letter_optional")]

    operations = [migrations.RunPython(make_optional, make_required_again)]
