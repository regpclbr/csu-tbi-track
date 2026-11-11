"""The grouped Activities list (ASSUMPTIONS 353): services.grouped_activities() puts each activity in Happening now,
Needs follow-up, Upcoming, Drafts or Past (activity_start_end and the automatic job's rules), the page shows the
sections, the follow-up cards' quick actions follow the permissions, and the plain sorts' order and tie-breakers."""

from datetime import date, datetime, time, timedelta

import pytest
from django.test import Client
from django.urls import reverse
from django.utils import timezone

from activities import pages, services
from activities.models import Activity, ActivityStatus

from .conftest import at

pytestmark = pytest.mark.django_db

LIST = reverse("activities:list")
# Wednesday Oct 21, 2026, 10:00 AM Manila.
NOW = timezone.make_aware(datetime(2026, 10, 21, 10, 0))
TODAY = NOW.date()


@pytest.fixture
def login():
    def log_in(member) -> Client:
        client = Client()
        client.force_login(member)
        return client

    return log_in


@pytest.fixture
def make(director, valid_data):
    """Factory: make(title, status, start, end=None, times=(8, 17)) creates an activity through the service (as of a
    month before NOW, so past dates are allowed) and sets its status directly (these tests are about grouping)."""

    def create(title: str, status: str, start: date, end: date | None = None, times=(8, 17)) -> Activity:
        end = end or start
        activity = services.create_activity(
            director,
            valid_data(title=title, start_date=start, end_date=end, start_time=time(times[0]), end_time=time(times[1]),
                       registration_deadline=at(start - timedelta(days=1)), evaluation_deadline=at(end + timedelta(days=7))),
            now=timezone.make_aware(datetime(2026, 9, 1, 9)),
        )
        Activity.objects.filter(pk=activity.pk).update(status=ActivityStatus.objects.get(name=status))
        activity.refresh_from_db()
        return activity

    return create


def titles(groups) -> dict[str, list[str]]:
    return {group.key: [activity.title for activity in group.items] for group in groups}


ONGOING, SCHEDULED, DRAFT, CONDUCTED = "Ongoing", "Scheduled", "Draft", "Conducted"


@pytest.fixture
def every_case(make):
    day = timedelta(days=1)
    make("Running today", ONGOING, TODAY)
    make("Three days", ONGOING, TODAY - day, TODAY + day)
    make("Ended this morning", ONGOING, TODAY, times=(7, 9))
    make("Ended yesterday", ONGOING, TODAY - day)
    make("Draft that started", DRAFT, TODAY, times=(8, 17))
    make("Draft from last week", DRAFT, TODAY - 7 * day)
    make("Ongoing too early", ONGOING, TODAY + 4 * day)
    make("Next week", SCHEDULED, TODAY + 7 * day)
    make("Tomorrow", SCHEDULED, TODAY + day)
    make("Draft this afternoon", DRAFT, TODAY, times=(14, 17))
    make("Draft in November", DRAFT, date(2026, 11, 5))
    make("Business Model Training", CONDUCTED, date(2026, 10, 2), date(2026, 10, 3))
    make("IP Seminar", CONDUCTED, date(2026, 10, 13))
    make("Pitch Competition", CONDUCTED, date(2026, 9, 23))


def test_each_activity_is_in_its_group_in_order(every_case) -> None:
    groups = services.grouped_activities(now=NOW)
    assert [group.key for group in groups] == ["now", "follow_up", "upcoming", "drafts", "past"]
    assert titles(groups) == {
        "now": ["Three days", "Running today"],  # earliest start first
        "follow_up": ["Draft from last week", "Ended yesterday", "Ended this morning", "Draft that started"],
        "upcoming": ["Tomorrow", "Ongoing too early", "Next week"],  # soonest first, future Ongoing included
        "drafts": ["Draft this afternoon", "Draft in November"],
        "past": ["IP Seminar", "Business Model Training", "Pitch Competition"],  # newest first
    }
    assert [group.total for group in groups] == [2, 4, 3, 2, 3]


