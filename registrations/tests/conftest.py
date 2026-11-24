"""Fixtures for Step 5 tests: activities with forms built from the default fields."""

from datetime import datetime, time

import pytest
from django.utils import timezone

from accounts.seed import load_default_form_fields
from activities import services as activity_services
from activities.models import Activity, ParticipantCategory
from activities.tests.conftest import (  # noqa: F401  (re-exported fixtures)
    TEST_NOW,
    at,
    director,
    move_to,
    new_activity,
    valid_data,
)
from registrations import services
from registrations.models import FormTemplate, FormType, Registration


def cat(name: str) -> ParticipantCategory:
    return ParticipantCategory.objects.get(name=name)


def form_of(activity: Activity, form_type: str = FormType.REGISTRATION) -> FormTemplate:
    return activity.form_templates.get(form_type=form_type)


def field_id(form: FormTemplate, label: str) -> str:
    return str(form.fields.get(field_label=label).pk)


# The fixed fields every new registration needs (ASSUMPTIONS 426).
FIXED = {"designation": "Project officer", "office_agency": "CSU"}
# The signature every public registration entry point now requires (ASSUMPTIONS 434): a typed one.
SIGN_JSON = {"kind": "typed", "typed_name": "Ana Cruz", "confirmed": True}
SIGN_POST = {"signature_kind": "typed", "signature_typed_name": "Ana Cruz", "signature_confirm": "yes"}
OLD_FIELD = "Affiliation / Organization"  # no longer a default field (ASSUMPTIONS 426); some tests add it by hand


def registration_answers(activity: Activity, affiliation: str = "CSU") -> dict:
    """Valid answers for the activity's registration form: the default form has no fields of its own any more
    (ASSUMPTIONS 426); a test that added the old "Affiliation / Organization" field gets it answered."""
    form = form_of(activity)
    field = form.fields.filter(field_label=OLD_FIELD).first()
    return {str(field.pk): affiliation} if field else {}


# The certificate name and email every evaluation now needs (Step 9).
CERT = {"certificate_name": "Ana Cruz", "certificate_email": "ana.cert@example.com"}


def evaluation_answers(activity: Activity) -> dict:
    """Valid answers for the default evaluation form."""
    form = form_of(activity, FormType.EVALUATION)
    return {
        field_id(form, "Overall rating"): 5,
        field_id(form, "Resource person rating"): "4",
        field_id(form, "Organization / venue rating"): 4,
        field_id(form, "Key learnings"): "How to pitch.",
    }


def opens_at(activity: Activity) -> datetime:
    return timezone.make_aware(datetime.combine(activity.end_date, time(0, 0)))


@pytest.fixture
def defaults(roles) -> None:
    """The seeded default fields (since ASSUMPTIONS 426 none for registration forms), plus ONE test-only required
    short-text registration field per activity type, with the old default's label, so the field editor, the answer
    rules and the locking tests have a field to work on. The real seed never creates it (test_fixed_registration_fields)."""
    load_default_form_fields()
    from activities.models import ActivityType
    from registrations.models import DefaultFormField

    for activity_type in ActivityType.objects.all():
        DefaultFormField.objects.get_or_create(
            activity_type=activity_type, form_type=FormType.REGISTRATION, field_label=OLD_FIELD,
            defaults={"field_type": "short_text", "is_required": True, "sort_order": 1},
        )


@pytest.fixture
def with_forms(director, defaults):
    """Factory: give an activity both forms (from the defaults)."""

    def make(activity: Activity) -> Activity:
        services.create_form(director, activity, FormType.REGISTRATION)
        services.create_form(director, activity, FormType.EVALUATION)
        return activity

    return make


@pytest.fixture
def scheduled(new_activity, with_forms, move_to) -> Activity:
    """A Scheduled activity (one category: Incubatees, 30 slots) with both forms: registration is open."""
    with_forms(new_activity)
    move_to(new_activity, "Scheduled")
    return new_activity


@pytest.fixture
def signup():
    """Factory: signup(activity, email) registers someone through the public service."""

    def make(activity: Activity, email: str, **extra) -> Registration:
        extra.setdefault("answers", registration_answers(activity))
        for key, value in FIXED.items():
            extra.setdefault(key, value)
        return services.register(activity, name=extra.pop("name", "Ana Cruz"), email=email, **extra)

    return make


@pytest.fixture
def attended(scheduled, signup, director):
    """Factory: attended(n) registers n people, moves the activity to Ongoing and marks them attended."""

    def make(count: int = 1) -> list[Registration]:
        people = [signup(scheduled, f"person{n}@example.com") for n in range(count)]
        activity_services.change_status(director, scheduled, "Ongoing")
        services.set_attendance(director, scheduled, [p.pk for p in people], True, "staff")
        for person in people:
            person.refresh_from_db()
        return people

    return make
