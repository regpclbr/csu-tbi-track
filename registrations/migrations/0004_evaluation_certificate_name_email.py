"""Step 9 (ERD change): EVALUATION_SUBMISSION.certificate_name and certificate_email.

The columns are added as nullable, then every existing evaluation is filled from its
registration's name and email (trimmed; email in lower case). The next migration (0005)
makes them NOT NULL.

If any evaluation cannot be filled (empty or over-long name, invalid email), the migration stops
with the list of submission IDs and changes nothing. It prints the counts.
"""

from django.core.exceptions import ValidationError
from django.core.validators import validate_email
from django.db import migrations, models

NAME_MAX = 150
EMAIL_MAX = 254


def fill_from_registration(apps, schema_editor) -> None:
    EvaluationSubmission = apps.get_model("registrations", "EvaluationSubmission")
    total = filled = 0
    problems: list[str] = []
    for submission in EvaluationSubmission.objects.select_related("registration").order_by("pk"):
        total += 1
        name = (submission.registration.name or "").strip()
        email = (submission.registration.email or "").strip().lower()
        try:
            validate_email(email)
            valid_email = len(email) <= EMAIL_MAX
        except ValidationError:
            valid_email = False
        if not name or len(name) > NAME_MAX or not valid_email:
            problems.append(str(submission.pk))
            continue
        submission.certificate_name = name
        submission.certificate_email = email
        submission.save(update_fields=["certificate_name", "certificate_email"])
        filled += 1
    print(f"\n    Evaluation submissions: {total}. Filled from the registration: {filled}. Could not fill: {len(problems)}.")
    if problems:
        raise RuntimeError(
            "These evaluation submissions could not be filled from their registration (empty or longer than "
            f"{NAME_MAX} characters name, or invalid email): {', '.join(problems)}. Nothing was changed."
        )


class Migration(migrations.Migration):
    dependencies = [("registrations", "0003_registration_category_required")]

    operations = [
        migrations.AddField(
            model_name="evaluationsubmission",
            name="certificate_name",
            field=models.CharField(max_length=150, null=True),
        ),
        migrations.AddField(
            model_name="evaluationsubmission",
            name="certificate_email",
            field=models.EmailField(max_length=254, null=True),
        ),
        migrations.RunPython(fill_from_registration, migrations.RunPython.noop),
    ]
