"""Turn API input (strings and IDs) into Python values. Business rules live in services.py."""

from django import forms
from django.core.exceptions import ValidationError

from .models import Activity, ActivityType, ParticipantCategory
from .typed_fields import TypedDateField, TypedDateTimeField, TypedTimeField


class ActivityDataForm(forms.Form):
    """Type conversion only. Inactive activity types are accepted here so that
    services.validate_activity_values() can give the clear "inactive" message."""

    title = forms.CharField(max_length=255)
    description = forms.CharField(required=False)
    objectives = forms.CharField(required=False)  # one per line (ASSUMPTIONS 428); services.clean_objectives tidies it
    activity_type = forms.ModelChoiceField(queryset=ActivityType.objects.all())
    participant_categories = forms.ModelMultipleChoiceField(  # a list of IDs, e.g. [1, 3]
        queryset=ParticipantCategory.objects.all(),
        error_messages={"required": "Choose at least one participant category."},
    )
    start_date = forms.DateField()
    end_date = forms.DateField()
    start_time = forms.TimeField()  # "08:00" (Manila time)
    end_time = forms.TimeField()
    venue = forms.CharField(max_length=255)
    max_slots = forms.IntegerField()
    registration_deadline = forms.DateTimeField()  # e.g. "2026-11-01T17:00" (Manila time)
    evaluation_deadline = forms.DateTimeField(required=False)  # empty / null = stays open (ASSUMPTIONS 423)
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


class ActivityPageForm(ActivityDataForm):
    """The create / edit page (activities/page_views.py). Same conversion as the API; every rule is still
    checked by services.create_activity / update_activity, whose errors the view puts under the fields.
    The type list offers the active types plus the activity's current one (an inactive type sent anyway
    gets the service's "inactive" message)."""

    # Typed dates and times (ASSUMPTIONS 422): month suggestions + calendar, hh:mm + AM/PM (activities.typed_fields).
    start_date = TypedDateField(help_text="For example June 5, 2026.")
    end_date = TypedDateField(help_text="For example June 5, 2026.")
    start_time = TypedTimeField(help_text="Philippine time, for example 08:00 AM.")
    end_time = TypedTimeField(help_text="On a one-day activity, after the start time.")
    registration_deadline = TypedDateTimeField(help_text="Philippine time. Registration closes at this time.")
    evaluation_deadline = TypedDateTimeField(
        required=False,
        help_text="Optional. Philippine time. Empty = the evaluation stays open (no certificate without an evaluation).")
    description = forms.CharField(required=False, widget=forms.Textarea)
    objectives = forms.CharField(
        required=False, widget=forms.Textarea(attrs={"rows": 4}),
        help_text="Optional. One objective per line. Shown on the Overview and used in the reports.")

    field_order = [
        "title", "description", "objectives", "activity_type", "participant_categories", "start_date", "end_date", "start_time", "end_time",
        "venue", "max_slots", "registration_deadline", "evaluation_deadline", "budget_amount",
    ]

    def __init__(self, *args, activity: Activity | None = None, **kwargs):
        super().__init__(*args, **kwargs)
        labels = {"activity_type": "Activity type", "participant_categories": "Participant categories",
                  "start_time": "Start time", "end_time": "End time", "max_slots": "Max slots", "budget_amount": "Budget"}
        for name, label in labels.items():
            self.fields[name].label = label
        self.fields["budget_amount"].help_text = "In pesos (₱), two decimals."
        types = ActivityType.objects.filter(is_active=True)
        if activity is not None:
            types = types | ActivityType.objects.filter(pk=activity.activity_type_id)
        self.fields["activity_type"].choices = [("", "Choose a type")] + [(t.pk, t.name) for t in types.order_by("name")]
        self.fields["participant_categories"].queryset = ParticipantCategory.objects.order_by("name")


SIGNATORY_SLOTS = ("left", "right")  # certificates.models.Slot values, printed left to right
PREPARE_FIELD = "prepare_certificates"  # the New activity form's optional "Prepare the certificates now" (ASSUMPTIONS 420)
ADD_PERSON = "add"  # the dropdown's "Add a person…" (opens a dialog; never saved as a choice)


