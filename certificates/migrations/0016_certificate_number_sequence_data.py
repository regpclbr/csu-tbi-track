"""Fills CERTIFICATE_NUMBER_SEQUENCE (ASSUMPTIONS 389) from the existing certificate numbers: one row per year with the
highest sequence used (TBI-<year>-<00001>). No certificate is changed. Going back removes the rows (the table itself is
removed by reversing 0015).
"""

import re

from django.db import migrations

NUMBER = re.compile(r"^TBI-(\d{4})-(\d+)$")


def forwards(apps, schema_editor):
    Certificate = apps.get_model("certificates", "Certificate")
    Sequence = apps.get_model("certificates", "CertificateNumberSequence")
    highest: dict[int, int] = {}
    for number in Certificate.objects.values_list("certificate_number", flat=True):
        match = NUMBER.match(number or "")
        if match:
            year, sequence = int(match.group(1)), int(match.group(2))
            highest[year] = max(highest.get(year, 0), sequence)
    for year, last in highest.items():
        Sequence.objects.update_or_create(year=year, defaults={"last_number": last})
    print(f"\n  Certificate number sequence filled for {len(highest)} year(s): "
          + (", ".join(f"{y} -> {n}" for y, n in sorted(highest.items())) or "no certificates yet") + ".")


def backwards(apps, schema_editor):
    apps.get_model("certificates", "CertificateNumberSequence").objects.all().delete()


class Migration(migrations.Migration):
    dependencies = [("certificates", "0015_certificate_number_sequence")]

    operations = [migrations.RunPython(forwards, backwards)]
