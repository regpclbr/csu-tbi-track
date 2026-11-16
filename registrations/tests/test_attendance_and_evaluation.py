"""Attendance (staff) and the evaluation flow (personal signed links, the evaluation window)."""

import json
from datetime import timedelta

import pytest
from django.core import mail, signing
from django.core.exceptions import PermissionDenied

from activities import services as activity_services
from registrations import services
from registrations.models import EvaluationSubmission, FormType, Registration

from .conftest import CERT, cat, evaluation_answers, form_of, opens_at

# --- Attendance ------------------------------------------------------------------------------


@pytest.mark.django_db
def test_attendance_only_when_ongoing_or_conducted(director, scheduled, signup, move_to) -> None:
    person = signup(scheduled, "ana@example.com")
    with pytest.raises(services.RegistrationError, match="Attendance opens when the activity starts on"):
        services.set_attendance(director, scheduled, [person.pk], True, "email")

    activity_services.change_status(director, scheduled, "Ongoing")
    assert services.set_attendance(director, scheduled, [person.pk], True, "wet_signature") == 1

    activity_services.change_status(director, scheduled, "Conducted")  # Conducted locks the activity, not attendance
    assert services.set_attendance(director, scheduled, [person.pk], False) == 1


@pytest.mark.django_db
def test_mark_many_and_unmark_clears_method(director, attended) -> None:
    people = attended(3)
    assert all(p.attendance_confirmed and p.confirmation_method == "email" for p in people)
    activity = people[0].activity
    services.set_attendance(director, activity, [people[0].pk, people[1].pk], False)
    statuses = dict(Registration.objects.filter(activity=activity).values_list("email", "confirmation_method"))
    assert statuses == {"person0@example.com": None, "person1@example.com": None, "person2@example.com": "email"}
    assert not Registration.objects.get(pk=people[0].pk).attendance_confirmed


@pytest.mark.django_db
def test_marking_needs_a_valid_method(director, attended) -> None:
    person = attended(1)[0]
    for method in (None, "selfie"):
        with pytest.raises(services.RegistrationError, match="email or wet_signature"):
            services.set_attendance(director, person.activity, [person.pk], True, method)


@pytest.mark.django_db
def test_unmark_refused_after_evaluation(director, attended) -> None:
    person = attended(1)[0]
    activity = person.activity
    services.submit_evaluation(services.evaluation_token(person), **CERT, answers=evaluation_answers(activity), now=opens_at(activity))
    with pytest.raises(services.RegistrationError, match="already submitted the evaluation"):
        services.set_attendance(director, activity, [person.pk], False)
    person.refresh_from_db()
    assert person.attendance_confirmed is True


@pytest.mark.django_db
def test_registrations_of_another_activity_are_refused(director, attended, valid_data, with_forms, move_to) -> None:
    person = attended(1)[0]
    other = with_forms(activity_services.create_activity(director, valid_data(title="Other")))
    move_to(other, "Ongoing")
    with pytest.raises(services.RegistrationError, match="not found for this activity"):
        services.set_attendance(director, other, [person.pk], True, "email")


@pytest.mark.django_db
def test_view_only_members_cannot_mark_attendance(view_only, attended) -> None:
    person = attended(1)[0]
    with pytest.raises(PermissionDenied):
        services.set_attendance(view_only, person.activity, [person.pk], False)


@pytest.mark.django_db
def test_list_registrations_filters(director, scheduled, signup) -> None:
    activity_services.update_activity(director, scheduled, {"participant_categories": [cat("Faculty"), cat("Incubatees")]})
    ana = signup(scheduled, "ana@example.com", participant_category=cat("Faculty").pk)
    signup(scheduled, "ben@example.com", participant_category=cat("Incubatees").pk)
    activity_services.change_status(director, scheduled, "Ongoing")
    services.set_attendance(director, scheduled, [ana.pk], True, "email")

    emails = lambda **f: [r.email for r in services.list_registrations(scheduled, **f)]  # noqa: E731
    assert emails() == ["ana@example.com", "ben@example.com"]
    assert emails(attended=True) == ["ana@example.com"]
    assert emails(attended=False) == ["ben@example.com"]
    assert emails(participant_category_id=cat("Incubatees").pk) == ["ben@example.com"]


# --- The evaluation window ----------------------------------------------------------------


@pytest.mark.django_db
def test_evaluation_window_edges(attended) -> None:
    person = attended(1)[0]
    activity = person.activity
    opens, closes = services.evaluation_window(activity)
    assert opens == opens_at(activity) and closes == activity.evaluation_deadline

    minute = timedelta(minutes=1)
    assert "opens on" in services.evaluation_closed_reason(person, now=opens - minute)
    assert services.evaluation_closed_reason(person, now=opens) is None  # 00:00 Manila on the end date
    assert services.evaluation_closed_reason(person, now=closes) is None  # at the deadline: still open
    assert "closed on" in services.evaluation_closed_reason(person, now=closes + minute)

    token = services.evaluation_token(person)
    with pytest.raises(services.RegistrationError, match="opens on"):
        services.submit_evaluation(token, **CERT, answers=evaluation_answers(activity), now=opens - minute)
    with pytest.raises(services.RegistrationError, match="closed on"):
        services.submit_evaluation(token, **CERT, answers=evaluation_answers(activity), now=closes + minute)
    assert not EvaluationSubmission.objects.exists()


@pytest.mark.django_db
def test_evaluation_only_for_attendees(scheduled, signup, director) -> None:
    person = signup(scheduled, "ana@example.com")
    activity_services.change_status(director, scheduled, "Ongoing")
    with pytest.raises(services.RegistrationError, match="Only people marked as attended"):
        services.submit_evaluation(
            services.evaluation_token(person), **CERT, answers=evaluation_answers(scheduled), now=opens_at(scheduled)
        )


