"""Schema, part 1 of 3 (ASSUMPTIONS 434, NAVIGATU UAT: QR attendance, signatures, walk-ins).

Adds REGISTRATION.registration_source (online / portal / walk_in, default online) and REGISTRATION.cancelled_at
("Will not attend"), and the two new tables PARTICIPANT_SIGNATURE and ATTENDANCE_DAY. Nothing is removed here: the
old attendance columns are copied by 0012 and dropped by 0013.
"""

import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models

import registrations.models


class Migration(migrations.Migration):
    dependencies = [
        ("registrations", "0010_affiliation_to_office_agency"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.AddField(
            model_name="registration",
            name="registration_source",
            field=models.CharField(
                choices=[("online", "Online"), ("portal", "Startup portal"), ("walk_in", "Walk-in")],
                default="online", max_length=20,
            ),
        ),
        migrations.AddField(
            model_name="registration",
            name="cancelled_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.CreateModel(
            name="ParticipantSignature",
            fields=[
                ("signature_id", models.BigAutoField(primary_key=True, serialize=False)),
                ("kind", models.CharField(
                    choices=[("draw", "Drawn on the screen"), ("upload", "Uploaded image"), ("typed", "Typed name")],
                    max_length=10)),
                ("file_path", models.FileField(blank=True, default="", max_length=255,
                                               upload_to=registrations.models.signature_upload_to)),
                ("typed_name", models.CharField(blank=True, max_length=150, null=True)),
                ("signed_at", models.DateTimeField()),
                ("deleted_at", models.DateTimeField(blank=True, null=True)),
                ("registration", models.OneToOneField(
                    on_delete=django.db.models.deletion.CASCADE, related_name="signature",
                    to="registrations.registration")),
            ],
            options={"db_table": "participant_signature"},
        ),
        migrations.AddConstraint(
            model_name="participantsignature",
            constraint=models.CheckConstraint(
                condition=models.Q(("deleted_at__isnull", False))
                | models.Q(("kind", "typed"), ("typed_name__isnull", False))
                | (models.Q(("kind__in", ["draw", "upload"])) & ~models.Q(("file_path", ""))),
                name="participant_signature_has_content",
            ),
        ),
        migrations.CreateModel(
            name="AttendanceDay",
            fields=[
                ("attendance_day_id", models.BigAutoField(primary_key=True, serialize=False)),
                ("day", models.DateField()),
                ("checked_in_at", models.DateTimeField()),
                ("method", models.CharField(
                    choices=[("qr", "QR check-in"), ("staff", "Marked by staff"),
                             ("email", "Email (before November 2026)"),
                             ("wet_signature", "Wet signature (before November 2026)")],
                    max_length=20)),
                ("created_at", models.DateTimeField()),
                ("marked_by", models.ForeignKey(
                    blank=True, null=True, on_delete=django.db.models.deletion.PROTECT,
                    related_name="attendance_days_marked", to=settings.AUTH_USER_MODEL,
                    help_text="The staff member who marked it (method staff); empty for a QR check-in.")),
                ("registration", models.ForeignKey(
                    on_delete=django.db.models.deletion.CASCADE, related_name="attendance_days",
                    to="registrations.registration")),
            ],
            options={"db_table": "attendance_day", "ordering": ["day", "checked_in_at"]},
        ),
        migrations.AddConstraint(
            model_name="attendanceday",
            constraint=models.UniqueConstraint(fields=("registration", "day"), name="one_attendance_per_registration_day"),
        ),
    ]
