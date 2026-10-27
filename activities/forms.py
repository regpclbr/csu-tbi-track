"""Turn API input (strings and IDs) into Python values. Business rules live in services.py."""

from datetime import datetime

from django import forms
from django.core.exceptions import ValidationError
from django.utils import timezone

from .models import Activity, ActivityType, ParticipantCategory


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


class DateInput(forms.DateInput):
    input_type = "date"

    def __init__(self, attrs=None):
        super().__init__(attrs, format="%Y-%m-%d")


class DateTimeLocalInput(forms.DateTimeInput):
    input_type = "datetime-local"


class LocalDateTimeField(forms.DateTimeField):
    """A date-time typed in Manila time (TIME_ZONE) in a datetime-local input; shown back the same way."""

    widget = DateTimeLocalInput

    def prepare_value(self, value):
        if isinstance(value, datetime):
            return timezone.localtime(value).strftime("%Y-%m-%dT%H:%M") if timezone.is_aware(value) else value.strftime("%Y-%m-%dT%H:%M")
        return value


class ActivityPageForm(ActivityDataForm):
    """The create / edit page (activities/page_views.py). Same conversion as the API; every rule is still
    checked by services.create_activity / update_activity, whose errors the view puts under the fields.
    The type list offers the active types plus the activity's current one (an inactive type sent anyway
    gets the service's "inactive" message)."""

    start_date = forms.DateField(widget=DateInput)
    end_date = forms.DateField(widget=DateInput)
    registration_deadline = LocalDateTimeField(help_text="Manila time. Registration closes at this time.")
    evaluation_deadline = LocalDateTimeField(help_text="Manila time. Evaluation forms close at this time.")
    description = forms.CharField(required=False, widget=forms.Textarea)

    field_order = [
        "title", "description", "activity_type", "participant_categories", "start_date", "end_date", "time_schedule",
        "venue", "max_slots", "registration_deadline", "evaluation_deadline", "budget_amount",
    ]

    def __init__(self, *args, activity: Activity | None = None, **kwargs):
        super().__init__(*args, **kwargs)
        labels = {"activity_type": "Activity type", "participant_categories": "Participant categories",
                  "time_schedule": "Time schedule", "max_slots": "Max slots", "budget_amount": "Budget"}
        for name, label in labels.items():
            self.fields[name].label = label
        self.fields["time_schedule"].help_text = "For example 8:00 AM – 5:00 PM."
        self.fields["budget_amount"].help_text = "In pesos (₱), two decimals."
        types = ActivityType.objects.filter(is_active=True)
        if activity is not None:
            types = types | ActivityType.objects.filter(pk=activity.activity_type_id)
        self.fields["activity_type"].choices = [("", "Choose a type")] + [(t.pk, t.name) for t in types.order_by("name")]
        self.fields["participant_categories"].queryset = ParticipantCategory.objects.order_by("name")
