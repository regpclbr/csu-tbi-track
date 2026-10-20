"""Public registration: who can register, the last-slot race, categories, and what the public sees."""

import json
import threading
from datetime import timedelta

import pytest
from django.core import mail
from django.core.exceptions import ValidationError
from django.db import connection

from activities import services as activity_services
from registrations import services
from registrations.models import FormAnswer, FormType, Registration

from .conftest import cat, form_of, registration_answers

# --- Who is visible, and when registration is open ----------------------------------------


@pytest.mark.django_db
@pytest.mark.parametrize("status", ["Draft", "Ongoing", "Conducted"])
def test_only_scheduled_activities_are_public(client, new_activity, with_forms, move_to, status) -> None:
    with_forms(new_activity)
    if status != "Draft":
        move_to(new_activity, status)
    assert services.get_public_activity(new_activity.pk) is None
    assert client.get(f"/register/{new_activity.pk}/").status_code == 404
    assert client.post(f"/register/{new_activity.pk}/", "{}", content_type="application/json").status_code == 404


@pytest.mark.django_db
def test_public_info_shows_slots_and_categories_but_no_registrants(client, scheduled, signup) -> None:
    signup(scheduled, "ana@example.com", name="Ana Cruz")
    body = json.loads(client.get(f"/register/{scheduled.pk}/").content)["activity"]
    assert body["slots_remaining"] == 29 and body["is_open"] is True
    assert body["participant_categories"] == [{"participant_category_id": cat("Incubatees").pk, "name": "Incubatees"}]
    assert [f["field_label"] for f in body["fields"]] == ["Affiliation / Organization", "Data Privacy consent"]
    text = json.dumps(body)
    assert "ana@example.com" not in text and "Ana Cruz" not in text


@pytest.mark.django_db
def test_closed_reasons(director, scheduled, signup, new_activity) -> None:
    deadline = scheduled.registration_deadline
    assert services.registration_closed_reason(scheduled, now=deadline) is None  # at the deadline: open
    assert "deadline" in services.registration_closed_reason(scheduled, now=deadline + timedelta(minutes=1))

    activity_services.update_activity(director, scheduled, {"max_slots": 1})
    signup(scheduled, "first@example.com")
    assert services.registration_closed_reason(scheduled) == "Registration is closed: all slots are taken."
    with pytest.raises(services.RegistrationError, match="all slots are taken"):
        signup(scheduled, "second@example.com")


@pytest.mark.django_db
def test_closed_without_a_registration_form(director, new_activity, move_to) -> None:
    move_to(new_activity, "Scheduled")
    assert "form is not ready" in services.registration_closed_reason(new_activity)


@pytest.mark.django_db
def test_closed_after_the_deadline(scheduled) -> None:
    with pytest.raises(services.RegistrationError, match="deadline"):
        services.register(
            scheduled, name="Late", email="late@example.com", answers=registration_answers(scheduled),
            now=scheduled.registration_deadline + timedelta(seconds=1),
        )


# --- The last slot: two people at the same moment ----------------------------------------


