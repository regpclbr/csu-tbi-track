"""The general evaluation link (/evaluation/<id>/, ASSUMPTIONS 334-337): one link per activity that emails a
registration's personal evaluation link. It never reveals who attended: every request ends on the same message.
Also: the participant category on the registration page (ASSUMPTIONS 331) and the email-free security log."""

import re
from datetime import timedelta

import pytest
from django.core import mail
from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.urls import reverse

from activities import services as activity_services
from activities.models import ParticipantCategory
from registrations import services
from registrations.models import EvaluationSubmission, Registration

from .conftest import CERT, evaluation_answers, opens_at
from .test_public_pages import registration_post

pytestmark = pytest.mark.django_db

IP = "203.0.113.7"


def page(activity) -> str:
    return reverse("evaluation_request", args=[activity.pk])


def sent_page(activity) -> str:
    return reverse("evaluation_request_sent", args=[activity.pk])


def html(response) -> str:
    return response.content.decode()


@pytest.fixture
def ongoing(attended, signup, scheduled, clock):
    """Ongoing activity, evaluation open (clock: 1 hour after 00:00 Manila on the end date). person0 and person1
    attended; absent@ registered but was not marked; done@ attended and has already evaluated."""
    absent = signup(scheduled, "absent@example.com")
    attendee, done = attended(2)
    clock(opens_at(scheduled) + timedelta(hours=1))
    services.submit_evaluation(services.evaluation_token(done), answers=evaluation_answers(scheduled), **CERT)
    mail.outbox.clear()
    return {"activity": scheduled, "attendee": attendee, "absent": absent, "done": done}


def ask(client, activity, email: str, ip: str = IP, **extra):
    data = {"email": email, services.HONEYPOT_FIELD: "", **extra}
    return client.post(page(activity), data, REMOTE_ADDR=ip)


def evaluation_emails() -> list:
    return [message for message in mail.outbox if message.subject.startswith("Please evaluate")]


# --- The service ---------------------------------------------------------------------------------------------


def test_an_attendee_gets_one_email_with_their_personal_link(ongoing, django_capture_on_commit_callbacks) -> None:
    attendee = ongoing["attendee"]
    with django_capture_on_commit_callbacks(execute=True):
        assert services.request_evaluation_link(ongoing["activity"], "  PERSON0@Example.com ") is None  # never says who
    [message] = evaluation_emails()
    assert message.to == [attendee.email]
    assert services.evaluation_link(attendee) in message.body  # the existing personal link, unchanged


@pytest.mark.parametrize("who", ["absent", "done", "unknown"])
def test_nobody_else_gets_an_email(ongoing, who, django_capture_on_commit_callbacks) -> None:
    email = {"absent": "absent@example.com", "done": ongoing["done"].email, "unknown": "nobody@example.com"}[who]
    with django_capture_on_commit_callbacks(execute=True):
        services.request_evaluation_link(ongoing["activity"], email)
    assert evaluation_emails() == []


def test_an_attendee_of_another_activity_gets_nothing(attended, scheduled, director, valid_data, with_forms, move_to,
                                                      clock, django_capture_on_commit_callbacks) -> None:
    attended(1)  # person0 attended `scheduled`
    other = with_forms(activity_services.create_activity(director, valid_data(title="Other")))
    move_to(other, "Ongoing")
    clock(opens_at(other) + timedelta(hours=1))
    with django_capture_on_commit_callbacks(execute=True):
        services.request_evaluation_link(other, "person0@example.com")
    assert evaluation_emails() == []


def test_one_registration_per_email_per_activity_is_enforced_by_the_database(ongoing) -> None:
    """The constraint request_evaluation_link relies on (it still handles every match, see the next test)."""
    attendee = ongoing["attendee"]
    with pytest.raises(IntegrityError), transaction.atomic():
        Registration.objects.create(activity=attendee.activity, template=attendee.template,
                                    participant_category=attendee.participant_category, name="Twin",
                                    email=" " + attendee.email.upper() + " ")


class _TwoMatches:
    """Recipients whose email filter matches two registrations: what a duplicate email would look like."""

    def __init__(self, ids: list[int]):
        self.ids = ids

    def filter(self, **kwargs):
        return Registration.objects.filter(pk__in=self.ids)


def test_every_eligible_registration_with_that_email_is_queued(ongoing, monkeypatch, django_capture_on_commit_callbacks) -> None:
    """If more than one registration ever matched (the database prevents it, see above), each gets its own link."""
    queued = []
    monkeypatch.setattr(services, "queue_evaluation_link", queued.append)
    ids = [ongoing["attendee"].pk, ongoing["absent"].pk]
    monkeypatch.setattr(services, "evaluation_link_recipients", lambda activity: _TwoMatches(ids))
    with django_capture_on_commit_callbacks(execute=True):
        services.request_evaluation_link(ongoing["activity"], "person0@example.com")
    assert queued == sorted(ids)


