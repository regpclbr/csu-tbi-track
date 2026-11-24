"""The evaluation deadline is optional (ASSUMPTIONS 423, NAVIGATU UAT): empty = the evaluation stays open. "No evaluation
= no certificate" stays; the automatic links before the end (item 419) still go out; existing deadlines are kept."""

from datetime import datetime, time, timedelta

import pytest
from django.test import Client
from django.urls import reverse
from django.utils import timezone

from activities import attention, pages
from activities import services as activity_services
from activities.models import FundSource, Activity, ActivityStatus
from certificates import services as certificate_services
from registrations import services
from registrations.models import FormTemplate, FormType, Registration

pytestmark = pytest.mark.django_db


def manila(day, hour=9) -> datetime:
    return timezone.make_aware(datetime.combine(day, time(hour, 0)))


@pytest.fixture
def no_deadline(activity: Activity, member) -> Activity:
    """The root conftest's activity without an evaluation deadline, with an evaluation form."""
    Activity.objects.filter(pk=activity.pk).update(evaluation_deadline=None)
    activity.refresh_from_db()
    FormTemplate.objects.create(activity=activity, form_type=FormType.EVALUATION, title="Evaluation", created_by=member)
    return activity


def test_the_service_accepts_an_empty_deadline_and_still_checks_a_given_one(make_member) -> None:
    director = make_member("Director")
    start = timezone.localdate() + timedelta(days=30)
    from activities.tests.conftest import at
    from activities.models import ActivityType, ParticipantCategory
    from decimal import Decimal

    values = {
        "title": "Open evaluation", "description": "", "activity_type": ActivityType.objects.get(name="Workshop"),
        "participant_categories": [ParticipantCategory.objects.get(name="Faculty")], "start_date": start,
        "end_date": start, "start_time": time(8), "end_time": time(17), "venue": "Hall", "max_slots": 10,
        "registration_deadline": at(start - timedelta(days=1)), "evaluation_deadline": None,
        "fund_source": FundSource.objects.get(code="RAF"),  # ASSUMPTIONS 433
    }
    activity = activity_services.create_activity(director, values)
    assert activity.evaluation_deadline is None
    with pytest.raises(Exception) as excinfo:
        activity_services.update_activity(director, activity, {"evaluation_deadline": at(start - timedelta(days=1))})
    assert "Evaluation deadline must be on or after the end date." in str(excinfo.value)


def test_the_window_never_closes_without_a_deadline(no_deadline) -> None:
    opens, closes = services.evaluation_window(no_deadline)
    assert closes is None
    years_later = manila(no_deadline.end_date + timedelta(days=3650))
    assert not services.evaluation_has_closed(no_deadline, years_later)
    assert services.evaluation_request_state(no_deadline, years_later).kind == "open"


def test_an_attendee_can_still_evaluate_long_after(no_deadline, registration) -> None:
    Registration.objects.filter(pk=registration.pk).update(attendance_confirmed=True)
    registration.refresh_from_db()
    state = services.evaluation_state(registration, manila(no_deadline.end_date + timedelta(days=400)))
    assert state.kind == "open" and state.shows_person


def test_existing_deadlines_keep_closing(activity, registration) -> None:
    assert activity.evaluation_deadline is not None  # existing rows keep their deadline (no data change)
    Registration.objects.filter(pk=registration.pk).update(attendance_confirmed=True)
    registration.refresh_from_db()
    later = activity.evaluation_deadline + timedelta(minutes=1)
    assert services.evaluation_has_closed(activity, later)


def test_automatic_links_still_go_out_before_the_end(no_deadline) -> None:
    ongoing, _ = ActivityStatus.objects.get_or_create(name=ActivityStatus.ONGOING)
    Activity.objects.filter(pk=no_deadline.pk).update(status=ongoing)
    moment = services.automatic_link_moment(no_deadline)
    assert no_deadline.pk in [a.pk for a in services.automatic_link_activities(moment + timedelta(minutes=1))]
    assert no_deadline.pk not in [a.pk for a in services.automatic_link_activities(moment - timedelta(minutes=1))]


def test_no_late_attendance_warning_without_a_deadline(no_deadline, registration) -> None:
    Registration.objects.filter(pk=registration.pk).update(
        attendance_confirmed=True, attendance_marked_at=manila(no_deadline.end_date + timedelta(days=60)))
    texts = [item.text for item in attention.activity_attention(no_deadline)]
    assert attention.ATTENDANCE_AFTER_EVALUATION not in texts


def test_certificate_waits_for_the_evaluation_and_never_closes(make_certificate, no_deadline, registration) -> None:
    Registration.objects.filter(pk=registration.pk).update(attendance_confirmed=True)
    certificate = make_certificate(registration=registration)
    late = manila(no_deadline.end_date + timedelta(days=900))
    label = certificate_services.readiness(certificate, now=late)
    assert label != certificate_services.EVALUATION_CLOSED  # "No evaluation = no certificate": it keeps waiting


def test_overview_and_evaluation_tab_say_it_stays_open(no_deadline, make_member) -> None:
    rows = dict(pages.detail_rows(activity_services.get_activity_detail(no_deadline)))
    assert rows["Evaluation deadline"] == pages.NO_EVALUATION_DEADLINE
    client = Client()
    client.force_login(make_member("Director"))
    html = client.get(f"{reverse('activities:detail', args=[no_deadline.pk])}?tab=evaluation").content.decode()
    assert "stays open (no evaluation deadline)" in html
    overview = client.get(reverse("activities:detail", args=[no_deadline.pk])).content.decode()
    assert pages.NO_EVALUATION_DEADLINE in overview
