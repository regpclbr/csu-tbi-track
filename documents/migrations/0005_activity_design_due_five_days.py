"""Data only (ASSUMPTIONS 416, NAVIGATU's 5-day rule): the Activity Design is due 5 days before the start date (was 10),
the same as the Request Letter.

Changes ONE row of DOCUMENT_TYPE, the one named "Activity Design", and ONLY while it still has the old seeded rule
exactly (due_reference "start_date", due_offset_days -10): due_offset_days -> -5. Any other value was chosen in the
Django admin (for example the owner's own database, already changed by hand to -5) and is left alone. Nothing happens
when the row does not exist (a new database: seed_data creates it with -5). No document, activity or notification is
touched: due dates are calculated, never stored. The reverse is guarded the same way: start_date / -5 -> -10.
No schema change.
"""

from django.db import migrations

NAME = "Activity Design"
OLD_OFFSET = -10
NEW_OFFSET = -5


def _move(apps, from_offset: int, to_offset: int) -> None:
    DocumentType = apps.get_model("documents", "DocumentType")
    DocumentType.objects.filter(name=NAME, due_reference="start_date", due_offset_days=from_offset).update(
        due_offset_days=to_offset)


def five_days_before(apps, schema_editor):
    _move(apps, OLD_OFFSET, NEW_OFFSET)


def ten_days_before(apps, schema_editor):
    _move(apps, NEW_OFFSET, OLD_OFFSET)


class Migration(migrations.Migration):
    dependencies = [("documents", "0004_accomplishment_report_optional")]

    operations = [migrations.RunPython(five_days_before, ten_days_before)]
