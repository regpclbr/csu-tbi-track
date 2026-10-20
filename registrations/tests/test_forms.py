"""Forms: created from the default fields, one per type per activity, locked once answered,
and answers validated in one place (services.validate_answers)."""

import json

import pytest
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import IntegrityError, transaction

from accounts.seed import DATA_PRIVACY_CONSENT
from registrations import services
from registrations.models import DefaultFormField, FieldType, FormTemplate, FormType

from .conftest import form_of

# --- Creating forms ---------------------------------------------------------------------


@pytest.mark.django_db
def test_create_form_copies_the_default_fields(director, new_activity, defaults) -> None:
    form = services.create_form(director, new_activity, FormType.REGISTRATION)
    assert form.generated_from_default is True
    fields = [(f.field_label, f.field_type, services.parse_options(f.options), f.is_required) for f in form.fields.order_by("sort_order")]
    assert fields == [
        ("Affiliation / Organization", "short_text", [], True),
        ("Data Privacy consent", "checkbox", [DATA_PRIVACY_CONSENT], True),
    ]
    evaluation = services.create_form(director, new_activity, FormType.EVALUATION)
    assert [f.field_label for f in evaluation.fields.order_by("sort_order")] == [
        "Overall rating", "Resource person rating", "Organization / venue rating", "Key learnings", "Suggestions",
    ]


@pytest.mark.django_db
def test_create_form_without_defaults(director, new_activity, defaults) -> None:
    DefaultFormField.objects.filter(activity_type=new_activity.activity_type).delete()
    form = services.create_form(director, new_activity, FormType.REGISTRATION)
    assert form.generated_from_default is False and form.fields.count() == 0


@pytest.mark.django_db
def test_one_form_per_type_per_activity(director, new_activity, defaults) -> None:
    services.create_form(director, new_activity, FormType.REGISTRATION)
    with pytest.raises(services.RegistrationError, match="already has a registration form"):
        services.create_form(director, new_activity, FormType.REGISTRATION)
    with pytest.raises(IntegrityError), transaction.atomic():  # the database constraint too
        FormTemplate.objects.create(activity=new_activity, form_type=FormType.REGISTRATION, title="x", created_by=director)


@pytest.mark.django_db
def test_admin_staff_cannot_manage_forms(admin_staff, new_activity, defaults) -> None:
    with pytest.raises(PermissionDenied):
        services.create_form(admin_staff, new_activity, FormType.REGISTRATION)


# --- Editing fields ---------------------------------------------------------------------


@pytest.fixture
def form(director, new_activity, defaults) -> FormTemplate:
    return services.create_form(director, new_activity, FormType.REGISTRATION)


@pytest.mark.django_db
def test_add_edit_reorder_remove_fields(director, form) -> None:
    field = services.add_field(director, form, field_label="T-shirt size", field_type="multiple_choice", options=["S", "M", "L"])
    assert services.parse_options(field.options) == ["S", "M", "L"] and field.sort_order == 3

    services.update_field(director, field, field_label="Shirt size", options=["S", "M", "L", "XL"], is_required=False)
    field.refresh_from_db()
    assert (field.field_label, services.parse_options(field.options), field.is_required) == ("Shirt size", ["S", "M", "L", "XL"], False)

    ids = [f.pk for f in form.fields.order_by("sort_order")]
    services.reorder_fields(director, form, list(reversed(ids)))
    assert [f.pk for f in form.fields.order_by("sort_order")] == list(reversed(ids))

    services.remove_field(director, field)
    assert form.fields.count() == 2


@pytest.mark.django_db
def test_changing_a_choice_field_to_text_drops_its_options(director, form) -> None:
    field = services.add_field(director, form, field_label="Size", field_type="multiple_choice", options=["S", "M"])
    services.update_field(director, field, field_type="short_text")
    field.refresh_from_db()
    assert (field.field_type, field.options) == ("short_text", "")


@pytest.mark.django_db
@pytest.mark.parametrize("label", ["Name", " email ", "Contact number", "Participant category"])
def test_registration_columns_are_not_repeated_as_fields(director, form, label) -> None:
    with pytest.raises(ValidationError, match="already asked on every registration"):
        services.add_field(director, form, field_label=label, field_type="short_text")


@pytest.mark.django_db
@pytest.mark.parametrize(
    "field_type, options, message",
    [
        ("checkbox", [], "at least one option"),
        ("multiple_choice", ["A", " "], "no empty options"),
        ("multiple_choice", ["Yes", "yes"], "must not repeat"),
        ("multiple_choice", "not json", "JSON list"),
        ("short_text", ["A"], "Only multiple choice and checkbox"),
        ("dropdown", None, "Choose one of"),
    ],
)
def test_field_options_are_validated(director, form, field_type, options, message) -> None:
    with pytest.raises(ValidationError, match=message):
        services.add_field(director, form, field_label="Question", field_type=field_type, options=options)


