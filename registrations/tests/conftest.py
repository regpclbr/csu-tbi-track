"""Fixtures for Step 5 tests: activities with forms built from the default fields."""

from datetime import datetime, time

import pytest
from django.utils import timezone

from accounts.seed import DATA_PRIVACY_CONSENT, load_default_form_fields
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


def registration_answers(activity: Activity, affiliation: str = "CSU") -> dict:
    """Valid answers for the default registration form."""
    form = form_of(activity)
    return {
        field_id(form, "Affiliation / Organization"): affiliation,
        field_id(form, "Data Privacy consent"): [DATA_PRIVACY_CONSENT],
    }


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
    load_default_form_fields()


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
        return services.register(activity, name=extra.pop("name", "Ana Cruz"), email=email, **extra)

    return make


@pytest.fixture
def attended(scheduled, signup, director):
    """Factory: attended(n) registers n people, moves the activity to Ongoing and marks them attended."""

    def make(count: int = 1) -> list[Registration]:
        people = [signup(scheduled, f"person{n}@example.com") for n in range(count)]
        activity_services.change_status(director, scheduled, "Ongoing")
        services.set_attendance(director, scheduled, [p.pk for p in people], True, "email")
        for person in people:
            person.refresh_from_db()
        return people

    return make
