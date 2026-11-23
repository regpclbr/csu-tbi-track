"""Data only (ASSUMPTIONS 425, NAVIGATU UAT, Supply Office): the payment documents after the activity.

- Adds two DOCUMENT_TYPE rows, "Activity Report" and "Event Photos" (required, due end_date + 5), ONLY in a database
  that already has document types (a seeded one) and only when the name does not exist yet. A new database gets them
  from seed_data. An existing row of that name is never changed (it was set up in the Django admin).
- Moves the "Attendance Sheet" from end_date + 0 to end_date + 5, ONLY while it still has the old seeded rule exactly
  (end_date / 0); any other value was chosen in the Django admin and is left alone.
No document, activity or notification is touched: due dates are calculated, never stored. No schema change.
Reverse: the Attendance Sheet end_date / 5 -> 0 (same guard); each of the two rows is removed only while no document
uses it (otherwise it is left as it is: DOCUMENT.document_type is PROTECT).
"""

from django.db import migrations

NEW_TYPES = {
    "Activity Report": "Report of the conducted activity, needed by the Supply Office for payment.",
    "Event Photos": "Photos of the activity (JPG or PNG, one or more), needed by the Supply Office for payment.",
}
ATTENDANCE_SHEET = "Attendance Sheet"
OFFSET = 5


def forwards(apps, schema_editor):
    DocumentType = apps.get_model("documents", "DocumentType")
    if DocumentType.objects.exists():
        for name, description in NEW_TYPES.items():
            if not DocumentType.objects.filter(name=name).exists():
                DocumentType.objects.create(
                    name=name, description=description, is_required=True, due_reference="end_date",
                    due_offset_days=OFFSET, is_active=True,
                )
    DocumentType.objects.filter(name=ATTENDANCE_SHEET, due_reference="end_date", due_offset_days=0).update(
        due_offset_days=OFFSET)


def backwards(apps, schema_editor):
    DocumentType = apps.get_model("documents", "DocumentType")
    Document = apps.get_model("documents", "Document")
    DocumentType.objects.filter(name=ATTENDANCE_SHEET, due_reference="end_date", due_offset_days=OFFSET).update(
        due_offset_days=0)
    for document_type in DocumentType.objects.filter(name__in=NEW_TYPES):
        if not Document.objects.filter(document_type=document_type).exists():
            document_type.delete()


class Migration(migrations.Migration):
    dependencies = [("documents", "0005_activity_design_due_five_days")]

    operations = [migrations.RunPython(forwards, backwards)]
