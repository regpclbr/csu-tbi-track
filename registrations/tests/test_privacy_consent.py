"""The privacy notice and its required checkbox (ASSUMPTIONS 394): the public registration page and /api/register/, the
startup portal's registration (page and API), the evaluation review step and /api/evaluate/. Nothing is stored; without
the tick nothing is saved."""

import json

import pytest
from django.urls import reverse

from registrations import services
from registrations.models import EvaluationSubmission, Registration

from .conftest import CERT, FIXED, evaluation_answers, registration_answers, SIGN_JSON, SIGN_POST
from .test_public_pages import eval_url, evaluation_post, open_window, registration_post

pytestmark = pytest.mark.django_db

FIELD = services.PRIVACY_CONSENT_FIELD


# --- The rule -------------------------------------------------------------------------------------------------------


@pytest.mark.parametrize("value", [True, "yes", "on", "true", "1", " YES "])
def test_ticked_values_are_consent(value) -> None:
    assert services.has_privacy_consent(value)
    services.check_privacy_consent(value)


@pytest.mark.parametrize("value", [None, "", "no", "false", False, 1, [], "maybe"])
def test_anything_else_is_no_consent(value) -> None:
    from django.core.exceptions import ValidationError

    assert not services.has_privacy_consent(value)
    with pytest.raises(ValidationError) as refused:
        services.check_privacy_consent(value)
    assert refused.value.message_dict == {FIELD: [services.PRIVACY_CONSENT_REQUIRED]}


# --- The notice page ------------------------------------------------------------------------------------------------


def test_privacy_page_is_public_and_has_the_review_text(client) -> None:
    response = client.get(reverse("privacy_notice"))
    assert response.status_code == 200
    text = response.content.decode()
    assert "Privacy notice" in text and "Data Privacy Act of 2012" in text
    assert "PLACEHOLDER" not in text
    assert "User Acceptance Testing" in text
    assert "Data entered in this review version is kept on the researchers' computer until you ask for it to be deleted." in text
    assert "To ask, contact the researchers listed below." in text
    assert "November 30, 2026" not in text and "Version November 17, 2026." not in text
    assert "Regine S. Paclibar and Chrizza Patricia E. Mandap" in text and "Version November 25, 2026." in text


def test_consent_summary_has_the_review_text(client, scheduled) -> None:
    text = client.get(reverse("register", args=[scheduled.pk])).content.decode()
    assert "PLACEHOLDER" not in text and "November 30, 2026" not in text
    assert ("In this review version, the data you enter is kept on the researchers' computer until you ask for it to be "
            "deleted, and it is not shared outside CSU and NAVIGATU.") in text
    assert "User Acceptance Testing" in text and "Regine S. Paclibar" in text


def test_public_footer_links_to_the_notice(client) -> None:
    assert reverse("privacy_notice") in client.get(reverse("register_list")).content.decode()


# --- Public registration page and API -------------------------------------------------------------------------------


def test_registration_page_shows_the_checkbox(client, scheduled) -> None:
    text = client.get(reverse("register", args=[scheduled.pk])).content.decode()
    assert f'name="{FIELD}"' in text and 'type="checkbox"' in text and reverse("privacy_notice") in text


def test_registration_page_without_the_tick_saves_nothing(client, scheduled) -> None:
    data = registration_post(scheduled)
    data.pop(FIELD)
    response = client.post(reverse("register", args=[scheduled.pk]), data)
    assert response.status_code == 400
    assert services.PRIVACY_CONSENT_REQUIRED in response.content.decode()
    assert not Registration.objects.exists()
    assert 'value="Ana Cruz"' in response.content.decode()  # the typed values are kept


def test_registration_page_with_the_tick_registers(client, scheduled) -> None:
    response = client.post(reverse("register", args=[scheduled.pk]), registration_post(scheduled))
    assert response.status_code == 302
    assert Registration.objects.count() == 1


def api_body(activity, **extra) -> str:
    body = {"name": "Ana Cruz", "email": "ana@example.com", **FIXED, "answers": registration_answers(activity),
            "signature": SIGN_JSON, **extra}
    return json.dumps(body)


@pytest.mark.parametrize("consent", [None, False, "no"])
def test_api_without_consent_is_refused(client, scheduled, consent) -> None:
    extra = {} if consent is None else {FIELD: consent}
    response = client.post(f"/api/register/{scheduled.pk}/", api_body(scheduled, **extra), content_type="application/json")
    assert response.status_code == 400
    assert response.json() == {"errors": {FIELD: [services.PRIVACY_CONSENT_REQUIRED]}}
    assert not Registration.objects.exists()


