"""Step 5: REGISTRATION.participant_category, one form per type per activity, emails unique
ignoring case and outer spaces, and form options stored as a JSON list.

Data steps (both print what they did):
- fill registration.participant_category where the activity has exactly one category;
- convert old "one option per line" text into a JSON list.
"""

import json

import django.db.models.deletion
import django.db.models.functions.text
from django.conf import settings
from django.db import migrations, models


def fill_registration_categories(apps, schema_editor) -> None:
    Registration = apps.get_model("registrations", "Registration")
    Link = apps.get_model("activities", "ActivityParticipantCategory")
    total = filled = 0
    for registration in Registration.objects.filter(participant_category__isnull=True):
        total += 1
        category_ids = list(
            Link.objects.filter(activity_id=registration.activity_id).values_list("participant_category_id", flat=True)
        )
        if len(category_ids) == 1:
            registration.participant_category_id = category_ids[0]
            registration.save(update_fields=["participant_category"])
            filled += 1
    print(
        f"\n    Registrations: {total}. Category filled: {filled}. "
        f"Left empty (activity has several categories): {total - filled}. "
        "Any left empty must get a category before migration 0003 makes the column required."
    )


def options_to_json(apps, schema_editor) -> None:
    converted = 0
    for model_name in ("FormField", "DefaultFormField"):
        Model = apps.get_model("registrations", model_name)
        for row in Model.objects.exclude(options=""):
            if row.options.lstrip().startswith("["):
                continue  # already JSON
            lines = [line.strip() for line in row.options.splitlines() if line.strip()]
            row.options = json.dumps(lines, ensure_ascii=False)
            row.save(update_fields=["options"])
            converted += 1
    print(f"    Form field options converted to JSON lists: {converted}.")


class Migration(migrations.Migration):

    dependencies = [
        ("activities", "0004_activity_participant_categories"),
        ("registrations", "0001_initial"),
        ("startups", "0001_initial"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.RemoveConstraint(
            model_name="registration",
            name="unique_registration_per_activity_email",
        ),
        migrations.AddField(
            model_name="registration",
            name="participant_category",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.PROTECT,
                related_name="registrations",
                to="activities.participantcategory",
            ),
        ),
        migrations.RunPython(fill_registration_categories, migrations.RunPython.noop),
        migrations.AlterField(
            model_name="defaultformfield",
            name="options",
            field=models.TextField(
                blank=True,
                help_text='Choices for multiple choice/checkbox as a JSON list, e.g. ["Yes", "No"].',
            ),
        ),
        migrations.AlterField(
            model_name="formfield",
            name="options",
            field=models.TextField(
                blank=True,
                help_text='Choices for multiple choice/checkbox as a JSON list, e.g. ["Yes", "No"].',
            ),
        ),
        migrations.RunPython(options_to_json, migrations.RunPython.noop),
        migrations.AddConstraint(
            model_name="formtemplate",
            constraint=models.UniqueConstraint(
                fields=("activity", "form_type"), name="one_form_per_type_per_activity"
            ),
        ),
        migrations.AddConstraint(
            model_name="registration",
            constraint=models.UniqueConstraint(
                django.db.models.functions.text.Lower(
                    django.db.models.functions.text.Trim("email")
                ),
                models.F("activity"),
                name="unique_registration_per_activity_email",
            ),
        ),
    ]
