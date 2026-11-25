"""Dashboard API: access, scopes, filters, query limit, and the same numbers as a report."""

from datetime import date, datetime

import pytest
from django.db import connection
from django.test import Client
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from django.utils import timezone

from activities.models import ActivityStatus, ActivityType
from reports import metrics
from reports.content import build_content
from startups.models import Startup
from startups.services import STARTUP_SESSION_KEY

from .conftest import TEST_NOW, YEAR_2026

pytestmark = pytest.mark.django_db

URL = reverse("reports:api_dashboard")
QUERY_LIMIT = 20  # 18 today (Step 10): a fixed number, whatever the amount of data


def cards(data: dict) -> dict:
    return {card["key"]: card for card in data["cards"]}


@pytest.fixture
def some_data(make_activity, register, evaluate, add_document, make_certificate_row):
    """A few activities of every kind, so every card and chart has rows."""

    def build(copies: int = 1) -> None:
        for n in range(copies):
            conducted = make_activity(f"Conducted {n}", start=date(2026, 3 + n % 6, 10), categories=("Faculty", "MSMEs"))
            for i in range(3):
                registration = register(conducted, f"Person {n}-{i}", attended=i < 2, category="Faculty")
                if i == 0:
                    evaluate(registration, {"Overall rating": 4, "Venue": 5})
                    make_certificate_row(activity=conducted, registration=registration)
            add_document(conducted, "Activity Design")
            make_activity(f"Draft {n}", ActivityStatus.DRAFT, start=date(2026, 11, 20))
            make_activity(f"Scheduled {n}", ActivityStatus.SCHEDULED, start=date(2026, 11, 25), type_name="Seminar")

    return build


# --- Access ----------------------------------------------------------------------------------------


def test_anonymous_is_sent_to_login() -> None:
    response = Client().get(URL)
    assert response.status_code == 401 and response.json() == {"error": "Login required."}


def test_a_startup_session_is_not_staff(roles) -> None:
    startup = Startup.objects.create(name="Co", email="co@startup.test", navigatu_id="N1")
    client = Client()
    session = client.session
    session[STARTUP_SESSION_KEY] = startup.pk
    session.save()
    response = client.get(URL)
    assert response.status_code == 401 and response.json() == {"error": "Login required."}


def test_any_active_staff_member_sees_the_dashboard(view_only, login) -> None:
    response = login(view_only).get(URL)
    assert response.status_code == 200


# --- Shape and scopes -------------------------------------------------------------------------------


def test_every_card_says_its_scope(view_only, login, some_data) -> None:
    some_data()
    data = login(view_only).get(URL, {"start": "2026-01-01", "end": "2026-12-31"}).json()
    scopes = {card["key"]: card["scope"] for card in data["cards"]}
    assert scopes == {
        "activities_by_status": "period",
        "upcoming_activities": "now",
        "activities_conducted": "period",
        "registrations": "period",
        "evaluations": "period",
        "documents_complete": "period",
        "documents_overdue": "now",
        "documents_waiting_for_review": "now",
        "certificates_waiting_for_approval": "now",
        "certificates_issued": "period",
        "startups_engaged": "period",
    }
    assert data["filters"] == {
        "start": "2026-01-01", "end": "2026-12-31", "range_text": "January 1 – December 31, 2026", "activity_type": None,
    }
    assert set(data["charts"]) == {"per_month", "by_type", "by_category", "average_rating_by_question", "participant_mix"}
    assert len(data["charts"]["per_month"]) == 12


def test_now_cards_ignore_the_range_but_period_cards_do_not(view_only, login, some_data) -> None:
    some_data()
    client = login(view_only)
    this_year = cards(client.get(URL, {"start": "2026-01-01", "end": "2026-12-31"}).json())
    other_year = cards(client.get(URL, {"start": "2020-01-01", "end": "2020-12-31"}).json())
    assert this_year["activities_conducted"]["value"] == 1 and other_year["activities_conducted"]["value"] == 0
    assert this_year["activities_by_status"]["value"] == 3 and other_year["activities_by_status"]["value"] == 0
    for key in ("upcoming_activities", "documents_overdue", "documents_waiting_for_review", "certificates_waiting_for_approval"):
        assert this_year[key]["value"] == other_year[key]["value"], key


