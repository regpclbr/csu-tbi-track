"""ERD change (2026-11-06): ACTIVITY.time_schedule (free text) becomes start_time and end_time.

1. Add start_time and end_time (nullable).
2. Fill them by reading each activity's time_schedule (activities.time_parsing.parse_time_schedule).
   An unreadable text leaves both empty; the activity page then asks staff to set the times.
3. Remove time_schedule and add the database rules: both times or neither; a one-day activity ends after it starts.

Reverse: time_schedule comes back, rebuilt from the times ("8:00 AM - 5:00 PM"; empty when no time was set).
"""

from django.db import migrations, models

from activities.time_parsing import parse_time_schedule, time_schedule_text


def fill_times(apps, schema_editor) -> None:
    Activity = apps.get_model("activities", "Activity")
    read = unreadable = 0
    for activity in Activity.objects.only("pk", "time_schedule").order_by("pk"):
        times = parse_time_schedule(activity.time_schedule)
        if times is None:
            unreadable += 1
            continue
        Activity.objects.filter(pk=activity.pk).update(start_time=times[0], end_time=times[1])
        read += 1
    if read or unreadable:
        print(f"\n  Activity times: {read} read from time_schedule, {unreadable} left empty (unreadable).")


def rebuild_text(apps, schema_editor) -> None:
    Activity = apps.get_model("activities", "Activity")
    for activity in Activity.objects.only("pk", "start_time", "end_time").order_by("pk"):
        Activity.objects.filter(pk=activity.pk).update(time_schedule=time_schedule_text(activity.start_time, activity.end_time))


class Migration(migrations.Migration):
    dependencies = [
        ("activities", "0004_activity_participant_categories"),
    ]

    operations = [
        migrations.AddField(model_name="activity", name="start_time", field=models.TimeField(blank=True, null=True)),
        migrations.AddField(model_name="activity", name="end_time", field=models.TimeField(blank=True, null=True)),
        migrations.RunPython(fill_times, rebuild_text),
        migrations.RemoveField(model_name="activity", name="time_schedule"),
        migrations.AddConstraint(
            model_name="activity",
            constraint=models.CheckConstraint(
                condition=models.Q(("end_time__isnull", True), ("start_time__isnull", True))
                | models.Q(("end_time__isnull", False), ("start_time__isnull", False)),
                name="activity_times_both_or_neither",
            ),
        ),
        migrations.AddConstraint(
            model_name="activity",
            constraint=models.CheckConstraint(
                condition=models.Q(
                    ("end_date__gt", models.F("start_date")),
                    ("start_time__isnull", True),
                    ("end_time__gt", models.F("start_time")),
                    _connector="OR",
                ),
                name="activity_one_day_end_time_after_start_time",
            ),
        ),
    ]