def test_api_with_consent_registers(client, scheduled) -> None:
    response = client.post(f"/api/register/{scheduled.pk}/", api_body(scheduled, **{FIELD: True}),
                           content_type="application/json")
    assert response.status_code == 201
    assert Registration.objects.count() == 1


def test_the_service_itself_is_unchanged(scheduled, signup) -> None:
    """Only the public entry points ask: staff tools and the demo seed call register() as before."""
    assert signup(scheduled, "staff.added@example.com").pk


# --- Startup portal -------------------------------------------------------------------------------------------------


@pytest.fixture
def startup_client(client, scheduled):
    """A startup signed in through the portal's login page (as startups/tests/test_portal_pages.py)."""
    from startups.models import Startup

    startup = Startup.objects.create(name="Kape Caraga", email="kape@example.com", contact_number="0917")
    startup.set_password("Kalinaw-Sapa-2026!")
    startup.save()
    response = client.post(reverse("startups:portal_login_page"), {"email": startup.email, "password": "Kalinaw-Sapa-2026!"})
    assert response.status_code == 302, response.content
    return client


def portal_data(activity, **extra) -> dict:
    data = {"designation": "Founder", "office_agency": "Kape Caraga", **SIGN_POST}  # ASSUMPTIONS 426, 434
    for key, value in registration_answers(activity).items():
        data[f"field_{key}"] = value[0] if isinstance(value, list) else value
    data.update(extra)
    return data


def test_portal_page_without_the_tick_saves_nothing(startup_client, scheduled) -> None:
    url = reverse("startups:portal_register_page", args=[scheduled.pk])
    response = startup_client.post(url, portal_data(scheduled))
    if response.status_code == 302:  # the session fixture did not log in: a wrong setup, not a pass
        pytest.fail(f"Not logged in: redirected to {response['Location']}")
    assert response.status_code == 400
    assert services.PRIVACY_CONSENT_REQUIRED in response.content.decode()
    assert not Registration.objects.exists()


def test_portal_page_with_the_tick_registers(startup_client, scheduled) -> None:
    url = reverse("startups:portal_register_page", args=[scheduled.pk])
    response = startup_client.post(url, portal_data(scheduled, **{FIELD: "yes"}))
    assert response.status_code == 302
    assert Registration.objects.get().startup.email == "kape@example.com"


def test_portal_api_needs_consent(startup_client, scheduled) -> None:
    url = reverse("startups:portal_register", args=[scheduled.pk])
    body = {"designation": "Founder", "answers": registration_answers(scheduled), "signature": SIGN_JSON}
    refused = startup_client.post(url, json.dumps(body), content_type="application/json")
    assert refused.status_code == 400 and FIELD in refused.json()["errors"]
    assert not Registration.objects.exists()
    accepted = startup_client.post(url, json.dumps({**body, FIELD: True}), content_type="application/json")
    assert accepted.status_code == 201


# --- Evaluation -----------------------------------------------------------------------------------------------------


@pytest.fixture
def person(attended, scheduled):
    attendee = attended(1)[0]
    open_window(scheduled)
    return attendee


def test_review_step_shows_the_checkbox(client, person, scheduled) -> None:
    response = client.post(eval_url(person), evaluation_post(scheduled, step="review"))
    assert response.status_code == 200
    assert f'name="{FIELD}"' in response.content.decode()


def test_confirm_without_the_tick_stays_on_the_review_page(client, person, scheduled) -> None:
    data = evaluation_post(scheduled, step="confirm")
    data.pop(FIELD)
    response = client.post(eval_url(person), data)
    assert response.status_code == 400
    text = response.content.decode()
    assert services.PRIVACY_CONSENT_REQUIRED in text
    assert CERT["certificate_name"] in text  # still the review page, values kept
    assert not EvaluationSubmission.objects.exists()


def test_confirm_with_the_tick_submits(client, person, scheduled) -> None:
    response = client.post(eval_url(person), evaluation_post(scheduled, step="confirm"))  # the helper ticks it
    assert response.status_code == 302
    assert EvaluationSubmission.objects.filter(registration=person).exists()


def test_evaluation_api_needs_consent(client, person, scheduled) -> None:
    url = f"/api/evaluate/{services.evaluation_token(person)}/"
    body = {**CERT, "comments": "", "answers": evaluation_answers(scheduled)}
    refused = client.post(url, json.dumps(body), content_type="application/json")
    assert refused.status_code == 400 and FIELD in refused.json()["errors"]
    assert not EvaluationSubmission.objects.exists()
    accepted = client.post(url, json.dumps({**body, FIELD: True}), content_type="application/json")
    assert accepted.status_code == 201
