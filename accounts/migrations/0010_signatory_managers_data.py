"""ROLE.can_manage_signatories (ASSUMPTIONS 418, NAVIGATU: only the TBI Director manages certificate signatories): True for
the role named "Director", False for every other role (the default of 0009). Looking the role up by name happens only
here, once (like 0004-0008); the code checks the flag.

Guarded:
- nothing happens when the role does not exist (a new database: seed_data creates it with the flag);
- a "Director" role that manages users is left alone (the flag is never combined with can_manage_users,
  accounts.services.check_role_flags); a message says so.
Going back sets it False everywhere (0009's column is then removed by reversing 0009).
"""

from django.db import migrations

ROLE_NAME = "Director"


def forwards(apps, schema_editor):
    Role = apps.get_model("accounts", "Role")
    roles = Role.objects.filter(role_name=ROLE_NAME)
    skipped = roles.filter(can_manage_users=True).count()
    changed = roles.filter(can_manage_users=False).update(can_manage_signatories=True)
    print(f"\n  can_manage_signatories is True for {changed} role ({ROLE_NAME}) and False for every other role.")
    if skipped:
        print(f"  Skipped {ROLE_NAME}: it manages users (never together with can_manage_signatories).")


def backwards(apps, schema_editor):
    apps.get_model("accounts", "Role").objects.update(can_manage_signatories=False)


class Migration(migrations.Migration):
    dependencies = [("accounts", "0009_role_can_manage_signatories")]

    operations = [migrations.RunPython(forwards, backwards)]
