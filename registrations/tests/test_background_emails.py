"""Registration emails are Django-Q2 tasks: a mail-server failure never turns a saved registration
(or "send evaluation links") into an error. The root conftest runs these tasks straight away;
tests that check the real queueing put the real queue functions back."""

import json
import smtplib

import pytest
from django.core import mail
from django_q.models import OrmQ

from registrations import emails, services, tasks
from registrations.models import Registration

from .conftest import CERT, FIXED, evaluation_answers, opens_at, registration_answers
from .conftest import SIGN_JSON, SIGN_POST

pytestmark = pytest.mark.django_db


@pytest.fixture
def smtp_down(monkeypatch):
    """Every registration email fails as if the mail server refused the connection."""

    def refuse(*args, **kwargs):
        raise smtplib.SMTPConnectError(421, "Service not available")

    monkeypatch.setattr(emails, "send_mail", refuse)


@pytest.fixture
def real_queue(monkeypatch, registration_emails_run_now):
    """Use the real Django-Q2 queueing instead of running the tasks straight away."""
    monkeypatch.setattr(services, "queue_confirmation", registration_emails_run_now["confirmation"])
    monkeypatch.setattr(services, "queue_evaluation_link", registration_emails_run_now["evaluation_link"])


def payload(activity, email: str = "ana@example.com") -> str:
    return json.dumps({"name": "Ana Cruz", "email": email, **FIXED, "answers": registration_answers(activity),
                       "privacy_consent": True, "signature": SIGN_JSON})


# --- Registration confirmation ----------------------------------------------------------------------------


def test_registration_is_saved_and_201_even_when_the_email_fails(client, scheduled, smtp_down, caplog, django_capture_on_commit_callbacks) -> None:
    with caplog.at_level("ERROR", logger="registrations"), django_capture_on_commit_callbacks(execute=True):
        response = client.post(f"/api/register/{scheduled.pk}/", payload(scheduled), content_type="application/json")
    assert response.status_code == 201
    registration = Registration.objects.get()
    assert registration.email == "ana@example.com" and mail.outbox == []
    assert f"Registration confirmation {registration.pk} was not sent: SMTPConnectError." in caplog.text
    assert "ana@example.com" not in caplog.text  # never the address


def test_confirmation_is_queued_as_a_task_after_the_commit(scheduled, signup, real_queue, django_capture_on_commit_callbacks) -> None:
    with django_capture_on_commit_callbacks(execute=False) as callbacks:
        registration = signup(scheduled, "ana@example.com")
    assert OrmQ.objects.count() == 0  # nothing before the commit
    for callback in callbacks:
        callback()
    [queued] = OrmQ.objects.all()
    assert queued.task["func"] == services.CONFIRMATION_TASK and queued.task["args"] == (registration.pk,)
    assert mail.outbox == []  # qcluster sends it, not the request


def test_confirmation_task_sends_the_email(scheduled, signup) -> None:
    registration = signup(scheduled, "ana@example.com")
    assert tasks.run_send_registration_confirmation(registration.pk) == {"registration_id": registration.pk, "result": "sent"}
    assert mail.outbox[-1].to == ["ana@example.com"]
    assert tasks.run_send_registration_confirmation(999999)["result"] == "not found"


# --- Evaluation links -----------------------------------------------------------------------------------------


def test_send_links_queues_one_task_per_attendee(director, attended, real_queue) -> None:
    people = attended(3)
    activity = people[0].activity
    assert services.send_evaluation_links(director, activity, now=opens_at(activity)) == 3
    queued = sorted(q.task["args"][0] for q in OrmQ.objects.all())
    assert queued == sorted(p.pk for p in people)
    assert {q.task["func"] for q in OrmQ.objects.all()} == {services.EVALUATION_LINK_TASK}
    assert mail.outbox == []


def test_send_links_never_fails_when_the_mail_server_is_down(client, director, attended, smtp_down, caplog, monkeypatch) -> None:
    people = attended(2)
    activity = people[0].activity
    monkeypatch.setattr(services.timezone, "now", lambda: opens_at(activity))
    client.force_login(director)
    with caplog.at_level("ERROR", logger="registrations"):
        response = client.post(f"/registrations/api/activities/{activity.pk}/send-evaluation-links/")
    assert response.status_code == 200 and response.json() == {"queued": 2}
    for person in people:
        assert f"Evaluation link for registration {person.pk} was not sent: SMTPConnectError." in caplog.text
        assert person.email not in caplog.text
    assert "/evaluate/" not in caplog.text  # never the link


def test_link_task_skips_someone_who_submitted_meanwhile(attended) -> None:
    person = attended(1)[0]
    activity = person.activity
    services.submit_evaluation(services.evaluation_token(person), **CERT, answers=evaluation_answers(activity), now=opens_at(activity))
    assert tasks.run_send_evaluation_link(person.pk)["result"] == "already submitted"
    assert not [m for m in mail.outbox if m.subject.startswith("Please evaluate")]
