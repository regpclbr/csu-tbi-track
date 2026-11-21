"""One participant's evaluation (ASSUMPTIONS 402, 404): the answers WITH the participant's name, in the registration dialog
and as a portrait PDF, only for staff with can_create_activity (owner decision 2026-11-17). Everyone else, the System
Administrator included, gets 403 on the PDF and only a note in the dialog (the summary without names stays open).
Layout (404): header, average rating, one row per rating (dots + "4/5" + screen-reader text), text answers in cards
(empty ones left out), contact details and registration answers in a collapsed section."""

import re
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
    evaluate(registration, {"Overall rating": 4, "Venue rating": 5}, comments="More examples please",
             text_answers={"Key learnings": "How to open a pitch", "Anything else?": "  "})
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
    assert data["ratings"] == [
        {"question": "Overall rating", "value": 4, "maximum": 5},
        {"question": "Venue rating", "value": 5, "maximum": 5},
    ]
    assert data["average"] == 4.5
    assert data["answers"] == [  # the empty "Anything else?" is left out; Comments last
        {"label": "Key learnings", "answer": "How to open a pitch"},
        {"label": "Comments", "answer": "More examples please"},
    ]
    assert "ana.private@example.com" not in repr({k: v for k, v in data.items() if k != "registration"})


def test_average_has_two_decimals_and_skips_unanswered_ratings(make_activity, register, evaluate, director) -> None:
    registration = register(make_activity(), "Ben", attended=True)
    submission = evaluate(registration, {"A": 5, "B": 4, "C": 4})
    assert registration_services.evaluation_answers(director, registration)["average"] == 4.33
    submission.answers.filter(field__field_label="C").update(answer_value="")
    data = registration_services.evaluation_answers(director, registration)
    assert data["ratings"][2]["value"] is None and data["average"] == 4.5


def test_no_ratings_means_no_average(make_activity, register, evaluate, director) -> None:
    registration = register(make_activity(), "Cy", attended=True)
    evaluate(registration, {}, comments="")
    data = registration_services.evaluation_answers(director, registration)
    assert (data["ratings"], data["average"], data["answers"]) == ([], None, [])


def test_a_registration_without_evaluation_raises(make_activity, register, director) -> None:
    registration = register(make_activity(), "No Answer", attended=True)
    with pytest.raises(EvaluationSubmission.DoesNotExist):
        registration_services.evaluation_answers(director, registration)


# --- The dialog ----------------------------------------------------------------------------------------------------


def visible(html: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", html))


def test_the_dialog_shows_the_answers_and_the_pdf_button(submitted, director, login) -> None:
    html = login(director).get(dialog_url(submitted)).content.decode()
    assert "Evaluation answers" in html and 'id="evaluation-answers"' in html
    assert f'href="{pdf_url(submitted)}"' in html
    text = visible(html)
    assert "Name on the certificate: Ana Cruz" in text and "Participant category: Incubatees" in text
    assert "Submitted " in text and "Average rating: 4.50 out of 5" in text
    assert "Overall rating" in text and "4/5" in text and "4 out of 5" in text and "5 out of 5" in text
    assert "How to open a pitch" in html and "More examples please" in html
    assert "Anything else?" not in html  # empty answers are skipped


def test_the_rating_dots_are_hidden_from_screen_readers(submitted, director, login) -> None:
    html = login(director).get(dialog_url(submitted)).content.decode()
    row = html[html.index("Overall rating"):html.index("Venue rating")]
    assert row.count("rounded-full bg-primary") == 4 and row.count("rounded-full bg-slate-300") == 1
    assert '<span class="flex gap-1" aria-hidden="true">' in row and '<span class="sr-only">4 out of 5</span>' in row


def test_the_top_shows_no_contact_details_and_the_section_is_closed(submitted, director, login) -> None:
    html = login(director).get(dialog_url(submitted)).content.decode()
    facts = html[html.index('id="registration-facts"'):html.index("</dl>", html.index('id="registration-facts"'))]
    assert "Participant category" in facts and "Registered" in facts and "Attendance" in facts and "Evaluation" in facts
    assert "ana.private@example.com" not in facts and "Contact number" not in facts
    section = html[html.index("<details"):]
    assert section.startswith('<details class="group mt-6 rounded-2xl border border-line" id="registration-answers">')  # closed
    assert "Show contact details and registration answers" in section
    assert "ana.private@example.com" in section and "Contact number" in section and "Registration answers" in section
    assert html.index('id="evaluation"') < html.index('id="registration-answers"')


def test_without_an_evaluation_the_section_is_open(make_activity, register, director, login) -> None:
    registration = register(make_activity(), "No Answer", attended=True, email="no.answer@example.com")
    html = login(director).get(dialog_url(registration)).content.decode()
    assert 'id="registration-answers" open>' in html and "no.answer@example.com" in html
    assert 'id="evaluation"' not in html


@pytest.mark.parametrize("role", ["view_only", "System Administrator"])
def test_without_can_create_activity_the_dialog_hides_the_answers(submitted, role, view_only, make_member, login) -> None:
    member = view_only if role == "view_only" else make_member(role)
    response = login(member).get(dialog_url(submitted))
    html = response.content.decode()
    assert response.status_code == 200
    assert "Only staff who can create activities see a participant's evaluation answers." in html
    assert 'id="registration-answers" open>' in html  # nothing else to read: the section stays open
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
    html = fake_pdf_renderer.last
    assert "size: A4 portrait" in html  # one person's form: portrait (the reports stay landscape)
    text = fake_pdf_renderer.last_text()
    assert "Evaluation - Pitching Clinic" in text and "Ana Cruz" in text and "Participant category: Incubatees" in text
    assert "Submitted " in text and "Average rating: 4.50 out of 5" in text
    assert "Overall rating ● ● ● ● ○ 4/5" in text and "Venue rating ● ● ● ● ● 5/5" in text
    assert "Key learnings How to open a pitch" in text and "Comments More examples please" in text
    assert "Anything else?" not in text and "Registered as" not in text  # same name on the certificate
    assert "ana.private@example.com" not in html and "Contact" not in text


def test_the_pdf_says_registered_as_when_the_certificate_name_differs(submitted, director, login, fake_pdf_renderer) -> None:
    EvaluationSubmission.objects.filter(registration=submitted).update(certificate_name="Ana M. Cruz")
    login(director).get(pdf_url(submitted))
    text = fake_pdf_renderer.last_text()
    assert "Ana M. Cruz" in text and "Registered as: Ana Cruz" in text


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