@pytest.mark.django_db
def test_reorder_needs_every_field_once(director, form) -> None:
    with pytest.raises(services.RegistrationError, match="every field"):
        services.reorder_fields(director, form, [form.fields.first().pk])


# --- The form lock ------------------------------------------------------------------------


@pytest.mark.django_db
def test_fields_are_locked_once_the_form_has_answers(director, scheduled, signup) -> None:
    form = form_of(scheduled)
    field = form.fields.get(field_label="Affiliation / Organization")
    signup(scheduled, "ana@example.com")
    assert services.form_is_locked(form)

    attempts = [
        lambda: services.update_field(director, field, field_label="Company"),
        lambda: services.update_field(director, field, field_type="long_text"),
        lambda: services.update_field(director, form.fields.get(field_type="checkbox"), options=["I agree"]),
        lambda: services.remove_field(director, field),
        lambda: services.add_field(director, form, field_label="New question", field_type="short_text"),
    ]
    for attempt in attempts:
        with pytest.raises(services.RegistrationError, match="fields are locked"):
            attempt()
    field.refresh_from_db()
    assert field.field_label == "Affiliation / Organization"

    # Reordering is still allowed: it does not change what anyone answered.
    ids = [f.pk for f in form.fields.order_by("sort_order")]
    services.reorder_fields(director, form, list(reversed(ids)))


@pytest.mark.django_db
def test_unanswered_form_is_not_locked(scheduled) -> None:
    assert services.form_is_locked(form_of(scheduled)) is False


# --- Answer validation (one function) -------------------------------------------------


@pytest.fixture
def quiz(director, new_activity, defaults) -> FormTemplate:
    form = services.create_form(director, new_activity, FormType.EVALUATION)
    form.fields.all().delete()
    for label, field_type, options, required in [
        ("Short", "short_text", None, True),
        ("Long", "long_text", None, False),
        ("Rating", "rating", None, True),
        ("Choice", "multiple_choice", ["Red", "Blue"], True),
        ("Boxes", "checkbox", ["A", "B", "C"], False),
    ]:
        services.add_field(director, form, field_label=label, field_type=field_type, options=options, is_required=required)
    return form


def ids(form: FormTemplate) -> dict[str, str]:
    return {f.field_label: str(f.pk) for f in form.fields.all()}


@pytest.mark.django_db
def test_valid_answers_are_stored_in_their_format(quiz) -> None:
    f = ids(quiz)
    stored = services.validate_answers(quiz, {f["Short"]: "  CSU  ", f["Rating"]: "5", f["Choice"]: "Blue", f["Boxes"]: ["C", "A"]})
    by_label = {field.field_label: value for field, value in stored.items()}
    assert by_label == {"Short": "CSU", "Rating": "5", "Choice": "Blue", "Boxes": json.dumps(["A", "C"])}  # form order


@pytest.mark.django_db
@pytest.mark.parametrize(
    "label, value, message",
    [
        ("Short", "", "required"),
        ("Short", "x" * 256, "under 255"),
        ("Rating", 0, "1 to 5"),
        ("Rating", 6, "1 to 5"),
        ("Rating", "great", "1 to 5"),
        ("Rating", True, "1 to 5"),
        ("Choice", "Green", "one of the given options"),
        ("Boxes", ["A", "Z"], "only from the given options"),
        ("Boxes", "A", "Choose from the given options"),
    ],
)
def test_invalid_answers(quiz, label, value, message) -> None:
    f = ids(quiz)
    answers = {f["Short"]: "CSU", f["Rating"]: 4, f["Choice"]: "Red"}  # a valid set...
    answers[f[label]] = value  # ...with one bad answer
    with pytest.raises(ValidationError) as excinfo:
        services.validate_answers(quiz, answers)
    assert message in excinfo.value.message_dict[f"field_{f[label]}"][0]


@pytest.mark.django_db
def test_required_checkbox_needs_a_tick(scheduled) -> None:
    form = form_of(scheduled)
    consent = form.fields.get(field_type=FieldType.CHECKBOX)
    affiliation = form.fields.get(field_type=FieldType.SHORT_TEXT)
    with pytest.raises(ValidationError) as excinfo:
        services.validate_answers(form, {str(affiliation.pk): "CSU", str(consent.pk): []})
    assert excinfo.value.message_dict[f"field_{consent.pk}"] == ["This field is required."]


@pytest.mark.django_db
def test_unknown_field_ids_are_refused(quiz) -> None:
    with pytest.raises(ValidationError, match="Unknown field IDs"):
        services.validate_answers(quiz, {"999999": "x"})