class SignatoriesFormMixin(forms.Form):
    """"Who signs the certificates" on the activity form (ASSUMPTIONS 365): per slot a person (active people, plus the
    activity's current person when inactive) and an optional "Title on this certificate". Conversion only: the rules
    (active, never both sides, never emptied) are certificates.services.assign_default_signatories /
    set_activity_signatories. `signatory_choices()` gives the service's {slot: (person or None, title)}."""

    signatory_left = forms.ChoiceField(required=False, label="Left")
    title_left = forms.CharField(required=False, max_length=255, label="Title on this certificate")
    signatory_right = forms.ChoiceField(required=False, label="Right")
    title_right = forms.CharField(required=False, max_length=255, label="Title on this certificate")

    def setup_signatories(self, current: dict, can_add: bool, can_choose: bool = True, titles: dict | None = None) -> None:
        """`current`: {slot: the person shown first (the activity's, or the default on a new activity) or None}.
        `can_choose` False (no can_approve_certificates, ASSUMPTIONS 431): the slots are shown read only and anything
        posted for them is ignored (signatory_choices() gives None)."""
        from certificates.models import CertificateSignatory

        self.signatory_editable = can_choose

        self.signatory_people = {p.pk: p for p in CertificateSignatory.objects.filter(is_active=True).order_by("name", "position", "pk")}
        self.signatory_current = current
        for slot in SIGNATORY_SLOTS:
            person = current.get(slot)
            people = dict(self.signatory_people)
            if person is not None:
                people.setdefault(person.pk, person)  # an inactive current person stays selectable (kept as it is)
            choices = [("", "Not chosen yet")] if person is None else []
            choices += [(str(pk), f"{p.name} · {p.position}") for pk, p in people.items()]
            if can_add:
                choices.append((ADD_PERSON, "Add a person…"))
            field = self.fields[f"signatory_{slot}"]
            field.choices = choices
            field.initial = str(person.pk) if person is not None else ""
            field.allowed_people = people
            # The saved "Title on this certificate" (edit form): an untouched field must not clear it.
            self.fields[f"title_{slot}"].initial = (titles or {}).get(slot, "")

    def _clean_person(self, slot: str):
        if not self.signatory_editable or not self.signatories_used():  # "Prepare the certificates now" not chosen: the defaults (ASSUMPTIONS 420)
            return None
        raw = self.cleaned_data.get(f"signatory_{slot}") or ""
        if raw == ADD_PERSON:
            raise ValidationError("Choose a person from the list (add one first with “Add a person…”).")
        if not raw:
            return None
        return self.fields[f"signatory_{slot}"].allowed_people.get(int(raw))

    def clean_signatory_left(self):
        return self._clean_person("left")

    def clean_signatory_right(self):
        return self._clean_person("right")

    def signatories_used(self) -> bool:
        """True when the posted signatories count: always on the edit form; on the New activity form only with
        "Prepare the certificates now" ticked (ASSUMPTIONS 420)."""
        field = self.fields.get(PREPARE_FIELD)
        if field is None:
            return True
        return bool(field.widget.value_from_datadict(self.data, self.files, self.add_prefix(PREPARE_FIELD)))

    def certificate_preparation(self) -> bool:
        """The New activity form's "Prepare the certificates now" for the services (ASSUMPTIONS 420); removes the field
        from cleaned_data (the activity services reject unknown keys). False on the edit form."""
        return bool(self.cleaned_data.pop(PREPARE_FIELD, False))

    def signatory_choices(self) -> dict | None:
        """{slot: (person or None, title text)} for the services; removes the four fields from cleaned_data (the
        activity services reject unknown keys). None when the member may not choose (ASSUMPTIONS 431): no change on
        the edit form, the defaults on the New form."""
        choices = {}
        for slot in SIGNATORY_SLOTS:
            person = self.cleaned_data.pop(f"signatory_{slot}", None)
            title = self.cleaned_data.pop(f"title_{slot}", "")
            choices[slot] = (person, title)
        return choices if self.signatory_editable else None


class ActivityPageFormWithSignatories(SignatoriesFormMixin, ActivityPageForm):
    """The create / edit page with "Who signs the certificates" (ASSUMPTIONS 365)."""

    def __init__(self, *args, activity: Activity | None = None, current_signatories: dict | None = None,
                 can_add_person: bool = False, can_choose_signatories: bool = True,
                 current_titles: dict | None = None, **kwargs):
        super().__init__(*args, activity=activity, **kwargs)
        if activity is None:  # closed by default: not ticked = default signatories and default text (ASSUMPTIONS 420)
            self.fields[PREPARE_FIELD] = forms.BooleanField(required=False, initial=False,
                                                            label="Prepare the certificates now")
        self.setup_signatories(current_signatories or {}, can_add_person, can_choose_signatories, current_titles)
