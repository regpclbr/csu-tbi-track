"""The dashboard's spotlight card (ASSUMPTIONS 351): an activity happening now beats the next upcoming one; the choice is
reports.services.dashboard_spotlight (activities.services.happening_now + S2), never the template."""

from datetime import date, datetime, time, timedelta

import pytest
from django.urls import reverse
from django.utils import timezone

from activities import services as activity_services
from activities.models import Activity, ActivityStatus, ActivityType
from reports import metrics, services

pytestmark = pytest.mark.django_db

URL = reverse("home")
TODAY = date(2026, 11, 6)
NOW = timezone.make_aware(datetime.combine(TODAY, time(10, 0)))  # Manila


def filters(activity_type: int | None = None) -> metrics.ReportFilters:
    return metrics.ReportFilters(date(2026, 1, 1), date(2026, 12, 31), activity_type)


@pytest.fixture
def now(clock) -> datetime:
    """The page loads see the same moment as the service calls (10:00 AM Manila on Nov 6, 2026)."""
    clock(datetime.combine(TODAY, time(10, 0)))
    return NOW


def status_of(activity: Activity) -> str:
    return Activity.objects.select_related("status").get(pk=activity.pk).status.name


# --- The choice (service) ------------------------------------------------------------------------------


def test_ongoing_beats_upcoming(make_activity, now) -> None:
    make_activity("Investor Networking", ActivityStatus.SCHEDULED, start=TODAY + timedelta(days=11))
    ongoing = make_activity("Market Research Training", ActivityStatus.ONGOING, start=TODAY)
    chosen = services.dashboard_spotlight(filters(), now)
    assert (chosen.kind, chosen.activity, chosen.more_ongoing) == (services.ONGOING_NOW, ongoing, 0)


def test_several_ongoing_the_one_that_started_first_and_the_others_counted(make_activity, now) -> None:
    later_today = make_activity("Afternoon Clinic", ActivityStatus.ONGOING, start=TODAY)
    Activity.objects.filter(pk=later_today.pk).update(start_time=time(9, 30))
    make_activity("Morning Clinic", ActivityStatus.ONGOING, start=TODAY)  # 8:00 AM
    first = make_activity("Bootcamp", ActivityStatus.ONGOING, start=TODAY - timedelta(days=2), end=TODAY + timedelta(days=1))
    chosen = services.dashboard_spotlight(filters(), now)
    assert chosen.activity == first and chosen.more_ongoing == 2
    assert [a.title for a in activity_services.happening_now(now=now)] == ["Bootcamp", "Morning Clinic", "Afternoon Clinic"]


def test_none_ongoing_shows_the_next_scheduled(make_activity, now) -> None:
    make_activity("Far Training", ActivityStatus.SCHEDULED, start=TODAY + timedelta(days=40))
    soon = make_activity("Investor Networking", ActivityStatus.SCHEDULED, start=TODAY + timedelta(days=11))
    chosen = services.dashboard_spotlight(filters(), now)
    assert (chosen.kind, chosen.activity, chosen.more_ongoing) == (services.NEXT_UP, soon, 0)


def test_draft_is_never_shown(make_activity, now) -> None:
    make_activity("Draft today", ActivityStatus.DRAFT, start=TODAY)
    make_activity("Draft later", ActivityStatus.DRAFT, start=TODAY + timedelta(days=3))
    assert services.dashboard_spotlight(filters(), now) is None


def test_ongoing_past_its_end_date_is_not_happening_now(make_activity, now) -> None:
    make_activity("Ended, attendance not marked", ActivityStatus.ONGOING, start=TODAY - timedelta(days=3))
    upcoming = make_activity("Next week", ActivityStatus.SCHEDULED, start=TODAY + timedelta(days=7))
    assert services.dashboard_spotlight(filters(), now).activity == upcoming


def test_the_activity_type_filter_is_respected(make_activity, now) -> None:
    make_activity("Workshop now", ActivityStatus.ONGOING, start=TODAY, type_name="Workshop")
    seminar = make_activity("Seminar soon", ActivityStatus.SCHEDULED, start=TODAY + timedelta(days=5), type_name="Seminar")
    chosen = services.dashboard_spotlight(filters(ActivityType.objects.get(name="Seminar").pk), now)
    assert chosen.kind == services.NEXT_UP and chosen.activity == seminar


# --- The page ----------------------------------------------------------------------------------------------


def test_page_shows_happening_now_with_attendance_ring_and_mark_attendance(director, login, make_activity, register, now) -> None:
    make_activity("Investor Networking", ActivityStatus.SCHEDULED, start=TODAY + timedelta(days=11))
    ongoing = make_activity("Market Research Training", ActivityStatus.ONGOING, start=TODAY, max_slots=30)
    for i in range(4):
        register(ongoing, f"P{i}", attended=i < 1)
    response = login(director).get(URL)
    page, html = response.context["page"], response.content.decode()
    assert page.next_activity["activity"] == ongoing and page.next_activity["ongoing"]
    assert "Happening now" in html and ">Next activity<" not in html
    assert 'aria-label="1 of 4 registered marked attended"' in html and ">1/4<" in html
    assert f'{reverse("activities:detail", args=[ongoing.pk])}?tab=registrations' in html and "Mark attendance" in html
    # The upcoming list keeps the first upcoming activity (the spotlight does not show it)
    assert [row["activity"].title for row in page.upcoming_after_next] == ["Investor Networking"]
    assert "more ongoing" not in html


def test_page_links_the_other_ongoing_activities_to_the_filtered_list(director, login, make_activity, now) -> None:
    make_activity("First", ActivityStatus.ONGOING, start=TODAY - timedelta(days=1), end=TODAY)
    make_activity("Second", ActivityStatus.ONGOING, start=TODAY)
    make_activity("Third", ActivityStatus.ONGOING, start=TODAY)
    html = login(director).get(URL).content.decode()
    ongoing_id = ActivityStatus.objects.get(name=ActivityStatus.ONGOING).pk
    assert "+2 more ongoing" in html and f'{reverse("activities:list")}?status={ongoing_id}' in html


def test_page_without_ongoing_shows_the_next_activity_as_before(director, login, make_activity, now) -> None:
    make_activity("Investor Networking", ActivityStatus.SCHEDULED, start=TODAY + timedelta(days=11), max_slots=20)
    make_activity("Far Training", ActivityStatus.SCHEDULED, start=TODAY + timedelta(days=40))
    response = login(director).get(URL)
    page, html = response.context["page"], response.content.decode()
    assert page.next_activity["activity"].title == "Investor Networking" and not page.next_activity["ongoing"]
    assert ">Next activity<" in html and "Happening now" not in html and "Open activity" in html
    assert 'aria-label="0 of 20 slots registered"' in html
    assert [row["activity"].title for row in page.upcoming_after_next] == ["Far Training"]


def test_page_load_moves_a_started_scheduled_activity_to_ongoing_first(director, login, make_activity, now) -> None:
    started = make_activity("Started at 8", ActivityStatus.SCHEDULED, start=TODAY)  # 8:00 AM, now is 10:00 AM
    page = login(director).get(URL).context["page"]
    assert status_of(started) == ActivityStatus.ONGOING
    assert page.next_activity["activity"] == started and page.next_activity["ongoing"]


def test_draft_today_is_never_the_spotlight(director, login, make_activity, now) -> None:
    make_activity("Draft today", ActivityStatus.DRAFT, start=TODAY)
    html = login(director).get(URL).content.decode()
    assert "Nothing scheduled yet" in html and "Draft today" not in html
