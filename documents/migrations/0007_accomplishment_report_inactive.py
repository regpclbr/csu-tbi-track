"""Data only (ASSUMPTIONS 430, NAVIGATU UAT round 2): the per-activity "Accomplishment Report" document type leaves the
activity checklist. The Accomplishment Report is now the quarterly / semi-annual / annual report made in Reports (not a
DOCUMENT_TYPE); the per-activity report is the "Activity Report" (a payment document, item 425).

Changes ONE row of DOCUMENT_TYPE, the one named "Accomplishment Report": is_active -> False, ONLY while it is still
exactly as migration 0004 left it (active, optional, no due rule). A row the Admin changed in the Django admin (made
required again or given a due rule) is never touched. Nothing happens when the row does not exist.
No document, file, activity or notification is touched: uploaded Accomplishment Reports stay, listed under "Other
documents" and downloadable (documents.services.document_checklist lists inactive types that have documents).
Reverse: the same row, only while it is inactive, optional and without a due rule, becomes active again.
"""

from django.db import migrations

NAME = "Accomplishment Report"
AS_LEFT_BY_0004 = {"name": NAME, "is_required": False, "due_reference": None, "due_offset_days": None}


def deactivate(apps, schema_editor):
    DocumentType = apps.get_model("documents", "DocumentType")
    DocumentType.objects.filter(is_active=True, **AS_LEFT_BY_0004).update(is_active=False)


def reactivate(apps, schema_editor):
    DocumentType = apps.get_model("documents", "DocumentType")
    DocumentType.objects.filter(is_active=False, **AS_LEFT_BY_0004).update(is_active=True)


class Migration(migrations.Migration):
    dependencies = [("documents", "0006_payment_documents")]

    operations = [migrations.RunPython(deactivate, reactivate)]
