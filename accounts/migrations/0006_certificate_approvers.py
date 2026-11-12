"""Certificate approvers (ASSUMPTIONS 361): can_approve_certificates becomes True for the Director, the Incubation
Project Head, the Community Development Head and the System Administrator, and False for every other role
(Admin Staff only views certificates).

Looking roles up by name happens only here, once (like 0004 and 0005); the code checks the flag. A role that does not
exist is skipped. Going back gives the flag to the Director only again (0005's rule).
"""

from django.db import migrations

APPROVERS = ("Director", "Incubation Project Head", "Community Development Head", "System Administrator")


def forwards(apps, schema_editor):
    Role = apps.get_model("accounts", "Role")
    Role.objects.exclude(role_name__in=APPROVERS).update(can_approve_certificates=False)
    changed = Role.objects.filter(role_name__in=APPROVERS).update(can_approve_certificates=True)
    print(f"\n  can_approve_certificates is True for {changed} of the {len(APPROVERS)} approver roles "
          "and False for every other role.")


def backwards(apps, schema_editor):
    Role = apps.get_model("accounts", "Role")
    Role.objects.exclude(role_name="Director").update(can_approve_certificates=False)
    Role.objects.filter(role_name="Director").update(can_approve_certificates=True)


class Migration(migrations.Migration):
    dependencies = [("accounts", "0005_role_can_approve_certificates")]

    operations = [migrations.RunPython(forwards, backwards)]