def test_empty_groups_are_left_out(make) -> None:
    make("Tomorrow", SCHEDULED, TODAY + timedelta(days=1))
    assert [group.key for group in services.grouped_activities(now=NOW)] == ["upcoming"]
    assert services.grouped_activities({"search": "nothing like this"}, now=NOW) == []


def test_filters_apply_inside_the_groups(every_case) -> None:
    draft = ActivityStatus.objects.get(name=DRAFT).pk
    assert titles(services.grouped_activities({"status": draft}, now=NOW)) == {
        "follow_up": ["Draft from last week", "Draft that started"],
        "drafts": ["Draft this afternoon", "Draft in November"],
    }
    assert titles(services.grouped_activities({"search": "draft", "date_from": date(2026, 11, 1)}, now=NOW)) == {
        "drafts": ["Draft in November"],
    }


def test_only_past_is_paginated(every_case) -> None:
    first = services.grouped_activities(per_page=2, now=NOW)
    past = first[-1]
    assert past.key == "past" and past.total == 3 and len(past.items) == 2 and past.page.num_pages == 2
    assert len(first[1].items) == 4 and first[1].page is None  # follow-up shows every activity
    second = services.grouped_activities(page=2, per_page=2, now=NOW)
    assert [a.title for a in second[-1].items] == ["Pitch Competition"]


def test_happening_now_group_is_happening_now(every_case) -> None:
    """The list's group and the dashboard's happening_now() are the same rule (parity), and an activity whose end
    time has passed today is in neither (ASSUMPTIONS 351, 353)."""
    group = next(g for g in services.grouped_activities(now=NOW) if g.key == "now")
    assert [a.title for a in services.happening_now(now=NOW)] == [a.title for a in group.items]
    assert "Ended this morning" not in [a.title for a in services.happening_now(now=NOW)]
    for activity in Activity.objects.select_related("status"):
        assert services.is_happening_now(activity, NOW) == (activity in group.items)


def test_happening_now_without_times_runs_until_the_end_of_the_day(make) -> None:
    activity = make("No times", ONGOING, TODAY)
    Activity.objects.filter(pk=activity.pk).update(start_time=None, end_time=None)
    late = timezone.make_aware(datetime(2026, 10, 21, 23, 30))
    assert list(services.happening_now(now=late)) == [activity]


# --- The plain sorts (the "newest first" bug) --------------------------------------------------------------------


def test_newest_first_is_newest_first_with_ties_by_time(make) -> None:
    make("Oct 2-3", CONDUCTED, date(2026, 10, 2), date(2026, 10, 3))
    make("Oct 13", CONDUCTED, date(2026, 10, 13))
    make("Oct 19-21", ONGOING, date(2026, 10, 19), date(2026, 10, 21))
    make("Oct 20", ONGOING, date(2026, 10, 20))
    make("Oct 21 morning", DRAFT, date(2026, 10, 21), times=(8, 12))
    make("Oct 21 afternoon", DRAFT, date(2026, 10, 21), date(2026, 10, 22), times=(13, 17))
    newest = [a.title for a in services.list_activities({"sort": "-start_date"}).items]
    assert newest == ["Oct 21 afternoon", "Oct 21 morning", "Oct 20", "Oct 19-21", "Oct 13", "Oct 2-3"]
    oldest = [a.title for a in services.list_activities({"sort": "start_date"}).items]
    assert oldest == list(reversed(newest))


def test_the_page_default_is_grouped_and_the_api_default_stays_newest_first() -> None:
    assert pages.parse_list_filters({}).sort == services.GROUPED_SORT
    assert pages.parse_list_filters({}).query() == ""  # the default is not written in the URL
    assert pages.parse_list_filters({"sort": "-start_date"}).query() == "sort=-start_date"
    assert services.DEFAULT_SORT == "-start_date"


