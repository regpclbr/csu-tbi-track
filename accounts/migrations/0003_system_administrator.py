"""Rename the role "Admin" to "System Administrator" and take can_create_activity away from it (ASSUMPTIONS 85, 339).

Separation of duties: the system administrator manages user accounts and does not create activities. Role names are
unique, so the migration stops (changing nothing) if a role called "System Administrator" already exists. Going back
restores the old name and the old flag.
"""

from django.db import migrations

OLD_NAME, NEW_NAME = "Admin", "System Administrator"
NEW_DESCRIPTION = "System administrator: manages user accounts and system settings; does not create activities."


def forwards(apps, schema_editor):
    Role = apps.get_model("accounts", "Role")
    if not Role.objects.filter(role_name=OLD_NAME).exists():
        print(f'\n  No role named "{OLD_NAME}": nothing to rename (seed_data creates "{NEW_NAME}").')
        return
    if Role.objects.filter(role_name__iexact=NEW_NAME).exists():
        raise RuntimeError(
            f'A role named "{NEW_NAME}" already exists, so "{OLD_NAME}" cannot be renamed. Nothing was changed. '
            "Rename or merge the existing role in the Django admin first, then run migrate again."
        )
    changed = Role.objects.filter(role_name=OLD_NAME).update(
        role_name=NEW_NAME, can_create_activity=False, can_manage_users=True, description=NEW_DESCRIPTION
    )
    print(f'\n  Renamed "{OLD_NAME}" to "{NEW_NAME}" ({changed} row); can_create_activity is now False.')


def backwards(apps, schema_editor):
    Role = apps.get_model("accounts", "Role")
    Role.objects.filter(role_name=NEW_NAME).update(
        role_name=OLD_NAME, can_create_activity=True, description="System administrator; manages user accounts."
    )


class Migration(migrations.Migration):
    dependencies = [("accounts", "0002_member_email_ignoring_case")]

    operations = [migrations.RunPython(forwards, backwards)]
