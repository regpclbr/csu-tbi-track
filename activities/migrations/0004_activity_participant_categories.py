"""ERD change: an activity can be for several participant categories.

1. Create ACTIVITY_PARTICIPANT_CATEGORY.
2. Copy each activity's current category into it (no data is lost), printing the counts.
3. Drop ACTIVITY.participant_category_id.

Going back only works on an empty database (used by the migration test): with real
activities, re-adding the required old column fails and the whole migration rolls back,
so no data is lost. Going back with data would have to pick one category per activity.
"""

import django.db.models.deletion
from django.db import migrations, models


def copy_categories(apps, schema_editor) -> None:
    Activity = apps.get_model("activities", "Activity")
    Link = apps.get_model("activities", "ActivityParticipantCategory")
    activities = Activity.objects.all()
    before = activities.count()
    Link.objects.bulk_create(
        Link(activity_id=activity_id, participant_category_id=category_id)
        for activity_id, category_id in activities.values_list("pk", "participant_category_id")
    )
    after = Link.objects.count()
    print(f"\n    Activities: {before}. Category links created: {after} (one per activity).")
    if after != before:
        raise RuntimeError(f"Expected {before} category links, got {after}. Nothing was changed.")


class Migration(migrations.Migration):
    dependencies = [
        ("activities", "0003_alter_participantcategory_options_and_more"),
    ]

    operations = [
        migrations.CreateModel(
            name="ActivityParticipantCategory",
            fields=[
                ("activity_participant_category_id", models.BigAutoField(primary_key=True, serialize=False)),
                (
                    "activity",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="category_links",
                        to="activities.activity",
                    ),
                ),
                (
                    "participant_category",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="activity_links",
                        to="activities.participantcategory",
                    ),
                ),
            ],
            options={
                "db_table": "activity_participant_category",
                "constraints": [
                    models.UniqueConstraint(
                        fields=("activity", "participant_category"), name="unique_category_per_activity"
                    )
                ],
            },
        ),
        migrations.RunPython(copy_categories, migrations.RunPython.noop),
        migrations.RemoveField(model_name="activity", name="participant_category"),
        migrations.AddField(
            model_name="activity",
            name="participant_categories",
            field=models.ManyToManyField(
                related_name="activities",
                through="activities.ActivityParticipantCategory",
                to="activities.participantcategory",
            ),
        ),
    ]
