"""Data only (ASSUMPTIONS 426, NAVIGATU UAT round 2): "Designation / Position" and "Office / Agency" are now fixed
REGISTRATION columns (migration 0009), so the free form field "Affiliation / Organization" is no longer needed.

1. Every registration whose office_agency is still empty gets its "Affiliation / Organization" answer copied into
   office_agency (trimmed, at most 255 characters). designation stays empty (it was never asked). The old FORM_ANSWER
   rows are KEPT (nothing is deleted from a registration).
2. The DEFAULT_FORM_FIELD rows named exactly "Affiliation / Organization" (registration forms) are removed, so new
   registration forms no longer ask it. A renamed or edited label is left alone.
3. That field is removed from registration forms NOBODY has submitted yet (no registration, no answer: the same test as
   services.form_is_locked). Forms with registrations keep it (they are locked) and their answers stay.

Only the label matched exactly is touched. No activity, file or email. Reverse: the default field is put back for every
activity type that has no field of that name (short text, required, first), the fields removed from unused forms are
NOT put back (staff add a field on the Forms tab), and the copied values disappear with the columns (0009 reversed).
"""

from django.db import migrations

LABEL = "Affiliation / Organization"
REGISTRATION = "registration"
MAX_LENGTH = 255


def forwards(apps, schema_editor):
    Registration = apps.get_model("registrations", "Registration")
    FormAnswer = apps.get_model("registrations", "FormAnswer")
    FormField = apps.get_model("registrations", "FormField")
    DefaultFormField = apps.get_model("registrations", "DefaultFormField")

    answers = (
        FormAnswer.objects.filter(field__field_label=LABEL, registration__isnull=False, registration__office_agency="")
        .order_by("answer_id")
        .values_list("registration_id", "answer_value")
    )
    copied: dict[int, str] = {}
    for registration_id, value in answers:
        value = " ".join((value or "").split())[:MAX_LENGTH]
        if value and registration_id not in copied:
            copied[registration_id] = value
    for registration_id, value in copied.items():
        Registration.objects.filter(pk=registration_id, office_agency="").update(office_agency=value)

    DefaultFormField.objects.filter(form_type=REGISTRATION, field_label=LABEL).delete()

    for field in FormField.objects.filter(field_label=LABEL, template__form_type=REGISTRATION).select_related("template"):
        template = field.template
        used = (
            Registration.objects.filter(template=template).exists()
            or template.evaluation_submissions.exists()
            or FormAnswer.objects.filter(field__template=template).exists()
        )
        if not used:
            field.delete()


def backwards(apps, schema_editor):
    ActivityType = apps.get_model("activities", "ActivityType")
    DefaultFormField = apps.get_model("registrations", "DefaultFormField")
    for activity_type in ActivityType.objects.all():
        if not DefaultFormField.objects.filter(activity_type_id=activity_type.pk, form_type=REGISTRATION,
                                               field_label=LABEL).exists():
            DefaultFormField.objects.create(
                activity_type_id=activity_type.pk, form_type=REGISTRATION, field_label=LABEL, field_type="short_text",
                options="", is_required=True, sort_order=1,
            )


class Migration(migrations.Migration):
    dependencies = [
        ("registrations", "0009_registration_designation_office"),
        ("activities", "0012_activity_objectives_registration_closed"),
    ]

    operations = [migrations.RunPython(forwards, backwards)]
