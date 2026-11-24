"""Designation / Position and Office / Agency (ASSUMPTIONS 426): fixed REGISTRATION columns, required for every new
registration (public page and API, startup portal), shown on the Registrations tab, the registration detail and the
attendance sheet; migration registrations/0010 moves the old "Affiliation / Organization" answers."""

import importlib
import json

import pytest
from django.apps import apps
from django.core.exceptions import ValidationError
from django.test import Client
from django.urls import reverse

from accounts.seed import DEFAULT_FORM_FIELDS, load_default_form_fields
from activities import services as activity_services
from activities.models import ActivityType
from registrations import services
from registrations.models import DefaultFormField, FormAnswer, FormField, FormType, Registration
from startups.models import Startup

from .conftest import FIXED, OLD_FIELD, form_of, registration_answers
from .conftest import SIGN_JSON, SIGN_POST

pytestmark = pytest.mark.django_db

migration = importlib.import_module("registrations.migrations.0010_affiliation_to_office_agency")
HTMX = {"HTTP_HX_REQUEST": "true"}


def client_for(member) -> Client:
    client = Client()
    client.force_login(member)
    return client


# --- The rule (registrations.services.register) ---------------------------------------------------------------------


@pytest.mark.parametrize("missing", ["designation", "office_agency"])
def test_both_fields_are_required(scheduled, missing) -> None:
    values = {**FIXED, missing: "   "}
    with pytest.raises(ValidationError) as refused:
        services.register(scheduled, name="Ana Cruz", email="ana@example.com", **values,
                          answers=registration_answers(scheduled))
    assert refused.value.message_dict == {missing: ["This field is required."]}
    assert not Registration.objects.exists()


def test_values_are_tidied_and_saved(scheduled) -> None:
    registration = services.register(
        scheduled, name="Ana Cruz", email="ana@example.com", designation="  Project   officer ",
        office_agency=" DTI  Caraga ", answers=registration_answers(scheduled))
    assert (registration.designation, registration.office_agency) == ("Project officer", "DTI Caraga")


def test_too_long_is_refused(scheduled) -> None:
    with pytest.raises(ValidationError) as refused:
        services.register(scheduled, name="Ana", email="ana@example.com", designation="x" * 256, office_agency="CSU",
                          answers=registration_answers(scheduled))
    assert "designation" in refused.value.message_dict


@pytest.mark.parametrize("label", ["Designation / Position", "Office / Agency", "position"])
def test_a_form_field_cannot_repeat_the_fixed_fields(director, scheduled, label) -> None:
    form = form_of(scheduled)
    with pytest.raises(ValidationError):
        services.add_field(director, form, field_label=label, field_type="short_text")


# --- Public page, API, staff pages -------------------------------------------------------------------------------


def test_public_page_asks_both_and_saves_them(client, scheduled) -> None:
    url = reverse("register", args=[scheduled.pk])
    page = client.get(url).content.decode()
    assert 'name="designation"' in page and 'name="office_agency"' in page
    assert "Designation / Position" in page and "Office / Agency" in page
    form = form_of(scheduled)
    data = {"name": "Ana Cruz", "email": "ana@example.com", "designation": "", "office_agency": "",
            f"field_{form.fields.get(field_label=OLD_FIELD).pk}": "CSU", "privacy_consent": "yes", **SIGN_POST, "website": ""}
    refused = client.post(url, data)
    assert refused.status_code == 400 and 'id="id_designation-error"' in refused.content.decode()
    assert 'id="id_office_agency-error"' in refused.content.decode()
    assert client.post(url, {**data, **FIXED}).status_code == 302
    assert Registration.objects.get().office_agency == "CSU"


def test_public_api_needs_and_saves_both(client, scheduled) -> None:
    url = f"/api/register/{scheduled.pk}/"
    body = {"name": "Ana Cruz", "email": "ana@example.com", "answers": registration_answers(scheduled),
            "privacy_consent": True, "signature": SIGN_JSON}
    refused = client.post(url, json.dumps(body), content_type="application/json")
    assert refused.status_code == 400 and {"designation", "office_agency"} <= set(refused.json()["errors"])
    assert client.post(url, json.dumps({**body, **FIXED}), content_type="application/json").status_code == 201
    assert Registration.objects.get().designation == FIXED["designation"]


def test_staff_pages_show_both(director, scheduled, signup) -> None:
    person = signup(scheduled, "ana@example.com", designation="Faculty member", office_agency="CSU College of Engineering")
    client = client_for(director)
    tab = client.get(f"{reverse('activities:detail', args=[scheduled.pk])}?tab=registrations").content.decode()
    assert "Designation / Position" in tab and "Faculty member" in tab and "CSU College of Engineering" in tab
    detail = client.get(reverse("registrations:registration_detail", args=[scheduled.pk, person.pk]), **HTMX)
    assert "Faculty member" in detail.content.decode() and "CSU College of Engineering" in detail.content.decode()
    sheet = client.get(reverse("registrations:attendance_sheet", args=[scheduled.pk])).content.decode()
    assert "Designation / Position" in sheet and "Faculty member" in sheet and "CSU College of Engineering" in sheet
    assert "ana@example.com" not in sheet  # still never an email on paper
    api = client.get(reverse("registrations:api_registrations", args=[scheduled.pk])).json()
    assert api["registrations"][0]["office_agency"] == "CSU College of Engineering"
    assert [r.pk for r in services.list_registrations(scheduled, search="engineering")] == [person.pk]


