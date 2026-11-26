"""Close / open the evaluation by hand (ASSUMPTIONS 452): activities.services.set_evaluation_closed_by_hand
(can_create_activity or the activity's assignment, Ongoing or Conducted, one History row), read ONLY through
registrations.services.evaluation_has_closed / evaluation_open_q, so links, answers and certificate readiness follow.
Times are passed as `now`; nothing depends on today's date."""

from datetime import datetime, time, timedelta

import pytest
from django.core.exceptions import PermissionDenied
from django.urls import reverse
from django.utils import timezone

from activities import services as activity_services
from activities.models import Activity, ActivityAuditLog
from registrations import services

from .conftest import CERT, evaluation_answers, opens_at

pytestmark = pytest.mark.django_db


def close(member, activity):
    return activity_services.set_evaluation_closed_by_hand(member, activity, closed=True)


def reopen(member, activity):
    return activity_services.set_evaluation_closed_by_hand(member, activity, closed=False)


@pytest.fixture
def ongoing(attended, scheduled):
    """One attendee on an Ongoing activity WITHOUT an evaluation deadline (it would stay open forever, item 423)."""
    person = attended(1)[0]
    Activity.objects.filter(pk=scheduled.pk).update(evaluation_deadline=None)
    scheduled.refresh_from_db()
    return person


def test_closing_works_without_a_deadline_and_refuses_new_answers(director, scheduled, ongoing) -> None:
    now = opens_at(scheduled) + timedelta(hours=10)
    assert not services.evaluation_has_closed(scheduled, now)
    close(director, scheduled)
    scheduled.refresh_from_db()
    assert scheduled.evaluation_closed_by_hand and services.evaluation_has_closed(scheduled, now)
    assert not Activity.objects.filter(services.evaluation_open_q(now), pk=scheduled.pk).exists()  # parity
    state = services.evaluation_state(ongoing, now)
    assert state.reason == services.EVALUATION_CLOSED_BY_TBI
    with pytest.raises(services.RegistrationError, match="closed the evaluation"):
        services.submit_evaluation(services.evaluation_token(ongoing), answers=evaluation_answers(scheduled), **CERT,
                                   now=now)
    assert services.evaluation_links_closed_reason(scheduled, now).endswith(services.EVALUATION_CLOSED_BY_TBI)
    assert services.evaluation_request_state(scheduled, now).kind == "closed"
    row = ActivityAuditLog.objects.filter(activity=scheduled, field_changed=activity_services.EVALUATION_LABEL).get()
    assert (row.new_value, row.changed_by) == (activity_services.EVALUATION_CLOSED_VALUE, director)


def test_opening_again_lets_them_answer_and_writes_history(director, scheduled, ongoing) -> None:
    now = opens_at(scheduled) + timedelta(hours=10)
    close(director, scheduled)
    reopen(director, scheduled)
    scheduled.refresh_from_db()
    assert not services.evaluation_has_closed(scheduled, now)
    services.submit_evaluation(services.evaluation_token(ongoing), answers=evaluation_answers(scheduled), **CERT, now=now)
    values = list(ActivityAuditLog.objects.filter(activity=scheduled, field_changed=activity_services.EVALUATION_LABEL)
                  .order_by("log_id").values_list("old_value", "new_value"))
    assert values[-1] == (activity_services.EVALUATION_CLOSED_VALUE, activity_services.EVALUATION_REOPENED_VALUE)


def test_the_deadline_still_applies_after_opening_again(director, scheduled, attended) -> None:
    attended(1)
    deadline = timezone.make_aware(datetime.combine(scheduled.end_date + timedelta(days=3), time(17)))
    Activity.objects.filter(pk=scheduled.pk).update(evaluation_deadline=deadline)
    close(director, scheduled)
    reopen(director, scheduled)
    scheduled.refresh_from_db()
    assert services.evaluation_has_closed(scheduled, deadline + timedelta(minutes=1))


def test_rules_of_the_switch(director, scheduled, view_only, ongoing) -> None:
    with pytest.raises(PermissionDenied):
        close(view_only, scheduled)
    with pytest.raises(activity_services.ActivityError, match="was not closed by hand"):
        reopen(director, scheduled)
    close(director, scheduled)
    with pytest.raises(activity_services.ActivityError, match="already closed"):
        close(director, scheduled)
    Activity.objects.filter(pk=scheduled.pk).update(
        status=scheduled.status.__class__.objects.get(name="Scheduled"), evaluation_closed_by_hand=False)
    scheduled.refresh_from_db()
    assert activity_services.evaluation_switch(scheduled) is None
    with pytest.raises(activity_services.ActivityError, match="Ongoing or Conducted"):
        close(director, scheduled)


def test_no_evaluation_no_certificate_while_closed(director, scheduled, ongoing) -> None:
    """Attendees who never evaluated stay without a certificate ("Evaluation closed without an answer")."""
    from certificates import services as certificate_services
    from certificates.models import Certificate

    now = opens_at(scheduled) + timedelta(hours=10)
    certificate = Certificate(activity=scheduled, registration=ongoing,
                              recipient_type=Certificate.RecipientType.REGISTRATION, status=Certificate.Status.GENERATED)
    assert not services.evaluation_has_closed(scheduled, now)  # no deadline: it would wait for the evaluation forever
    close(director, scheduled)
    certificate.activity.refresh_from_db()
    assert certificate_services.readiness(certificate, evaluated=False, signatures={}, now=now) == \
        certificate_services.EVALUATION_CLOSED


def test_the_evaluation_tab_switch(client, director, scheduled, ongoing) -> None:
    client.force_login(director)
    tab_url = reverse("activities:detail", args=[scheduled.pk]) + "?tab=evaluation"
    assert "Close the evaluation" in client.get(tab_url).content.decode()
    response = client.post(reverse("activities:evaluation_switch", args=[scheduled.pk]), {"action": "close"})
    assert response.status_code == 302 and response["Location"].endswith("?tab=evaluation")
    page = client.get(tab_url).content.decode()
    assert "Open the evaluation again" in page and "data-evaluation-closed" in page
    assert client.get(reverse("activities:evaluation_switch", args=[scheduled.pk])).status_code == 302  # GET: the tab
