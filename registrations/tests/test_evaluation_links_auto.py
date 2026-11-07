"""Evaluation links (ASSUMPTIONS 348): sent automatically once per attendee after the activity is Conducted, marked
sent or failed, never retried automatically, resent by staff on the Evaluation tab."""

from datetime import timedelta

import pytest
from django.core import mail
from django.urls import reverse

from activities import services as activity_services
from config.mail import email_sent_message
from registrations import emails, services
from registrations.models import EvaluationSubmission, FormTemplate, FormType, Registration

from .conftest import opens_at

pytestmark = pytest.mark.django_db


@pytest.fixture
def conducted(attended, scheduled, director):
    """A Conducted activity with both forms and three attendees; returns (activity, people, a moment in the window)."""
    people = attended(3)
    activity_services.change_status(director, scheduled, "Conducted")
    return scheduled, people, opens_at(scheduled) + timedelta(hours=20)


def states(people) -> list[tuple[bool, bool]]:
    rows = Registration.objects.filter(pk__in=[p.pk for p in people]).order_by("pk")
    return [(r.evaluation_link_sent_at is not None, r.evaluation_link_failed_at is not None) for r in rows]


def test_automatic_send_reaches_each_attendee_once(conducted) -> None:
    activity, people, now = conducted
    mail.outbox.clear()
    assert services.send_automatic_evaluation_links(now) == {"sent": 3, "failed": 0, "skipped": 0}
    assert len(mail.outbox) == 3 and states(people) == [(True, False)] * 3
    assert services.send_automatic_evaluation_links(now + timedelta(minutes=5)) == {"sent": 0, "failed": 0, "skipped": 0}
    assert len(mail.outbox) == 3


def test_only_conducted_with_a_form_and_an_open_window(attended, scheduled, director) -> None:
    attended(1)
    now = opens_at(scheduled) + timedelta(hours=20)
    mail.outbox.clear()
    assert services.send_automatic_evaluation_links(now)["sent"] == 0  # still Ongoing
    activity_services.change_status(director, scheduled, "Conducted")
    assert services.send_automatic_evaluation_links(scheduled.evaluation_deadline + timedelta(minutes=1))["sent"] == 0  # closed
    FormTemplate.objects.filter(activity=scheduled, form_type=FormType.EVALUATION).delete()
    assert services.send_automatic_evaluation_links(now)["sent"] == 0  # no evaluation form
    assert mail.outbox == []


def test_people_who_evaluated_get_nothing(conducted) -> None:
    activity, people, now = conducted
    EvaluationSubmission.objects.create(template=services.get_form(activity, FormType.EVALUATION), registration=people[0],
                                        certificate_name="A", certificate_email="a@example.com")
    assert services.send_automatic_evaluation_links(now)["sent"] == 2


def test_a_failed_send_is_marked_and_not_retried_automatically(conducted, monkeypatch) -> None:
    activity, people, now = conducted
    real_send, server_down = emails.send_evaluation_link, [True]

    def send(registration, link):
        if server_down[0]:
            raise ConnectionRefusedError("mail server down")
        real_send(registration, link)

    monkeypatch.setattr(emails, "send_evaluation_link", send)
    assert services.send_automatic_evaluation_links(now)["failed"] == 3
    assert states(people) == [(False, True)] * 3
    server_down[0] = False  # the server is back: still nothing is sent automatically
    mail.outbox.clear()
    assert services.send_automatic_evaluation_links(now + timedelta(minutes=5))["sent"] == 0
    assert mail.outbox == []
    assert services.evaluation_link_counts(activity) == {"sent": 0, "failed": 3, "not_sent": 0}


def test_resend_one_person_clears_failed(conducted, director, monkeypatch) -> None:
    activity, people, now = conducted
    Registration.objects.filter(pk=people[0].pk).update(evaluation_link_failed_at=now)
    mail.outbox.clear()
    assert services.send_evaluation_links(director, activity, registration_ids=[people[0].pk], now=now) == 1
    assert len(mail.outbox) == 1 and mail.outbox[0].to == [people[0].email]
    assert states(people)[0] == (True, False)