# --- Startup portal ------------------------------------------------------------------------------------------------


@pytest.fixture
def startup_client(client, scheduled):
    """A startup signed in through the portal's login page (as test_privacy_consent.py)."""
    startup = Startup.objects.create(name="Kape Caraga", email="kape@example.com", contact_number="0917")
    startup.set_password("Kalinaw-Sapa-2026!")
    startup.save()
    response = client.post(reverse("startups:portal_login_page"), {"email": startup.email, "password": "Kalinaw-Sapa-2026!"})
    assert response.status_code == 302, response.content
    return client


def test_portal_page_prefills_office_with_the_startup_name(startup_client, scheduled) -> None:
    url = reverse("startups:portal_register_page", args=[scheduled.pk])
    page = startup_client.get(url).content.decode()
    assert 'name="office_agency"' in page and 'value="Kape Caraga"' in page and 'name="designation"' in page
    answers = {f"field_{key}": value for key, value in registration_answers(scheduled).items()}
    response = startup_client.post(url, {**answers, "designation": "Founder", "office_agency": "Kape Caraga Inc.",
                                         "privacy_consent": "yes", **SIGN_POST})
    assert response.status_code == 302
    registration = Registration.objects.get()
    assert (registration.designation, registration.office_agency) == ("Founder", "Kape Caraga Inc.")


def test_portal_api_defaults_office_to_the_startup_name_but_needs_a_designation(startup_client, scheduled) -> None:
    url = reverse("startups:portal_register", args=[scheduled.pk])
    body = {"answers": registration_answers(scheduled), "privacy_consent": True, "signature": SIGN_JSON}
    refused = startup_client.post(url, json.dumps(body), content_type="application/json")
    assert refused.status_code == 400 and set(refused.json()["errors"]) == {"designation"}
    cleared = startup_client.post(url, json.dumps({**body, "designation": "CEO", "office_agency": ""}),
                                  content_type="application/json")
    assert cleared.status_code == 400 and "office_agency" in cleared.json()["errors"]  # emptied on purpose: refused
    accepted = startup_client.post(url, json.dumps({**body, "designation": "CEO"}), content_type="application/json")
    assert accepted.status_code == 201 and accepted.json()["registration"]["office_agency"] == "Kape Caraga"


# --- Seed and migration 0010 ---------------------------------------------------------------------------------------


def test_the_seed_has_no_registration_default_field(roles) -> None:
    assert DEFAULT_FORM_FIELDS["registration"] == []
    load_default_form_fields()
    assert not DefaultFormField.objects.filter(form_type=FormType.REGISTRATION).exists()


def test_migration_copies_answers_and_removes_the_field_only_where_unused(director, scheduled, valid_data, with_forms,
                                                                         signup) -> None:
    """scheduled: a locked form with the field and two registrations; unused: a form with the field, no registration."""
    unused = with_forms(activity_services.create_activity(director, valid_data(title="Unused form")))
    answered = signup(scheduled, "ana@example.com", designation="x", office_agency="x",
                      answers=registration_answers(scheduled, affiliation="  DTI   Caraga "))
    Registration.objects.filter(pk=answered.pk).update(designation="", office_agency="")  # as before migration 0009
    kept = signup(scheduled, "ben@example.com", office_agency="Already typed")
    unused_field = form_of(unused).fields.get(field_label=OLD_FIELD)
    renamed = DefaultFormField.objects.filter(field_label=OLD_FIELD).first()
    renamed.field_label = "Affiliation (our wording)"
    renamed.save()

    migration.forwards(apps, None)

    answered.refresh_from_db()
    assert (answered.designation, answered.office_agency) == ("", "DTI Caraga")
    kept.refresh_from_db()
    assert kept.office_agency == "Already typed"  # a value already there is never overwritten
    assert FormAnswer.objects.filter(registration=answered, field__field_label=OLD_FIELD).exists()  # answers stay
    assert form_of(scheduled).fields.filter(field_label=OLD_FIELD).exists()  # locked form keeps it
    assert not FormField.objects.filter(pk=unused_field.pk).exists()  # unused form loses it
    assert not DefaultFormField.objects.filter(field_label=OLD_FIELD).exists()
    assert DefaultFormField.objects.filter(field_label="Affiliation (our wording)").exists()  # renamed: left alone

    migration.backwards(apps, None)
    assert DefaultFormField.objects.filter(field_label=OLD_FIELD).count() == ActivityType.objects.count()
