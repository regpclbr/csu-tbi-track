"""Turn API input (strings and IDs) into Python values. Business rules live in services.py."""

from django import forms
from django.core.exceptions import ValidationError

from .models import ActivityType, ParticipantCategory


class ActivityDataForm(forms.Form):
    """Type conversion only. Inactive activity types are accepted here so that
    services.validate_activity_values() can give the clear "inactive" message."""

    title = forms.CharField(max_length=255)
    description = forms.CharField(required=False)
    activity_type = forms.ModelChoiceField(queryset=ActivityType.objects.all())
    participant_categories = forms.ModelMultipleChoiceField(  # a list of IDs, e.g. [1, 3]
        queryset=ParticipantCategory.objects.all(),
        error_messages={"required": "Choose at least one participant category."},
    )
    start_date = forms.DateField()
    end_date = forms.DateField()
    time_schedule = forms.CharField(max_length=100)
    venue = forms.CharField(max_length=255)
    max_slots = forms.IntegerField()
    registration_deadline = forms.DateTimeField()  # e.g. "2026-11-01T17:00" (Manila time)
    evaluation_deadline = forms.DateTimeField()
    budget_amount = forms.DecimalField(max_digits=12, decimal_places=2)


def parse_activity_data(raw: dict, partial: bool = False) -> dict:
    """Convert raw input to Python values. With partial=True only the given fields are read.

    Unknown keys are passed through unchanged so the service can reject them by name.
    Raises ValidationError({field: [messages]}) for values that cannot be converted.
    """
    form = ActivityDataForm(raw)
    if partial:
        for name in set(form.fields) - set(raw):
            del form.fields[name]
    if not form.is_valid():
        raise ValidationError({field: list(messages) for field, messages in form.errors.items()})
    parsed = dict(form.cleaned_data)
    parsed.update({key: value for key, value in raw.items() if key not in form.fields})
    return parsed
