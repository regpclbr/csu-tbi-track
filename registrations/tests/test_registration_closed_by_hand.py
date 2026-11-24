"""Close / reopen registration by hand (ASSUMPTIONS 427): activities.services.set_registration_closed_by_hand
(can_create_activity, Scheduled only, one History row), the one rule in registrations.services.registration_closed_reason
("Registration was closed by the TBI."), the same rule in is_registration_open and the open_for_registration query, the
automatic rules still on top, and the Share card switch."""

from datetime import timedelta

import pytest
from django.core.exceptions import PermissionDenied
from django.test import Client
from django.urls import reverse

from activities import services as activity_services
from activities.models import ActivityAuditLog
from registrations import services

pytestmark = pytest.mark.django_db


def close(member, activity):
    return activity_services.set_registration_closed_by_hand(member, activity, closed=True)


def reopen(member, activity):
    return activity_services.set_registration_closed_by_hand(member, activity, closed=False)


def is_listed(activity, now=None) -> bool:
    return activity_services.open_for_registration(now=now).filter(pk=activity.pk).exists()


def test_closing_stops_registration_everywhere_and_writes_history(director, scheduled, signup) -> None:
    assert services.registration_closed_reason(scheduled) is None and is_listed(scheduled)
    close(director, scheduled)
    scheduled.refresh_from_db()
    assert scheduled.registration_closed_by_hand is True
    assert services.registration_closed_reason(scheduled) == services.REGISTRATION_CLOSED_BY_TBI
    assert not activity_services.is_registration_open(scheduled) and not is_listed(scheduled)  # parity
    assert scheduled not in services.open_activities()  # public list and portal
    with pytest.raises(services.RegistrationError, match="closed by the TBI"):
        signup(scheduled, "ana@example.com")
    row = ActivityAuditLog.objects.filter(activity=scheduled, field_changed="Registration").get()
    assert (row.new_value, row.changed_by) == (activity_services.REGISTRATION_CLOSED_VALUE, director)


def test_reopening_opens_it_again_with_a_history_row(director, scheduled, signup) -> None:
    close(director, scheduled)
    reopen(director, scheduled)
    scheduled.refresh_from_db()
    assert services.registration_closed_reason(scheduled) is None and is_listed(scheduled)
    assert signup(scheduled, "ana@example.com").pk
    values = list(ActivityAuditLog.objects.filter(activity=scheduled, field_changed="Registration")
                  .order_by("log_id").values_list("old_value", "new_value"))
    assert values[-1] == (activity_services.REGISTRATION_CLOSED_VALUE, activity_services.REGISTRATION_REOPENED_VALUE)


def test_the_automatic_rules_still_apply_after_reopening(director, scheduled) -> None:
    close(director, scheduled)
    reopen(director, scheduled)
    late = scheduled.registration_deadline + timedelta(minutes=1)
    assert "deadline" in services.registration_closed_reason(scheduled, now=late)
    assert not is_listed(scheduled, now=late)
    activity_services.update_activity(director, scheduled, {"max_slots": 1})
    services.register(scheduled, name="Ana", email="ana@example.com", designation="Owner", office_agency="CSU",
                      answers={str(f.pk): "CSU" for f in scheduled.form_templates.get(form_type="registration").fields.all()})
    assert services.registration_closed_reason(scheduled) == "Registration is closed: all slots are taken."


def test_only_while_scheduled_and_never_twice(director, scheduled) -> None:
    with pytest.raises(activity_services.ActivityError, match="was not closed by hand"):
        reopen(director, scheduled)
    close(director, scheduled)
    with pytest.raises(activity_services.ActivityError, match="already closed"):
        close(director, scheduled)
    activity_services.change_status(director, scheduled, "Ongoing")
    with pytest.raises(activity_services.ActivityError, match="only while the activity is Scheduled"):
        reopen(director, scheduled)
    # Outside Scheduled the status reason comes first (registration is not open anyway).
    scheduled.refresh_from_db()
    assert services.registration_closed_reason(scheduled) == "Registration is not open for this activity."


def test_needs_can_create_activity(view_only, scheduled) -> None:
    with pytest.raises(PermissionDenied):
        close(view_only, scheduled)


def test_share_card_switch_for_creators_only_and_the_view(director, view_only, scheduled) -> None:
    overview = reverse("activities:detail", args=[scheduled.pk])
    viewer = Client()
    viewer.force_login(view_only)
    assert "data-registration-switch" not in viewer.get(overview).content.decode()
    assert viewer.post(reverse("activities:registration_switch", args=[scheduled.pk]), {"action": "close"}).status_code == 403

    client = Client()
    client.force_login(director)
    page = client.get(overview).content.decode()
    assert "data-registration-switch" in page and "Close registration" in page
    response = client.post(reverse("activities:registration_switch", args=[scheduled.pk]), {"action": "close"}, follow=True)
    html = response.content.decode()
    assert services.REGISTRATION_CLOSED_BY_TBI in html and "Reopen registration" in html
    client.post(reverse("activities:registration_switch", args=[scheduled.pk]), {"action": "reopen"})
    scheduled.refresh_from_db()
    assert scheduled.registration_closed_by_hand is False
    public = Client().get(reverse("register", args=[scheduled.pk])).content.decode()
    assert services.REGISTRATION_CLOSED_BY_TBI not in public


def test_public_page_says_closed_by_the_tbi(director, scheduled) -> None:
    close(director, scheduled)
    html = Client().get(reverse("register", args=[scheduled.pk])).content.decode()
    assert "Registration is closed" in html and services.REGISTRATION_CLOSED_BY_TBI in html
