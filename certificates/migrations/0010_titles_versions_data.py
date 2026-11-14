"""Titles on the certificate and versions of a person, step 2 of 3: the data (ASSUMPTIONS 365, 367). Prints the counts.

- Every certificate (Issued included): left/right_signatory_title = the position of the signatory row it uses. That is
  exactly the title printed until now (a position edit was always a new row), so no PDF changes and no Issued
  certificate becomes "Issued with an old signature".
- Versions: rows with the same name and position, in the order they were added; a row gets the one before it as its
  previous_version when that one is inactive (it was replaced). Only the link is saved: NO activity is moved and no
  certificate changes (owner decision: staff use "Use the current version" per activity).
- ACTIVITY_SIGNATORY.position_on_certificate stays empty everywhere (the position prints, as before).

Going back: the titles and links are cleared (0009's reverse then drops the columns).
"""

from django.db import migrations
from django.db.models import OuterRef, Subquery


def forwards(apps, schema_editor):
    Signatory = apps.get_model("certificates", "CertificateSignatory")
    Certificate = apps.get_model("certificates", "Certificate")

    titled = Certificate.objects.update(
        left_signatory_title=Subquery(Signatory.objects.filter(pk=OuterRef("left_signatory_id")).values("position")[:1]),
        right_signatory_title=Subquery(Signatory.objects.filter(pk=OuterRef("right_signatory_id")).values("position")[:1]),
    )

    linked = []
    groups: dict[tuple[str, str], list] = {}
    for row in Signatory.objects.order_by("created_at", "pk"):
        groups.setdefault((row.name, row.position), []).append(row)
    for rows in groups.values():
        for earlier, later in zip(rows, rows[1:]):
            if not earlier.is_active and later.previous_version_id is None:
                Signatory.objects.filter(pk=later.pk).update(previous_version=earlier.pk)
                linked.append(f"{later.pk} -> {earlier.pk} ({later.name})")
    print(f"\n    Certificates: {titled} got their printed titles (the position of the signatory they use; no PDF changed)."
          f"\n    Versions linked: {len(linked)}{': ' + ', '.join(linked) if linked else ''} (new -> previous)."
          "\n    Activities: none changed (staff use \"Use the current version\").")


def backwards(apps, schema_editor):
    Signatory = apps.get_model("certificates", "CertificateSignatory")
    Certificate = apps.get_model("certificates", "Certificate")
    Certificate.objects.update(left_signatory_title=None, right_signatory_title=None)
    Signatory.objects.update(previous_version=None)


class Migration(migrations.Migration):
    dependencies = [("certificates", "0009_titles_versions_render_error")]

    operations = [migrations.RunPython(forwards, backwards)]