@pytest.mark.django_db
def test_submit_evaluation_once(attended) -> None:
    person = attended(1)[0]
    activity = person.activity
    token = services.evaluation_token(person)
    submission = services.submit_evaluation(token, comments=" Great! ", **CERT, answers=evaluation_answers(activity), now=opens_at(activity))
    assert submission.registration == person and submission.comments == "Great!"  # never anonymous
    assert submission.template == form_of(activity, FormType.EVALUATION)
    assert submission.answers.count() == 4  # the optional "Suggestions" was left empty

    with pytest.raises(services.RegistrationError, match="already submitted"):
        services.submit_evaluation(token, **CERT, answers=evaluation_answers(activity), now=opens_at(activity))


@pytest.mark.django_db
def test_evaluation_and_answers_are_saved_together(attended, monkeypatch) -> None:
    from registrations.models import FormAnswer

    person = attended(1)[0]

    def fail(**kwargs):
        raise RuntimeError("database error")

    monkeypatch.setattr(FormAnswer.objects, "create", fail)
    with pytest.raises(RuntimeError):
        services.submit_evaluation(
            services.evaluation_token(person), **CERT, answers=evaluation_answers(person.activity), now=opens_at(person.activity)
        )
    assert not EvaluationSubmission.objects.exists()


# --- Personal signed links -------------------------------------------------------------


@pytest.mark.django_db
def test_link_identifies_the_registration(attended) -> None:
    person = attended(1)[0]
    assert services.registration_from_token(services.evaluation_token(person)) == person
    assert services.evaluation_link(person).startswith("http://127.0.0.1:8000/evaluate/")


@pytest.mark.django_db
def test_tampered_or_invalid_links_are_refused(attended) -> None:
    person, other = attended(2)
    token = services.evaluation_token(person)
    assert services.evaluation_token(person) == token  # the same person always gets the same link
    value, signature = token.rsplit(":", 1)
    other_value = services.evaluation_token(other).rsplit(":", 1)[0]
    evaluation_signer = signing.Signer(salt=services.EVALUATION_SALT)
    tampered = [
        token[:-1] + ("A" if token[-1] != "A" else "B"),  # signature changed
        f"{other_value}:{signature}",  # someone else's id with this person's signature
        signing.Signer(salt="some-other-salt").sign_object(other.pk),  # signed, but not as an evaluation link
        "not-a-token",
        "",
        evaluation_signer.sign_object(999999),  # valid signature, unknown registration
    ]
    for bad in tampered:
        with pytest.raises(services.RegistrationError, match="not valid"):
            services.registration_from_token(bad)
    assert value


@pytest.mark.django_db
def test_tampered_link_endpoint(client, attended) -> None:
    person = attended(1)[0]
    response = client.get(f"/api/evaluate/{services.evaluation_token(person)}x/")
    assert response.status_code == 400
    assert json.loads(response.content) == {"error": "This evaluation link is not valid."}


@pytest.mark.django_db
def test_evaluate_endpoint(client, attended, monkeypatch) -> None:
    person = attended(1)[0]
    activity = person.activity
    monkeypatch.setattr(services.timezone, "now", lambda: opens_at(activity) + timedelta(hours=9))
    url = f"/api/evaluate/{services.evaluation_token(person)}/"

    page = json.loads(client.get(url).content)["evaluation"]
    assert page["is_open"] is True and page["name"] == person.name
    assert len(page["fields"]) == 5

    payload = {"comments": "Thanks", **CERT, "answers": evaluation_answers(activity), "privacy_consent": True}
    response = client.post(url, json.dumps(payload), content_type="application/json")
    assert response.status_code == 201
    assert json.loads(client.get(url).content)["evaluation"]["closed_reason"] == (
        "You have already submitted your evaluation. Thank you!"
    )


# --- Sending the links -----------------------------------------------------------------


@pytest.mark.django_db
def test_send_links_only_to_attendees_who_have_not_evaluated(director, attended, signup) -> None:
    done, waiting = attended(2)
    activity = done.activity
    no_show = Registration.objects.create(
        activity=activity, template=done.template, participant_category=done.participant_category,
        name="No Show", email="noshow@example.com",
    )
    now = opens_at(activity)
    services.submit_evaluation(services.evaluation_token(done), **CERT, answers=evaluation_answers(activity), now=now)

    assert services.send_evaluation_links(director, activity, now=now) == 1
    assert [m.to for m in mail.outbox] == [[waiting.email]]
    assert services.evaluation_link(waiting) in mail.outbox[0].body
    assert no_show.email not in json.dumps([m.to for m in mail.outbox])


@pytest.mark.django_db
def test_send_links_refused_outside_the_window(director, attended) -> None:
    activity = attended(1)[0].activity
    opens, closes = services.evaluation_window(activity)
    with pytest.raises(services.RegistrationError, match="opens on"):
        services.send_evaluation_links(director, activity, now=opens - timedelta(minutes=1))
    with pytest.raises(services.RegistrationError, match="closed on"):
        services.send_evaluation_links(director, activity, now=closes + timedelta(minutes=1))
    assert mail.outbox == []


@pytest.mark.django_db
def test_send_links_refused_without_an_evaluation_form(director, attended) -> None:
    activity = attended(1)[0].activity
    form_of(activity, FormType.EVALUATION).delete()
    with pytest.raises(services.RegistrationError, match="no evaluation form"):
        services.send_evaluation_links(director, activity, now=opens_at(activity))


@pytest.mark.django_db
def test_view_only_members_cannot_send_links(view_only, attended) -> None:
    activity = attended(1)[0].activity
    with pytest.raises(PermissionDenied):
        services.send_evaluation_links(view_only, activity, now=opens_at(activity))
