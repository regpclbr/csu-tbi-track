"""Step 9: the evaluation asks for the name printed on the certificate and the address it goes to."""

import pytest
from django.core.exceptions import ValidationError

from registrations import services
from registrations.models import EvaluationSubmission

from .conftest import evaluation_answers, opens_at

pytestmark = pytest.mark.django_db


def submit(person, name, email):
    return services.submit_evaluation(
        services.evaluation_token(person), certificate_name=name, certificate_email=email,
        answers=evaluation_answers(person.activity), now=opens_at(person.activity),
    )


def test_name_is_trimmed_and_keeps_its_capitalization(attended) -> None:
    person = attended(1)[0]
    submission = submit(person, "  Ma. ANA dela Cruz-Reyes, PhD  ", "  Ana.Personal@Example.COM ")
    assert submission.certificate_name == "Ma. ANA dela Cruz-Reyes, PhD"
    assert submission.certificate_email == "ana.personal@example.com"


@pytest.mark.parametrize(
    "name, email, errors",
    [
        ("", "a@example.com", {"certificate_name"}),
        ("   ", "a@example.com", {"certificate_name"}),
        ("x" * 151, "a@example.com", {"certificate_name"}),
        ("Ana", "", {"certificate_email"}),
        ("Ana", "not-an-email", {"certificate_email"}),
        ("Ana", "a@" + "b" * 250 + ".com", {"certificate_email"}),
        ("", "bad", {"certificate_name", "certificate_email"}),
    ],
)
def test_name_and_email_are_checked(attended, name, email, errors) -> None:
    person = attended(1)[0]
    with pytest.raises(ValidationError) as error:
        submit(person, name, email)
    assert set(error.value.message_dict) == errors
    assert not EvaluationSubmission.objects.exists()


def test_name_of_exactly_150_characters_is_accepted(attended) -> None:
    assert len(submit(attended(1)[0], "y" * 150, "a@example.com").certificate_name) == 150


def test_answer_and_certificate_errors_are_reported_together(attended) -> None:
    person = attended(1)[0]
    with pytest.raises(ValidationError) as error:
        services.submit_evaluation(
            services.evaluation_token(person), certificate_name="", certificate_email="a@example.com", answers={},
            now=opens_at(person.activity),
        )
    assert "certificate_name" in error.value.message_dict and len(error.value.message_dict) > 1


def test_evaluation_page_suggests_the_registration_name_and_email(attended, client) -> None:
    person = attended(1)[0]
    page = client.get(f"/evaluate/{services.evaluation_token(person)}/").json()["evaluation"]
    assert page["suggested_certificate_name"] == person.name
    assert page["suggested_certificate_email"] == person.email


def test_public_post_needs_the_certificate_details(attended, client) -> None:
    person = attended(1)[0]
    url = f"/evaluate/{services.evaluation_token(person)}/"
    payload = {"answers": evaluation_answers(person.activity)}
    # The window opens on the end date; these fixtures' activity ends in the future, so freeze "now".
    from unittest import mock

    with mock.patch("registrations.services.timezone.now", return_value=opens_at(person.activity)):
        missing = client.post(url, payload, content_type="application/json")
        assert missing.status_code == 400 and set(missing.json()["errors"]) == {"certificate_name", "certificate_email"}
        done = client.post(url, {**payload, "certificate_name": "Ana", "certificate_email": "ana@x.com"}, content_type="application/json")
    assert done.status_code == 201
    assert EvaluationSubmission.objects.get().certificate_email == "ana@x.com"
