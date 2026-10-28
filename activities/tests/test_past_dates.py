"""Start date and registration deadline cannot be in the past (Manila "today")."""

from datetime import datetime, time, timedelta
from datetime import timezone as dt_timezone

import pytest
from django.conf import settings
from django.core.exceptions import ValidationError
from django.utils import timezone

from activities import services
from activities.models import Activity

from .conftest import at


def error_for(excinfo, field: str) -> str:
    return " ".join(excinfo.value.message_dict[field])


def test_time_zone_is_manila() -> None:
    assert settings.TIME_ZONE == "Asia/Manila"
    assert settings.USE_TZ is True


# --- Create ------------------------------------------------------------------------------


@pytest.mark.django_db
def test_create_refuses_past_start_date(director, valid_data) -> None:
    yesterday = timezone.localdate() - timedelta(days=1)
    with pytest.raises(ValidationError) as excinfo:
        services.create_activity(
            director,
            valid_data(start_date=yesterday, end_date=yesterday, registration_deadline=at(yesterday - timedelta(days=1))),
        )
    assert error_for(excinfo, "start_date") == "Start date cannot be in the past."


@pytest.mark.django_db
@pytest.mark.parametrize("moment", [time(9, 0), time(23, 58)], ids=["09:00", "23:58"])
def test_create_allows_start_today(director, valid_data, clock, moment) -> None:
    """A fixed clock (it used to skip itself near midnight): a start today with the deadline at the end of today."""
    clock(datetime.combine(datetime(2026, 11, 20).date(), moment))
    today = timezone.localdate()
    end_of_today = timezone.make_aware(datetime.combine(today, time.max))
    activity = services.create_activity(
        director, valid_data(start_date=today, end_date=today, registration_deadline=end_of_today)
    )
    assert activity.start_date == today == datetime(2026, 11, 20).date()


@pytest.mark.django_db
def test_create_refuses_past_registration_deadline(director, valid_data) -> None:
    an_hour_ago = timezone.now() - timedelta(hours=1)
    with pytest.raises(ValidationError) as excinfo:
        services.create_activity(director, valid_data(registration_deadline=an_hour_ago))
    assert error_for(excinfo, "registration_deadline") == "Registration deadline cannot be in the past."


@pytest.mark.django_db
def test_today_is_the_manila_date(director, valid_data) -> None:
    """At 11 PM UTC on Oct 19 it is already Oct 20 in Manila, so an Oct 19 start is in the past."""
    utc_11pm = datetime(2026, 10, 19, 23, 0, tzinfo=dt_timezone.utc)
    oct_19 = utc_11pm.date()
    with pytest.raises(ValidationError) as excinfo:
        services.create_activity(
            director,
            valid_data(start_date=oct_19, end_date=oct_19 + timedelta(days=1), registration_deadline=at(oct_19, 8)),
            now=utc_11pm,
        )
    assert error_for(excinfo, "start_date") == "Start date cannot be in the past."


# --- Update: only changed fields are checked ---------------------------------------------


@pytest.fixture
def started_activity(director, valid_data) -> Activity:
    """An activity that started 3 days ago (created "as of" two weeks ago)."""
    start = timezone.localdate() - timedelta(days=3)
    two_weeks_ago = timezone.now() - timedelta(days=14)
    return services.create_activity(
        director,
        valid_data(
            start_date=start,
            end_date=start + timedelta(days=5),
            registration_deadline=at(start - timedelta(days=1)),
            evaluation_deadline=at(start + timedelta(days=10)),
        ),
        now=two_weeks_ago,
    )


@pytest.mark.django_db
def test_started_activity_can_still_be_edited(director, started_activity) -> None:
    services.update_activity(director, started_activity, {"venue": "Room 101", "max_slots": 50})
    assert (started_activity.venue, started_activity.max_slots) == ("Room 101", 50)


@pytest.mark.django_db
def test_update_refuses_changing_start_date_to_the_past(director, started_activity) -> None:
    with pytest.raises(ValidationError) as excinfo:
        services.update_activity(director, started_activity, {"start_date": started_activity.start_date - timedelta(days=1)})
    assert error_for(excinfo, "start_date") == "Start date cannot be in the past."


@pytest.mark.django_db
def test_update_refuses_changing_deadline_to_the_past(director, new_activity) -> None:
    with pytest.raises(ValidationError) as excinfo:
        services.update_activity(director, new_activity, {"registration_deadline": timezone.now() - timedelta(minutes=1)})
    assert error_for(excinfo, "registration_deadline") == "Registration deadline cannot be in the past."


@pytest.mark.django_db
def test_unchanged_past_values_are_not_rechecked(director, started_activity) -> None:
    """Sending the same (past) start date again is not a change, so it is allowed."""
    services.update_activity(
        director, started_activity, {"start_date": started_activity.start_date, "venue": "Room 7"}
    )
    assert started_activity.venue == "Room 7"


@pytest.mark.django_db
def test_existing_rules_still_apply(director, valid_data) -> None:
    data = valid_data()
    with pytest.raises(ValidationError) as excinfo:
        services.create_activity(director, valid_data(end_date=data["start_date"] - timedelta(days=1)))
    assert "end_date" in excinfo.value.message_dict


# --- Django admin shows the error on the right field ------------------------------------


@pytest.mark.django_db
def test_admin_form_shows_past_date_errors_on_their_fields(client, admin_member, valid_data) -> None:
    from .test_admin import form_data

    yesterday = timezone.localdate() - timedelta(days=1)
    values = valid_data(start_date=yesterday, end_date=yesterday, registration_deadline=at(yesterday - timedelta(days=2)))
    client.force_login(admin_member)
    response = client.post("/admin/activities/activity/add/", form_data(values=values))

    assert response.status_code == 200
    form = response.context["adminform"].form
    assert form.errors["start_date"] == ["Start date cannot be in the past."]
    assert form.errors["registration_deadline"] == ["Registration deadline cannot be in the past."]
    assert not Activity.objects.exists()


@pytest.mark.django_db
def test_admin_edit_of_started_activity_is_allowed(client, admin_member, started_activity) -> None:
    from .test_admin import form_data

    client.force_login(admin_member)
    data = form_data(started_activity) | {"venue": "Room 101"}
    data["registration_deadline_1"] = timezone.localtime(started_activity.registration_deadline).strftime("%H:%M:%S")
    data["evaluation_deadline_1"] = timezone.localtime(started_activity.evaluation_deadline).strftime("%H:%M:%S")
    response = client.post(f"/admin/activities/activity/{started_activity.pk}/change/", data)
    assert response.status_code == 302
    started_activity.refresh_from_db()
    assert started_activity.venue == "Room 101"