def test_invalid_email_is_a_field_error(ongoing) -> None:
    with pytest.raises(ValidationError) as excinfo:
        services.request_evaluation_link(ongoing["activity"], "not-an-email")
    assert excinfo.value.message_dict == {"email": ["Enter a valid email address."]}


def test_not_open_before_midnight_manila_on_the_end_date(attended, scheduled, clock, django_capture_on_commit_callbacks) -> None:
    attended(1)
    clock(opens_at(scheduled) - timedelta(seconds=1))  # 23:59:59 Manila the day before the end date
    state = services.evaluation_request_state(scheduled)
    assert state.kind == "not_yet_open" and state.reason.startswith("The evaluation opens on")
    with pytest.raises(services.RegistrationError), django_capture_on_commit_callbacks(execute=True):
        services.request_evaluation_link(scheduled, "person0@example.com")
    assert evaluation_emails() == []
    clock(opens_at(scheduled))  # 00:00 Manila on the end date: open
    assert services.evaluation_request_state(scheduled).kind == "open"


def test_closed_after_the_deadline_and_not_open_without_a_form(attended, scheduled, clock) -> None:
    attended(1)
    clock(scheduled.evaluation_deadline + timedelta(minutes=1))
    assert services.evaluation_request_state(scheduled).kind == "closed"
    clock(opens_at(scheduled) + timedelta(hours=1))
    scheduled.form_templates.filter(form_type="evaluation").delete()  # test setup only
    state = services.evaluation_request_state(scheduled)
    assert (state.kind, state.reason) == ("not_yet_open", services.EVALUATION_REQUEST_NOT_READY)


# --- The page ------------------------------------------------------------------------------------------------


@pytest.mark.parametrize("email", ["person0@example.com", "absent@example.com", "done", "nobody@example.com"])
def test_page_always_answers_the_same(client, ongoing, email, django_capture_on_commit_callbacks) -> None:
    if email == "done":
        email = ongoing["done"].email
    with django_capture_on_commit_callbacks(execute=True):
        response = ask(client, ongoing["activity"], email)
    assert response.status_code == 302 and response["Location"] == sent_page(ongoing["activity"])
    sent = client.get(response["Location"])
    assert sent.status_code == 200 and services.EVALUATION_REQUEST_SENT.replace("'", "&#x27;") in html(sent)
    assert email not in html(sent) and "Ana Cruz" not in html(sent)
    assert len(evaluation_emails()) == (1 if email == "person0@example.com" else 0)


def test_page_shows_the_form_while_open(client, ongoing) -> None:
    text = html(client.get(page(ongoing["activity"])))
    assert ongoing["activity"].title in text and 'name="email"' in text and "csrfmiddlewaretoken" in text
    assert re.search(r'<div class="hp-field" aria-hidden="true">', text)
    assert "Ana Cruz" not in text and "person0@example.com" not in text  # never who registered


def test_page_invalid_email_shows_a_field_error(client, ongoing) -> None:
    response = ask(client, ongoing["activity"], "nope")
    assert response.status_code == 400 and "Enter a valid email address." in html(response)


def test_page_before_opening_says_not_open_yet(client, attended, scheduled, clock) -> None:
    attended(1)
    clock(opens_at(scheduled) - timedelta(minutes=1))
    response = client.get(page(scheduled))
    text = html(response)
    assert response.status_code == 200 and "The evaluation is not open yet" in text and 'name="email"' not in text
    assert ask(client, scheduled, "person0@example.com").status_code == 200  # a POST sends nothing either
    assert evaluation_emails() == []


def test_page_is_404_for_draft_and_unknown(client, new_activity) -> None:
    assert client.get(page(new_activity)).status_code == 404
    assert client.get(sent_page(new_activity)).status_code == 404
    assert client.get(reverse("evaluation_request", args=[999999])).status_code == 404


def test_every_public_page_is_404_for_a_draft(client, new_activity) -> None:
    for url in (reverse("register", args=[new_activity.pk]), reverse("register_done", args=[new_activity.pk]),
                reverse("register_calendar", args=[new_activity.pk]), reverse("api_register", args=[new_activity.pk]),
                page(new_activity), sent_page(new_activity)):
        assert client.get(url).status_code == 404, url
    assert new_activity.title not in html(client.get(reverse("register_list")))


# --- Rate limit and honeypot: the same message, no email, never the email in the log ------------------------


def test_rate_limit_per_email(client, ongoing, settings, caplog, django_capture_on_commit_callbacks) -> None:
    settings.EVALUATION_REQUEST_MAX_PER_EMAIL = 2
    activity = ongoing["activity"]
    with django_capture_on_commit_callbacks(execute=True):
        for n in range(2):
            ask(client, activity, "person0@example.com", ip=f"198.51.100.{n}")
    assert len(evaluation_emails()) == 2
    with caplog.at_level("WARNING", logger="accounts.security"), django_capture_on_commit_callbacks(execute=True):
        response = ask(client, activity, "PERSON0@example.com", ip="198.51.100.9")
    assert response.status_code == 302 and response["Location"] == sent_page(activity)  # the same answer
    assert len(evaluation_emails()) == 2  # no third email
    assert "Evaluation link request rate limit reached (ip=198.51.100.9, by email)." in caplog.text
    assert "person0" not in caplog.text.lower()