def test_now_cards_respect_the_activity_type(view_only, login, some_data, clock) -> None:
    clock(datetime(2026, 10, 23, 9, 0))  # the reports' test day: the Scheduled Seminar (Nov 25) is still upcoming
    some_data()
    seminar = ActivityType.objects.get(name="Seminar")
    data = cards(login(view_only).get(URL, {"activity_type": seminar.pk}).json())
    assert data["upcoming_activities"]["value"] == 1  # the Scheduled Seminar
    assert data["certificates_waiting_for_approval"]["approved_not_issued"] == 0  # certificates are on a Workshop


def test_default_range_is_the_current_year(view_only, login) -> None:
    data = login(view_only).get(URL).json()
    year = timezone.localdate().year
    assert data["filters"]["start"] == f"{year}-01-01" and data["filters"]["end"] == f"{year}-12-31"


def test_bad_filters_are_400(view_only, login) -> None:
    response = login(view_only).get(URL, {"start": "2026-05-01", "end": "2026-04-01"})
    assert response.status_code == 400 and "end" in response.json()["errors"]


# --- Query limit -------------------------------------------------------------------------------------


def test_dashboard_query_count_is_small_and_does_not_grow_with_data(some_data, roles) -> None:
    some_data(1)
    with CaptureQueriesContext(connection) as small:
        metrics.dashboard(YEAR_2026, TEST_NOW)
    some_data(12)
    with CaptureQueriesContext(connection) as large:
        metrics.dashboard(YEAR_2026, TEST_NOW)
    assert len(small) <= QUERY_LIMIT, len(small)
    assert len(large) == len(small)


# --- Same numbers as a report ---------------------------------------------------------------------


def test_dashboard_and_report_give_identical_numbers(some_data, director, make_activity, register) -> None:
    some_data(3)
    seminar = make_activity("A seminar", type_name="Seminar", start=date(2026, 4, 2))
    register(seminar, "Zed", attended=True)
    for filters in (YEAR_2026, metrics.ReportFilters(date(2026, 3, 1), date(2026, 4, 30)),
                    metrics.ReportFilters(date(2026, 1, 1), date(2026, 12, 31), ActivityType.objects.get(name="Seminar").pk)):
        dashboard = cards(metrics.dashboard(filters, TEST_NOW))
        content = build_content(
            "accomplishment", ["summary", "by_category", "evaluation", "certificates"], filters,
            generated_by=director.name, generated_at=TEST_NOW, now=TEST_NOW,
        )
        key_figures = {row[0]: row[1] for row in content.section("summary").tables[0].rows}
        value = lambda cell: getattr(cell, "value", cell)  # noqa: E731
        assert dashboard["activities_conducted"]["value"] == key_figures["Activities conducted"]
        assert dashboard["registrations"]["value"] == key_figures["Registered participants"]
        assert dashboard["registrations"]["attended"] == key_figures["Attended"]
        assert dashboard["registrations"]["attendance_rate"] == value(key_figures["Attendance rate (attended / registered)"])
        assert dashboard["registrations"]["unique_attendees"] == key_figures["Unique attendees (by email)"]
        assert dashboard["evaluations"]["value"] == key_figures["Evaluations submitted"]
        assert dashboard["evaluations"]["average_rating"] == value(key_figures["Average rating (1-5, all rating questions)"])
        assert dashboard["documents_complete"]["value"] == key_figures["Activities with all required documents approved"]
        assert dashboard["certificates_issued"]["value"] == key_figures["Certificates issued"]
        assert dashboard["certificates_issued"]["emailed"] == key_figures["Certificates emailed"]
        assert dashboard["startups_engaged"]["value"] == key_figures["Startups engaged"]
        # Charts and report tables come from the same functions too.
        category_table = {row[0]: row[1:4] for row in content.section("by_category").tables[0].rows}
        dashboard_categories = {
            row["name"]: [row["activities"], row["registered"], row["attended"]] for row in metrics.dashboard(filters, TEST_NOW)["charts"]["by_category"]
        }
        assert category_table == dashboard_categories
