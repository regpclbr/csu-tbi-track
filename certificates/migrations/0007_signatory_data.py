"""Different signatories per activity, step 2 of 3: the data (ASSUMPTIONS 363). Prints the counts.

- Positions: "vp" -> "Vice President", "director" -> "Director" (exactly the labels printed until now).
- Defaults: the active Director -> left, the active VP -> right (the order printed until now: Director on the left).
- Certificates (every status, Issued included; only columns change, no PDF file is touched):
  left_signatory = director_signatory, right_signatory = vp_signatory.
- Activities: each slot gets the signatory its own certificates use (the newest one of the certificates that are not
  Issued, else the newest of the Issued ones), so Issued certificates are not "outdated" right after this migration,
  except those printed before a signature of their activity was replaced (they really have an old signature);
  activities without certificates get the active Director (left) and VP (right).

Going back: the reverse (vp/director filled from right/left, positions back to vp/director). It refuses when a position
is no longer one of the two original labels (people added after this migration cannot become VP or Director rows).
"""

from django.db import migrations
from django.db.models import F, Max

POSITIONS = {"vp": "Vice President", "director": "Director"}


def forwards(apps, schema_editor):
    Signatory = apps.get_model("certificates", "CertificateSignatory")
    Certificate = apps.get_model("certificates", "Certificate")
    ActivitySignatory = apps.get_model("certificates", "ActivitySignatory")
    Activity = apps.get_model("activities", "Activity")

    for code, label in POSITIONS.items():
        Signatory.objects.filter(position=code).update(position=label)
    active_left = Signatory.objects.filter(position="Director", is_active=True).order_by("-pk").first()
    active_right = Signatory.objects.filter(position="Vice President", is_active=True).order_by("-pk").first()
    for row, slot in ((active_left, "left"), (active_right, "right")):
        if row is not None:
            Signatory.objects.filter(pk=row.pk).update(default_slot=slot)

    certificates = Certificate.objects.update(left_signatory=F("director_signatory"), right_signatory=F("vp_signatory"))

    def newest(queryset):
        return {row["activity_id"]: (row["left"], row["right"])
                for row in queryset.values("activity_id").order_by().annotate(left=Max("left_signatory"), right=Max("right_signatory"))}

    pending = newest(Certificate.objects.exclude(status="issued"))
    issued = newest(Certificate.objects.filter(status="issued"))
    rows, from_certificates, from_defaults = [], 0, 0
    for activity_id in Activity.objects.order_by("pk").values_list("pk", flat=True):
        pair = pending.get(activity_id) or issued.get(activity_id)
        if pair:
            from_certificates += 1
        else:
            pair = (active_left.pk if active_left else None, active_right.pk if active_right else None)
            from_defaults += 1
        left, right = pair
        if left is not None:
            rows.append(ActivitySignatory(activity_id=activity_id, slot="left", signatory_id=left))
        if right is not None and right != left:
            rows.append(ActivitySignatory(activity_id=activity_id, slot="right", signatory_id=right))
    ActivitySignatory.objects.bulk_create(rows)
    print(f"\n    Signatories: {Signatory.objects.count()} people (positions now text); defaults: "
          f"left = {active_left.name if active_left else 'none'}, right = {active_right.name if active_right else 'none'}."
          f"\n    Certificates: {certificates} mapped (left = Director, right = VP; no PDF changed)."
          f"\n    Activities: {from_certificates} got the signatories their certificates use, {from_defaults} got the "
          f"defaults ({len(rows)} slots saved).")


def backwards(apps, schema_editor):
    Signatory = apps.get_model("certificates", "CertificateSignatory")
    Certificate = apps.get_model("certificates", "Certificate")
    ActivitySignatory = apps.get_model("certificates", "ActivitySignatory")

    labels = {label: code for code, label in POSITIONS.items()}
    unknown = Signatory.objects.exclude(position__in=list(labels)).values_list("position", flat=True).distinct()
    if unknown:
        raise RuntimeError(f"Cannot go back: these positions are not VP or Director: {', '.join(unknown)}.")
    Certificate.objects.update(director_signatory=F("left_signatory"), vp_signatory=F("right_signatory"))
    for label, code in labels.items():
        Signatory.objects.filter(position=label).update(position=code)
    Signatory.objects.update(default_slot=None)
    ActivitySignatory.objects.all().delete()


class Migration(migrations.Migration):
    dependencies = [("certificates", "0006_signatory_list")]

    operations = [migrations.RunPython(forwards, backwards)]
