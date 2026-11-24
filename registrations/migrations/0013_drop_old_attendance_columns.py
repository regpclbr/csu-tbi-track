"""Schema, part 3 of 3 (ASSUMPTIONS 434): drop REGISTRATION.confirmation_method, attendance_marked_at and
attendance_marked_by. Their values were copied to ATTENDANCE_DAY by 0012 (methods email / wet_signature kept there).
Reversible: the columns come back empty and reversing 0012 fills them from the day rows.
"""

from django.conf import settings
from django.db import migrations


class Migration(migrations.Migration):
    dependencies = [
        ("registrations", "0012_attendance_days_data"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.RemoveField(model_name="registration", name="confirmation_method"),
        migrations.RemoveField(model_name="registration", name="attendance_marked_at"),
        migrations.RemoveField(model_name="registration", name="attendance_marked_by"),
    ]
