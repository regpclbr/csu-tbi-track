"""REGISTRATION.participant_category becomes required (NOT NULL), as in the ERD.

First checks that every registration already has a category. If any does not, the
migration stops with a clear message and changes nothing (set those categories first,
e.g. in the Django shell). The development database had 0 registrations.
"""

import django.db.models.deletion
from django.db import migrations, models


def check_no_empty_categories(apps, schema_editor) -> None:
    Registration = apps.get_model("registrations", "Registration")
    empty = Registration.objects.filter(participant_category__isnull=True).count()
    total = Registration.objects.count()
    print(f"\n    Registrations: {total}. Without a category: {empty}.")
    if empty:
        raise RuntimeError(
            f"{empty} registration(s) have no participant category. Give each one a category "
            "before running this migration; nothing was changed."
        )


class Migration(migrations.Migration):
    dependencies = [
        ("activities", "0004_activity_participant_categories"),
        ("registrations", "0002_step5_categories_forms_json_options"),
    ]

    operations = [
        migrations.RunPython(check_no_empty_categories, migrations.RunPython.noop),
        migrations.AlterField(
            model_name="registration",
            name="participant_category",
            field=models.ForeignKey(
                on_delete=django.db.models.deletion.PROTECT,
                related_name="registrations",
                to="activities.participantcategory",
            ),
        ),
    ]
