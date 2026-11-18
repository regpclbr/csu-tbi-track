"""One participant's evaluation (ASSUMPTIONS 402): the answers WITH the participant's name, in the registration dialog
and as a PDF, only for staff with can_create_activity (owner decision 2026-11-17). Everyone else, the System
Administrator included, gets 403 on the PDF and only a note in the dialog (the summary without names stays open)."""

from datetime import date

import pytest
from django.core.exceptions import PermissionDenied
from django.test import Client
from django.urls import reverse

from registrations import services as registration_services
from registrations.models import EvaluationSubmission

pytestmark = pytest.mark.django_db


@pytest.fixture
def submitted(make_activity, register, evaluate):
    activity = make_activity("Pitching Clinic", start=date(2026, 3, 10), categories=("Incubatees",))
    registration = register(activity, "Ana Cruz", attended=True, email="ana.private@example.com")
    evaluate(registration, {"Overall rating": 4}, comments="More examples please",
             text_answers={"Key learnings": "How to open a pitch"})
    return registration


def pdf_url(registration) -> str:
    return reverse("registrations:evaluation_pdf", args=[registration.activity_id, registration.pk])


def dialog_url(registration) -> str:
    return reverse("registrations:registration_detail", args=[registration.activity_id, registration.pk])


# --- The rule ------------------------------------------------------------------------------------------------------


def test_only_can_create_activity_may_see_individual_evaluations(submitted, director, view_only, make_member) -> None:
    assert registration_services.INDIVIDUAL_EVALUATION_PERMISSION.value == "can_create_activity"
    for role in ("Incubation Project Head", "Community Development Head", "Admin Staff"):
        assert registration_services.can_view_individual_evaluations(make_member(role))
    assert registration_services.can_view_individual_evaluations(director)
    for member in (view_only, make_member("System Administrator")):
        assert not registration_services.can_view_individual_evaluations(member)
        with pytest.raises(PermissionDenied):
            registration_services.evaluation_answers(member, submitted)


def test_answers_in_form_order_without_email(submitted, director) -> None:
    data = registration_services.evaluation_answers(director, submitted)
    assert (data["name"], data["category"], data["certificate_name"]) == ("Ana Cruz", "Incubatees", "Ana Cruz")
    assert data["rows"] == [
        {"question": "Overall rating", "answer": "4 / 5", "field_type": "rating"},
        {"question": "Key learnings", "answer": "How to open a pitch", "field_type": "long_text"},
    ]
    assert data["comments"] == "More examples please"
    assert "ana.private@example.com" not in repr({k: v for k, v in data.items() if k != "registration"})


def test_a_registration_without_evaluation_raises(make_activity, register, director) -> None:
    registration = register(make_activity(), "No Answer", attended=True)
    with pytest.raises(EvaluationSubmission.DoesNotExist):
        registration_services.evaluation_answers(director, registration)


# --- The dialog ----------------------------------------------------------------------------------------------------


def test_the_dialog_shows_the_answers_and_the_pdf_button(submitted, director, login) -> None:
    html = login(director).get(dialog_url(submitted)).content.decode()
    assert "Evaluation answers" in html and 'id="evaluation-answers"' in html
    assert "How to open a pitch" in html and "4 / 5" in html and "More examples please" in html
    assert f'href="{pdf_url(submitted)}"' in html


@pytest.mark.parametrize("role", ["view_only", "System Administrator"])
def test_without_can_create_activity_the_dialog_hides_the_answers(submitted, role, view_only, make_member, login) -> None:
    member = view_only if role == "view_only" else make_member(role)
    response = login(member).get(dialog_url(submitted))
    html = response.content.decode()
    assert response.status_code == 200
    assert "Only staff who can create activities see a participant's evaluation answers." in html
    assert 'id="evaluation-answers"' not in html and "How to open a pitch" not in html
    assert "More examples please" not in html and pdf_url(submitted) not in html


def test_the_submitted_badge_opens_the_dialog(submitted, director, login) -> None:
    html = login(director).get(reverse("activities:detail", args=[submitted.activity_id]), {"tab": "registrations"}).content.decode()
    badge = html[html.index("Submitted<span class=\"sr-only\">") - 600:html.index("Submitted<span class=\"sr-only\">")]
    assert f'hx-get="{dialog_url(submitted)}"' in badge and "<button" in badge


# --- The PDF -------------------------------------------------------------------------------------------------------


def test_the_pdf_for_can_create_activity(submitted, director, login, fake_pdf_renderer) -> None:
    response = login(director).get(pdf_url(submitted))
    assert response.status_code == 200 and response["Content-Type"] == "application/pdf"
    assert response["Content-Disposition"] == (
        f'attachment; filename="evaluation-activity-{submitted.activity_id}-registration-{submitted.pk}.pdf"')
    assert response["Cache-Control"] == "private, no-store" and response["X-Content-Type-Options"] == "nosniff"
    text = fake_pdf_renderer.last_text()
    assert "Evaluation - Pitching Clinic" in text and "Ana Cruz" in text and "Incubatees" in text
    assert "How to open a pitch" in text and "4 / 5" in text and "More examples please" in text
    assert "ana.private@example.com" not in text


@pytest.mark.parametrize("role", ["view_only", "System Administrator"])
def test_the_pdf_is_403_without_can_create_activity(submitted, role, view_only, make_member, login, fake_pdf_renderer) -> None:
    member = view_only if role == "view_only" else make_member(role)
    assert login(member).get(pdf_url(submitted)).status_code == 403
    assert fake_pdf_renderer.rendered == []  # refused before anything is made


def test_the_pdf_is_never_public(submitted) -> None:
    response = Client().get(pdf_url(submitted))
    assert response.status_code == 302 and reverse("accounts:login") in response["Location"]


def test_no_evaluation_or_another_activity_is_404(submitted, make_activity, register, director, login) -> None:
    client = login(director)
    other = make_activity("Other")
    no_answer = register(other, "No Answer", attended=True)
    assert client.get(pdf_url(no_answer)).status_code == 404
    wrong_activity = reverse("registrations:evaluation_pdf", args=[other.pk, submitted.pk])
    assert client.get(wrong_activity).status_code == 404
