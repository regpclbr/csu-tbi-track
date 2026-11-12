"""Certificates are approved by the Director, once per activity (ASSUMPTIONS 357).

ERD change on CERTIFICATE:
- status: Generated / Approved / Issued (Pending Sign-off is gone; Generated is shown as "Waiting for approval");
- reviewed_by is renamed approved_by, and approved_at is added (who approved it and when);
- reviewed_at, director_signed_at and vp_signed_at are removed (the VP's signature is an image on the certificate,
  the Director approves in the system);
- send_failed_at is added (the last email attempt failed; cleared when it is sent).

Data (printed counts):
- Pending Sign-off certificates go back to Generated: they wait for the Director's approval.
- Generated certificates lose reviewed_by (it now means "approved by").
- Approved and Issued certificates stay as they are (approved under the old rule): approved_by keeps reviewed_by and
  approved_at is the latest of the two sign-offs, else the review time, else the issue time.
Going back puts approved_at into reviewed_at and both sign-off columns; certificates moved back from Pending
Sign-off stay Generated.

This migration adds the two columns and changes the data; 0004 renames and removes columns. They are two migrations
because PostgreSQL refuses to ALTER a table in the same transaction as row updates that still have deferred
foreign-key checks pending.
"""

from django.conf import settings
from django.db import migrations, models
from django.db.models import F
from django.db.models.functions import Coalesce, Greatest


def forwards(apps, schema_editor):
    Certificate = apps.get_model("certificates", "Certificate")
    moved = Certificate.objects.filter(status="pending_signoff").update(status="generated", reviewed_at=None,
                                                                         director_signed_at=None, vp_signed_at=None)
    Certificate.objects.filter(status="generated").update(reviewed_by=None)
    approved = Certificate.objects.filter(status__in=["approved", "issued"]).update(
        approved_at=Coalesce(Greatest("director_signed_at", "vp_signed_at"), "reviewed_at", "issued_at"),
    )
    print(f"\n    Certificates: {moved} moved from Pending Sign-off to Generated (waiting for approval); "
          f"{approved} Approved or Issued kept, with approved_at filled.")


def backwards(apps, schema_editor):
    Certificate = apps.get_model("certificates", "Certificate")
    Certificate.objects.filter(status__in=["approved", "issued"]).update(
        reviewed_at=F("approved_at"), director_signed_at=F("approved_at"), vp_signed_at=F("approved_at"),
    )


class Migration(migrations.Migration):
    dependencies = [
        ("certificates", "0002_step9_file_names_resource_person_constraint"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.AddField(
            model_name="certificate",
            name="approved_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="certificate",
            name="send_failed_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.RunPython(forwards, backwards),
    ]
