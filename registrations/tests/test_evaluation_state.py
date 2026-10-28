"""Phase F3 read-only helpers: evaluation_state (the one rule behind an evaluation link), check_evaluation (the one
check of an evaluation's contents, used by the review step and by submit_evaluation) and registration_page_activity."""

from datetime import timedelta

import pytest
from django.core.exceptions import ValidationError

from activities import services as activity_services
from registrations import services
from registrations.models import EvaluationSubmission, FormType

from .conftest import CERT, evaluation_answers, field_id, form_of, opens_at

pytestmark = pytest.mark.django_db


def state(person, activity, hours_after_opening: float = 1):
    return services.evaluation_state(person, now=opens_at(activity) + timedelta(hours=hours_after_opening))


def test_open_state(attended, scheduled) -> None:
    person = attended(1)[0]
    result = state(person, scheduled)
    assert (result.kind, result.reason, result.shows_person) == ("open", None, True)


def test_not_yet_open_still_shows_the_person(attended, scheduled) -> None:
    person = attended(1)[0]
    result = state(person, scheduled, hours_after_opening=-1)
    assert result.kind == "not_yet_open" and result.reason.startswith("The evaluation opens on") and result.shows_person


def test_closed_hides_the_person(attended, scheduled) -> None:
    person = attended(1)[0]
    after = scheduled.evaluation_deadline + timedelta(minutes=1)
    result = services.evaluation_state(person, now=after)
    assert result.kind == "closed" and result.reason.startswith("The evaluation closed on") and not result.shows_person


def test_submitted_hides_the_person(attended, scheduled) -> None:
    person = attended(1)[0]
    services.submit_evaluation(services.evaluation_token(person), **CERT, answers=evaluation_answers(scheduled),
                               now=opens_at(scheduled) + timedelta(hours=1))
    result = state(person, scheduled)
    assert (result.kind, result.reason, result.shows_person) == ("submitted", services.ALREADY_SUBMITTED, False)


def test_not_attended_comes_first(scheduled, signup, director) -> None:
    person = signup(scheduled, "absent@example.com")
    activity_services.change_status(director, scheduled, "Ongoing")
    result = state(person, scheduled, hours_after_opening=-1)  # also not open yet: attendance is checked first
    assert result.kind == "not_attended" and result.reason == services.NOT_ATTENDED


def test_no_form(attended, scheduled) -> None:
    person = attended(1)[0]
    form_of(scheduled, FormType.EVALUATION).delete()
    result = state(person, scheduled)
    assert result.kind == "no_form" and result.reason == services.EVALUATION_FORM_NOT_READY


def test_closed_reason_and_api_page_follow_the_state(attended, scheduled) -> None:
    person = attended(1)[0]
    token = services.evaluation_token(person)
    for hours in (-1, 1):
        now = opens_at(scheduled) + timedelta(hours=hours)
        assert services.evaluation_closed_reason(person, now) == services.evaluation_state(person, now).reason
        assert services.evaluation_page(token, now)["closed_reason"] == services.evaluation_state(person, now).reason
    late = scheduled.evaluation_deadline + timedelta(minutes=1)
    assert set(services.evaluation_page(token, late)) == {"is_open", "closed_reason"}  # unchanged API


def test_check_evaluation_cleans_and_saves_nothing(attended, scheduled) -> None:
    attended(1)
    form = form_of(scheduled, FormType.EVALUATION)
    name, email, stored = services.check_evaluation(form, "  Ana  Cruz ", " Ana@Example.COM ", evaluation_answers(scheduled))
    assert (name, email) == ("Ana  Cruz", "ana@example.com")
    assert {field.field_label: value for field, value in stored.items()}["Overall rating"] == "5"
    assert not EvaluationSubmission.objects.exists()


def test_check_evaluation_reports_every_error_together(attended, scheduled) -> None:
    attended(1)
    form = form_of(scheduled, FormType.EVALUATION)
    answers = evaluation_answers(scheduled) | {field_id(form, "Overall rating"): 9}
    with pytest.raises(ValidationError) as error:
        services.check_evaluation(form, "", "nope", answers)
    assert set(error.value.message_dict) == {"certificate_name", "certificate_email", f"field_{field_id(form, 'Overall rating')}"}


def test_registration_page_activity(scheduled, director, valid_data) -> None:
    draft = activity_services.create_activity(director, valid_data(title="Draft"))
    assert services.registration_page_activity(draft.pk) is None  # Draft: never public
    assert services.registration_page_activity(999999) is None
    assert services.registration_page_activity(scheduled.pk) == scheduled
    activity_services.change_status(director, scheduled, "Ongoing")
    assert services.registration_page_activity(scheduled.pk) == scheduled
    assert services.get_public_activity(scheduled.pk) is None  # the JSON endpoint is unchanged (Scheduled only)
    activity_services.change_status(director, scheduled, "Conducted")
    assert services.registration_page_activity(scheduled.pk) == scheduled
