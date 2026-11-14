"""The certificate wording, step 2 of 3: the data (ASSUMPTIONS 370). Prints the counts.

- Creates the two DEFAULT wordings (Certificate of Participation, Certificate of Appreciation) by the System, with
  exactly the text the template printed until now (the optional " at <venue>" and " on "<topic>"" parts are written in
  [ ... ]), so no certificate changes.
- Every existing certificate (Issued included) gets the default of its type: that is the text it was printed with.
- No activity gets custom text and NO activity is marked approved (owner decision, ASSUMPTIONS 369).

The wording is copied here on purpose (a migration never imports app code that may change later).
Going back: the links are cleared and the rows deleted (0012's reverse then drops the table and the column).
"""

from django.db import migrations

DEFAULTS = {
    "participation": {
        "title": "Certificate of Participation",
        "line_before_name": "is presented to",
        "main_sentence": "for participating in “{activity}” held on {date}[ at {venue}].",
        "closing_line": "Given on {given_date} at {organization}.",
    },
    "appreciation": {
        "title": "Certificate of Appreciation",
        "line_before_name": "is presented to",
        "main_sentence": (
            "in grateful recognition of their valuable contribution as resource person[ on “{topic}”] during "
            "“{activity}” held on {date}[ at {venue}]."
        ),
        "closing_line": "Given on {given_date} at {organization}.",
    },
}
RECIPIENT_TYPES = {"participation": "registration", "appreciation": "resource_person"}


def forwards(apps, schema_editor):
    CertificateText = apps.get_model("certificates", "CertificateText")
    Certificate = apps.get_model("certificates", "Certificate")
    counts = {}
    for certificate_type, wording in DEFAULTS.items():
        row = CertificateText.objects.filter(certificate_type=certificate_type, activity__isnull=True,
                                             is_current=True).first()
        if row is None:
            row = CertificateText.objects.create(certificate_type=certificate_type, is_current=True, **wording)
        counts[certificate_type] = Certificate.objects.filter(
            recipient_type=RECIPIENT_TYPES[certificate_type], certificate_text__isnull=True).update(certificate_text=row)
    print(f"\n    Default wording: 2 rows (Participation, Appreciation), the text printed until now."
          f"\n    Certificates: {counts['participation']} Participation and {counts['appreciation']} Appreciation got the "
          "default wording (no PDF changed)."
          "\n    Activities: none has custom text and none was marked approved (\"Needs approval\").")


def backwards(apps, schema_editor):
    apps.get_model("certificates", "Certificate").objects.update(certificate_text=None)
    apps.get_model("certificates", "CertificateText").objects.all().delete()


class Migration(migrations.Migration):
    dependencies = [("certificates", "0012_certificate_text")]

    operations = [migrations.RunPython(forwards, backwards)]
