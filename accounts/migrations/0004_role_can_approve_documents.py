"""Add ROLE.can_approve_documents (ASSUMPTIONS 344): only the NAVIGATU Director approves or returns documents.

The data step sets the flag to True for the role named "Director" and False for every other role. Looking the role up
by name is done only here, once (like 0003); the code itself checks the flag. Without a "Director" row nothing is set
to True (seed_data gives the flag when it creates the role). Going back simply removes the column.
"""

from django.db import migrations, models

DIRECTOR = "Director"


def forwards(apps, schema_editor):
    Role = apps.get_model("accounts", "Role")
    Role.objects.exclude(role_name=DIRECTOR).update(can_approve_documents=False)
    changed = Role.objects.filter(role_name=DIRECTOR).update(can_approve_documents=True)
    if changed:
        print(f'\n  can_approve_documents is True for "{DIRECTOR}" and False for every other role.')
    else:
        print(f'\n  No role named "{DIRECTOR}": no role can approve documents yet (seed_data creates it).')


class Migration(migrations.Migration):
    dependencies = [("accounts", "0003_system_administrator")]

    operations = [
        migrations.AddField(
            model_name="role",
            name="can_approve_documents",
            field=models.BooleanField(default=False, db_default=False),
        ),
        migrations.RunPython(forwards, migrations.RunPython.noop),
    ]
