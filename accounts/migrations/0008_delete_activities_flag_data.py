"""ROLE.can_delete_activities (ASSUMPTIONS 389): True for the role named "System Administrator", False for every other
role (the default of 0007). Looking the role up by name happens only here, once (like 0004-0006); the code checks the
flag. Nothing happens when the role does not exist (a new database: seed_data creates it with the flag). Going back
sets it False everywhere (0007's column is then removed by reversing 0007).
"""

from django.db import migrations

ROLE_NAME = "System Administrator"


def forwards(apps, schema_editor):
    Role = apps.get_model("accounts", "Role")
    changed = Role.objects.filter(role_name=ROLE_NAME).update(can_delete_activities=True)
    print(f"\n  can_delete_activities is True for {changed} role ({ROLE_NAME}) and False for every other role.")


def backwards(apps, schema_editor):
    apps.get_model("accounts", "Role").objects.update(can_delete_activities=False)


class Migration(migrations.Migration):
    dependencies = [("accounts", "0007_delete_activities_flag_and_positions")]

    operations = [migrations.RunPython(forwards, backwards)]
