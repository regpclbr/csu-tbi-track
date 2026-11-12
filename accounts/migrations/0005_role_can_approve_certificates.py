"""Add ROLE.can_approve_certificates (ASSUMPTIONS 357): only the TBI Director approves certificates.

Same pattern as 0004: the data step sets the flag to True for the role named "Director" and False for every other role.
Looking the role up by name is done only here, once; the code checks the flag. Without a "Director" row nothing is set
to True (seed_data gives the flag when it creates the role). Going back simply removes the column.
"""

from django.db import migrations, models

DIRECTOR = "Director"


def forwards(apps, schema_editor):
    Role = apps.get_model("accounts", "Role")
    Role.objects.exclude(role_name=DIRECTOR).update(can_approve_certificates=False)
    changed = Role.objects.filter(role_name=DIRECTOR).update(can_approve_certificates=True)
    if changed:
        print(f'\n  can_approve_certificates is True for "{DIRECTOR}" and False for every other role.')
    else:
        print(f'\n  No role named "{DIRECTOR}": no role can approve certificates yet (seed_data creates it).')


class Migration(migrations.Migration):
    dependencies = [("accounts", "0004_role_can_approve_documents")]

    operations = [
        migrations.AddField(
            model_name="role",
            name="can_approve_certificates",
            field=models.BooleanField(default=False, db_default=False),
        ),
        migrations.RunPython(forwards, migrations.RunPython.noop),
    ]