# --- The page ----------------------------------------------------------------------------------------------------


def test_page_shows_the_sections_with_counts_and_month_headings(director, login, every_case, clock) -> None:
    clock(NOW)
    html = login(director).get(LIST).content.decode()
    for label in ("Happening now", "Needs follow-up", "Upcoming", "Drafts", "Past"):
        assert f">{label}" in html or f"{label} <span" in html
    assert "October 2026" in html and "September 2026" in html
    assert html.index("Happening now") < html.index("Needs follow-up") < html.index("Upcoming") < html.index("Past")


def test_page_hides_empty_sections_and_a_plain_sort_has_no_sections(director, login, make, clock) -> None:
    clock(NOW)
    make("Tomorrow", SCHEDULED, TODAY + timedelta(days=1))
    client = login(director)
    html = client.get(LIST).content.decode()
    assert 'id="group-upcoming"' in html and 'id="group-now"' not in html and 'id="group-past"' not in html
    plain = client.get(LIST, {"sort": "title"}).content.decode()
    assert 'id="group-' not in plain and "Tomorrow" in plain


def test_follow_up_cards_have_the_quick_actions(director, login, make, clock) -> None:
    clock(NOW)
    ended = make("Ended yesterday", ONGOING, TODAY - timedelta(days=1))
    late_draft = make("Draft from last week", DRAFT, TODAY - timedelta(days=7))
    html = login(director).get(LIST).content.decode()
    assert f'{reverse("activities:detail", args=[ended.pk])}?tab=registrations' in html and "Mark attendance" in html
    assert f'id="conduct-{ended.pk}"' in html and 'name="next" value="list"' in html
    assert "No one registered. Mark as Conducted or change the status." in html  # the attention note on the card
    # The Draft card: its attention text and "Open activity", no attendance or Conducted button
    assert "Still a Draft but it started on" in html and "Open activity" in html
    assert f'id="conduct-{late_draft.pk}"' not in html


def test_follow_up_cards_have_no_actions_without_can_create_activity(make_member, login, make, clock) -> None:
    clock(NOW)
    ended = make("Ended yesterday", ONGOING, TODAY - timedelta(days=1))
    html = login(make_member("System Administrator")).get(LIST).content.decode()
    assert "Ended yesterday" in html and "Mark attendance" not in html and f'id="conduct-{ended.pk}"' not in html


def test_mark_as_conducted_goes_through_change_status_and_back_to_the_list(director, login, make, clock) -> None:
    clock(NOW)
    ended = make("Ended yesterday", ONGOING, TODAY - timedelta(days=1))
    url = reverse("activities:change_status", args=[ended.pk])
    response = login(director).post(url, {"status": CONDUCTED, "next": "list"}, follow=True)
    assert response.redirect_chain[-1][0] == LIST
    ended.refresh_from_db()
    assert ended.status.name == CONDUCTED
    assert ended.audit_logs.filter(field_changed="Status", new_value=CONDUCTED, changed_by=director).exists()
    assert 'changed from Ongoing to Conducted' in response.content.decode()


def test_mark_as_conducted_without_permission_is_refused(make_member, login, make, clock) -> None:
    clock(NOW)
    ended = make("Ended yesterday", ONGOING, TODAY - timedelta(days=1))
    url = reverse("activities:change_status", args=[ended.pk])
    response = login(make_member("System Administrator")).post(url, {"status": CONDUCTED, "next": "list"})
    assert response.status_code == 403
    ended.refresh_from_db()
    assert ended.status.name == ONGOING


def test_next_is_never_a_url_from_the_request(director, login, make, clock) -> None:
    clock(NOW)
    ended = make("Ended yesterday", ONGOING, TODAY - timedelta(days=1))
    url = reverse("activities:change_status", args=[ended.pk])
    response = login(director).post(url, {"status": CONDUCTED, "next": "https://evil.example.com/"})
    assert response.url == reverse("activities:detail", args=[ended.pk])