def test_rate_limit_per_ip(client, ongoing, settings, caplog, django_capture_on_commit_callbacks) -> None:
    settings.EVALUATION_REQUEST_MAX_PER_IP = 3
    activity = ongoing["activity"]
    for n in range(3):
        ask(client, activity, f"guess{n}@example.com")
    with caplog.at_level("WARNING", logger="accounts.security"), django_capture_on_commit_callbacks(execute=True):
        response = ask(client, activity, "person0@example.com")
    assert response.status_code == 302 and response["Location"] == sent_page(activity)
    assert evaluation_emails() == []  # over the limit: nothing sent, even for an attendee
    assert f"Evaluation link request rate limit reached (ip={IP}, by ip)." in caplog.text
    assert "person0@example.com" not in caplog.text


def test_honeypot_sends_nothing_and_logs_only_the_ip(client, ongoing, caplog, django_capture_on_commit_callbacks) -> None:
    with caplog.at_level("WARNING", logger="accounts.security"), django_capture_on_commit_callbacks(execute=True):
        response = ask(client, ongoing["activity"], "person0@example.com", **{services.HONEYPOT_FIELD: "http://spam"})
    assert response.status_code == 302 and evaluation_emails() == []
    assert f"Evaluation link request refused: the hidden field was filled in (ip={IP})." in caplog.text
    assert "person0@example.com" not in caplog.text


def test_registration_rate_limit_log_never_has_the_email(settings, caplog) -> None:
    """The public registration guard (item 214): the per-email limit and the honeypot log the IP only."""
    settings.REGISTRATION_MAX_ATTEMPTS_PER_EMAIL = 1
    services.guard_public_registration(IP, "Secret.Person@example.com", "")  # the first attempt passes
    with caplog.at_level("WARNING", logger="accounts.security"):
        with pytest.raises(services.RegistrationRateLimited):
            services.guard_public_registration(IP, "Secret.Person@example.com", "")
        with pytest.raises(services.RegistrationError):
            services.guard_public_registration("198.51.100.1", "other@example.com", "filled")
    assert f"(ip={IP}, by email)" in caplog.text and "(ip=198.51.100.1)" in caplog.text
    assert "secret.person" not in caplog.text.lower() and "other@example.com" not in caplog.text


# --- Participant category on the public registration page ---------------------------------------------------


def radios(text: str) -> list[str]:
    return re.findall(r'<input type="radio" name="participant_category"[^>]*>', text)


def test_category_options_come_only_from_the_activity(client, director, scheduled) -> None:
    faculty, msmes = ParticipantCategory.objects.get(name="Faculty"), ParticipantCategory.objects.get(name="MSMEs")
    activity_services.update_activity(director, scheduled, {"participant_categories": [faculty, msmes]})
    ParticipantCategory.objects.create(name="Students")  # exists, but not for this activity
    found = radios(html(client.get(reverse("register", args=[scheduled.pk]))))
    assert sorted(re.search(r'value="(\d+)"', r).group(1) for r in found) == sorted([str(faculty.pk), str(msmes.pk)])
    assert not any(" checked" in r for r in found)  # several: no default


def test_single_category_is_preselected_and_saved(client, scheduled) -> None:
    incubatees = ParticipantCategory.objects.get(name="Incubatees")
    [radio] = radios(html(client.get(reverse("register", args=[scheduled.pk]))))
    assert f'value="{incubatees.pk}"' in radio and " checked" in radio
    response = client.post(reverse("register", args=[scheduled.pk]),
                           registration_post(scheduled, participant_category=str(incubatees.pk)))
    assert response.status_code == 302
    assert Registration.objects.get(email="ana@example.com").participant_category == incubatees


def test_a_foreign_category_posted_on_the_page_is_refused(client, director, scheduled) -> None:
    faculty, msmes = ParticipantCategory.objects.get(name="Faculty"), ParticipantCategory.objects.get(name="MSMEs")
    activity_services.update_activity(director, scheduled, {"participant_categories": [faculty, msmes]})
    other = ParticipantCategory.objects.get(name="Incubatees")
    response = client.post(reverse("register", args=[scheduled.pk]),
                           registration_post(scheduled, participant_category=str(other.pk)))
    assert response.status_code == 400
    assert "Choose one of this activity&#x27;s categories: Faculty, MSMEs." in html(response)
    assert not Registration.objects.exists()


def test_no_evaluation_submission_is_created_by_a_request(ongoing, django_capture_on_commit_callbacks) -> None:
    before = EvaluationSubmission.objects.count()
    with django_capture_on_commit_callbacks(execute=True):
        services.request_evaluation_link(ongoing["activity"], "person0@example.com")
    assert EvaluationSubmission.objects.count() == before  # it only sends the link; the personal flow is unchanged