@pytest.mark.django_db(transaction=True)
def test_last_slot_race(director, scheduled) -> None:
    """Two registrations race for the last slot; the activity row lock lets only one in."""
    activity_services.update_activity(director, scheduled, {"max_slots": 1})
    answers = registration_answers(scheduled)
    start = threading.Barrier(2)
    results: list[str] = []

    def attempt(number: int) -> None:
        try:
            start.wait()
            services.register(scheduled, name=f"Racer {number}", email=f"racer{number}@example.com", answers=answers)
            results.append("registered")
        except services.RegistrationError as error:
            results.append(str(error))
        finally:
            connection.close()  # each thread has its own database connection

    threads = [threading.Thread(target=attempt, args=(n,)) for n in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert sorted(results) == ["Registration is closed: all slots are taken.", "registered"]
    assert Registration.objects.filter(activity=scheduled).count() == 1


# --- One registration per email ----------------------------------------------------------


@pytest.mark.django_db
@pytest.mark.parametrize("again", ["ana@example.com", " ANA@Example.com  "])
def test_one_registration_per_email(scheduled, signup, again) -> None:
    signup(scheduled, "ana@example.com")
    with pytest.raises(services.RegistrationError, match="already registered for this activity"):
        signup(scheduled, again)


@pytest.mark.django_db
def test_email_is_stored_trimmed_and_lowercase(scheduled, signup) -> None:
    assert signup(scheduled, "  Ana@Example.COM ").email == "ana@example.com"


@pytest.mark.django_db
def test_name_and_email_are_checked(scheduled) -> None:
    with pytest.raises(ValidationError) as excinfo:
        services.register(scheduled, name=" ", email="not-an-email", answers=registration_answers(scheduled))
    assert set(excinfo.value.message_dict) == {"name", "email"}


# --- Participant category -----------------------------------------------------------------


@pytest.mark.django_db
def test_single_category_is_picked_automatically(scheduled, signup) -> None:
    assert signup(scheduled, "ana@example.com").participant_category == cat("Incubatees")


@pytest.fixture
def two_categories(director, scheduled):
    activity_services.update_activity(director, scheduled, {"participant_categories": [cat("Faculty"), cat("MSMEs")]})
    return scheduled


@pytest.mark.django_db
def test_category_required_when_the_activity_has_several(two_categories, signup) -> None:
    with pytest.raises(ValidationError) as excinfo:
        signup(two_categories, "ana@example.com")
    assert excinfo.value.message_dict["participant_category"] == ["Choose the category you are attending as."]
    assert signup(two_categories, "ana@example.com", participant_category=cat("MSMEs").pk).participant_category == cat("MSMEs")


@pytest.mark.django_db
def test_category_not_linked_to_the_activity_is_refused(two_categories, signup) -> None:
    with pytest.raises(ValidationError) as excinfo:
        signup(two_categories, "ana@example.com", participant_category=cat("Incubatees").pk)
    assert excinfo.value.message_dict["participant_category"] == [
        "Choose one of this activity's categories: Faculty, MSMEs."
    ]


@pytest.mark.django_db
def test_removing_a_category_that_registrations_use_is_refused(director, two_categories, signup) -> None:
    signup(two_categories, "ana@example.com", participant_category=cat("MSMEs").pk)
    with pytest.raises(ValidationError) as excinfo:
        activity_services.update_activity(director, two_categories, {"participant_categories": [cat("Faculty")]})
    assert excinfo.value.message_dict["participant_categories"] == [
        "Cannot remove MSMEs: registrations of this activity use it."
    ]
    # Removing an unused category, or adding one, is fine.
    activity_services.update_activity(director, two_categories, {"participant_categories": [cat("MSMEs"), cat("Incubatees")]})


@pytest.mark.django_db
def test_category_used_by_a_registration_cannot_be_deleted(admin_member, director, scheduled, signup) -> None:
    from activities.models import ActivityParticipantCategory, ParticipantCategory

    alumni = activity_services.save_participant_category(admin_member, ParticipantCategory(), "Alumni")
    activity_services.update_activity(director, scheduled, {"participant_categories": [alumni]})
    signup(scheduled, "ana@example.com")
    ActivityParticipantCategory.objects.filter(participant_category=alumni).delete()  # only the registration remains
    assert activity_services.why_category_cannot_be_deleted(alumni) == 'Cannot delete "Alumni": 1 registration uses it.'


@pytest.mark.django_db
def test_max_slots_cannot_go_below_registrations(director, scheduled, signup) -> None:
    for number in range(3):
        signup(scheduled, f"p{number}@example.com")
    with pytest.raises(ValidationError, match="lower than the 3 people already registered"):
        activity_services.update_activity(director, scheduled, {"max_slots": 2})


# --- Saving: one transaction, confirmation email ---------------------------------------


@pytest.mark.django_db
def test_registration_and_answers_are_saved_together(scheduled, signup, monkeypatch) -> None:
    def fail(**kwargs):
        raise RuntimeError("database error")

    monkeypatch.setattr(FormAnswer.objects, "create", fail)
    with pytest.raises(RuntimeError):
        signup(scheduled, "ana@example.com")
    assert not Registration.objects.exists()


@pytest.mark.django_db
def test_answers_are_saved_and_startup_stays_empty(scheduled, signup) -> None:
    registration = signup(scheduled, "ana@example.com")
    assert registration.startup is None
    assert registration.template == form_of(scheduled)
    assert sorted(a.field.field_label for a in registration.answers.all()) == ["Affiliation / Organization", "Data Privacy consent"]


@pytest.mark.django_db
def test_invalid_answers_are_refused(scheduled) -> None:
    with pytest.raises(ValidationError) as excinfo:
        services.register(scheduled, name="Ana", email="ana@example.com", answers={})
    assert len(excinfo.value.message_dict) == 2  # both default fields are required


@pytest.mark.django_db
def test_confirmation_email(scheduled, signup, django_capture_on_commit_callbacks) -> None:
    with django_capture_on_commit_callbacks(execute=True):
        signup(scheduled, "ana@example.com")
    assert len(mail.outbox) == 1
    message = mail.outbox[0]
    assert message.to == ["ana@example.com"]
    assert "You are registered" in message.subject
    assert "Registered as: Incubatees" in message.body


@pytest.mark.django_db
def test_no_email_when_registration_fails(scheduled, django_capture_on_commit_callbacks) -> None:
    with django_capture_on_commit_callbacks(execute=True), pytest.raises(ValidationError):
        services.register(scheduled, name="Ana", email="ana@example.com", answers={})
    assert mail.outbox == []


# --- Public API -------------------------------------------------------------------------


@pytest.mark.django_db
def test_public_register_endpoint(client, scheduled) -> None:
    payload = {"name": "Ana Cruz", "email": "ana@example.com", "contact_number": "0917", "answers": registration_answers(scheduled)}
    response = client.post(f"/register/{scheduled.pk}/", json.dumps(payload), content_type="application/json")
    assert response.status_code == 201
    assert json.loads(response.content) == {
        "message": "You are registered. A confirmation email is on its way.",
        "participant_category": "Incubatees",
        "slots_remaining": 29,
    }
    again = client.post(f"/register/{scheduled.pk}/", json.dumps(payload), content_type="application/json")
    assert again.status_code == 400
    assert json.loads(again.content) == {"error": "This email is already registered for this activity."}


@pytest.mark.django_db
def test_public_register_shows_field_errors(client, scheduled) -> None:
    response = client.post(f"/register/{scheduled.pk}/", json.dumps({"name": "", "email": "x"}), content_type="application/json")
    assert response.status_code == 400
    assert {"name", "email"} <= set(json.loads(response.content)["errors"])


def test_registration_is_open_rule_is_shared() -> None:
    """The closed reason builds on activities.services.is_registration_open (one rule)."""
    import inspect

    assert "is_registration_open(" in inspect.getsource(services.registration_closed_reason)