def test_a_failed_resend_clears_sent(conducted, director, monkeypatch) -> None:
    activity, people, now = conducted
    services.send_automatic_evaluation_links(now)
    def broken(registration, link):
        raise OSError("mail server down")

    monkeypatch.setattr(emails, "send_evaluation_link", broken)
    services.send_evaluation_links(director, activity, registration_ids=[people[0].pk], now=now)
    assert states(people)[0] == (False, True)


def test_resend_rules(conducted, director, admin_staff) -> None:
    activity, people, now = conducted
    EvaluationSubmission.objects.create(template=services.get_form(activity, FormType.EVALUATION), registration=people[0],
                                        certificate_name="A", certificate_email="a@example.com")
    with pytest.raises(services.RegistrationError, match="only to attendees who have not evaluated"):
        services.send_evaluation_links(director, activity, registration_ids=[people[0].pk], now=now)
    with pytest.raises(services.RegistrationError, match="Links cannot be sent now"):
        services.send_evaluation_links(director, activity, now=activity.evaluation_deadline + timedelta(minutes=1))
    from django.core.exceptions import PermissionDenied

    with pytest.raises(PermissionDenied):
        services.send_evaluation_links(admin_staff, activity, now=now)
    assert services.send_evaluation_links(director, activity, now=now) == 2  # everyone who has not answered


def test_the_task_skips_someone_who_evaluated(conducted) -> None:
    from registrations import tasks

    activity, people, now = conducted
    EvaluationSubmission.objects.create(template=services.get_form(activity, FormType.EVALUATION), registration=people[0],
                                        certificate_name="A", certificate_email="a@example.com")
    assert tasks.run_send_evaluation_link(people[0].pk)["result"] == "already submitted"
    assert states(people)[0] == (False, False)


def test_the_activity_task_sends_links_after_conducting(conducted, clock) -> None:
    from activities import tasks

    activity, people, now = conducted
    clock(now)
    mail.outbox.clear()
    result = tasks.run_activity_automation()
    assert result["sent"] == 3 and len(mail.outbox) == 3


def test_evaluation_tab_explains_counts_and_lists_who_has_not_answered(client, director, conducted, settings) -> None:
    settings.MAILERS = {**settings.MAILERS, "default": settings.CONSOLE_MAILER}  # development mode
    activity, people, now = conducted
    Registration.objects.filter(pk=people[0].pk).update(evaluation_link_failed_at=now)
    client.force_login(director)
    html = client.get(reverse("activities:detail", args=[activity.pk]) + "?tab=evaluation").content.decode()
    assert "Who gets the link: everyone marked as attended who has not answered the evaluation yet." in html
    assert "after the activity becomes Conducted" in html and "The evaluation opens on" in html
    assert "Development mode: emails are printed in the terminal" in html
    assert people[0].name in html and "Failed" in html and "Not sent yet" in html
    assert "Resend to all who haven&#x27;t answered" in html or "Resend to all who haven't answered" in html


def test_resend_view_goes_back_to_the_evaluation_tab(client, director, conducted, clock) -> None:
    activity, people, now = conducted
    clock(now)
    client.force_login(director)
    response = client.post(reverse("registrations:send_links", args=[activity.pk]), {"registration_id": people[0].pk}, follow=True)
    assert response.redirect_chain[-1][0].endswith("?tab=evaluation")
    expected = email_sent_message("They go out while qcluster is running.", before="Queued 1 evaluation link email. ")
    assert expected in response.content.decode()


def test_registrations_tab_links_to_the_evaluation_tab(client, director, conducted) -> None:
    activity, people, now = conducted
    client.force_login(director)
    html = client.get(reverse("activities:detail", args=[activity.pk]) + "?tab=registrations").content.decode()
    assert "Evaluation links</a>" in html and reverse("registrations:send_links", args=[activity.pk]) not in html
