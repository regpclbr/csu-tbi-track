"""The dashboard page (/): access, the same numbers as reports.metrics / the dashboard API, filters
(HTMX partial, URL keeps them), role-dependent parts, Needs attention, empty states."""

import re
from datetime import date, time, timedelta

import pytest
from django.test import Client
from django.urls import reverse
from django.utils import timezone

from activities.models import Activity, ActivityStatus, ActivityType
from certificates.models import Certificate
from documents.models import DocumentStatus
from documents.services import review_queue
from reports import services
from reports.dashboard_page import matching_quick_range, quick_range
from conftest import VIEW_ONLY_ROLE

pytestmark = pytest.mark.django_db

URL = reverse("home")
HTMX = {"HTTP_HX_REQUEST": "true"}


def today() -> date:
    return timezone.localdate()


def kpis(html: str) -> dict[str, str]:
    """KPI card label -> value as shown on the page (components/kpi_card.html)."""
    found = re.findall(
        r'<p class="[^"]*font-medium[^"]*text-muted">([^<]+)</p>\s*</div>\s*<div[^>]*>\s*<p class="text-2xl[^"]*">([^<]+)</p>', html
    )
    return {label.strip(): value.strip() for label, value in found}


@pytest.fixture
def data(make_activity, register, evaluate, add_document, make_certificate_row):
    """A small, varied data set relative to today (the page uses the real clock)."""
    now = today()
    conducted = make_activity("Conducted Workshop", start=now - timedelta(days=20), categories=("Faculty", "MSMEs"))
    for i in range(4):
        registration = register(conducted, f"P{i}", attended=i < 3, category="Faculty" if i % 2 else "MSMEs")
        if i == 0:
            evaluate(registration, {"Overall rating": 5})
            make_certificate_row(activity=conducted, registration=registration, status=Certificate.Status.GENERATED)
    make_activity("Soon Seminar", ActivityStatus.SCHEDULED, start=now + timedelta(days=8), type_name="Seminar", max_slots=20)
    make_activity("Far Training", ActivityStatus.SCHEDULED, start=now + timedelta(days=45), type_name="Training")
    make_activity("A draft", ActivityStatus.DRAFT, start=now + timedelta(days=12))
    return {"conducted": conducted}


# --- Access ---------------------------------------------------------------------------------------------


def test_logged_out_is_sent_to_the_login_page() -> None:
    response = Client().get(URL)
    assert response.status_code == 302 and response.url == f"{reverse('accounts:login')}?next=/"


def test_an_htmx_request_without_login_gets_401_with_hx_redirect() -> None:
    response = Client().get(URL, {"range": "last_year"}, HTTP_HX_CURRENT_URL="http://testserver/?range=last_year", **HTMX)
    assert response.status_code == 401
    assert response["HX-Redirect"] == f"{reverse('accounts:login')}?next=/%3Frange%3Dlast_year"


def test_hx_current_url_of_another_site_is_ignored() -> None:
    response = Client().get(URL, HTTP_HX_CURRENT_URL="https://evil.example/steal", **HTMX)
    assert response["HX-Redirect"] == f"{reverse('accounts:login')}?next=/"


def test_every_active_staff_member_sees_the_dashboard(director, view_only, login) -> None:
    for member in (director, view_only):
        response = login(member).get(URL)
        assert response.status_code == 200
        assert "dashboard/index.html" in [t.name for t in response.templates]


# --- Same numbers as the dashboard API ----------------------------------------------------------------


@pytest.mark.parametrize("params", [{}, {"range": "last_year"}, {"range": "this_month"}, {"range": "custom", "start": "2026-01-01", "end": "2026-03-31"}])
def test_page_numbers_are_the_dashboard_api_numbers(director, login, data, params) -> None:
    response = login(director).get(URL, params)
    page = response.context["page"]
    api_params = {"start": page.filters.start.isoformat(), "end": page.filters.end.isoformat()}
    assert page.data == services.dashboard(director, api_params)
    api = login(director).get(reverse("reports:api_dashboard"), api_params).json()
    assert {c["key"]: c["value"] for c in api["cards"]} == {c["key"]: c["value"] for c in page.data["cards"]}


def test_kpi_cards_show_the_metric_numbers(director, login, data) -> None:
    response = login(director).get(URL)
    page = response.context["page"]
    shown = kpis(response.content.decode())
    cards = page.cards
    assert shown["Upcoming activities"] == str(cards["upcoming_activities"]["within_30_days"]) == "1"
    assert shown["Certificate approval"] == str(cards["certificates_waiting_for_approval"]["value"]) == "1"
    assert "activity · need your approval" in response.content.decode()  # ASSUMPTIONS 369: counted as activities
    assert shown["Registrations"] == str(cards["registrations"]["value"]) == "4"
    assert "75% attended" in response.content.decode()
    assert shown["Open for registration"] == "0"  # no registration forms in this data set


def test_charts_show_the_metric_numbers(director, login, data) -> None:
    response = login(director).get(URL)
    page = response.context["page"]
    html = response.content.decode()
    by_category = {row["name"]: row["attended"] for row in page.data["charts"]["by_category"]}
    assert page.categories["total"] == sum(by_category.values()) == page.cards["registrations"]["attended"] == 3
    assert {row["name"]: row["attended"] for row in page.categories["legend"]} == by_category
    assert page.statuses["total"] == page.cards["activities_by_status"]["value"]
    assert [(row["name"], row["count"]) for row in page.conducted_by_type] == [("Workshop", 1)]
    # Not a fixed 4: the default range is this year, and the fixture's dates (relative to today) cross into next year
    # from mid-November on. The total itself is checked against the card above.
    assert f"Activities by status · {page.statuses['total']} total" in html
    # Text equivalents of the charts
    assert "Attendees by participant category" in html and "Conducted activities per activity type" in html


# --- Filters ------------------------------------------------------------------------------------------------


def test_quick_ranges() -> None:
    day = date(2026, 11, 15)
    assert quick_range("this_month", day) == (date(2026, 11, 1), date(2026, 11, 30))
    assert quick_range("this_quarter", day) == (date(2026, 10, 1), date(2026, 12, 31))
    assert quick_range("this_quarter", date(2026, 2, 3)) == (date(2026, 1, 1), date(2026, 3, 31))
    assert quick_range("this_year", day) == (date(2026, 1, 1), date(2026, 12, 31))
    assert quick_range("last_year", day) == (date(2025, 1, 1), date(2025, 12, 31))
    assert quick_range("custom", day) is None
    assert matching_quick_range(date(2026, 1, 1), date(2026, 12, 31), day) == "this_year"
    assert matching_quick_range(date(2026, 1, 2), date(2026, 12, 31), day) == "custom"


def test_default_is_this_year(director, login) -> None:
    page = login(director).get(URL).context["page"]
    assert (page.filters.start, page.filters.end, page.range_key) == (date(today().year, 1, 1), date(today().year, 12, 31), "this_year")


def test_last_year_filter(director, login, data) -> None:
    page = login(director).get(URL, {"range": "last_year"}).context["page"]
    assert page.filters.start == date(today().year - 1, 1, 1) and page.range_key == "last_year"


def test_a_quick_choice_wins_over_typed_dates(director, login) -> None:
    page = login(director).get(URL, {"range": "this_year", "start": "2020-01-01", "end": "2020-01-31"}).context["page"]
    assert page.filters.start == date(today().year, 1, 1)


def test_custom_range(director, login) -> None:
    page = login(director).get(URL, {"range": "custom", "start": "2026-02-01", "end": "2026-02-28"}).context["page"]
    assert (page.filters.start, page.filters.end, page.range_key, page.errors) == (date(2026, 2, 1), date(2026, 2, 28), "custom", [])


def test_an_invalid_custom_range_shows_the_error_and_this_year(director, login) -> None:
    response = login(director).get(URL, {"range": "custom", "start": "2026-06-30", "end": "2026-06-01"})
    page = response.context["page"]
    assert response.status_code == 200
    assert page.errors and "before the start date" in page.errors[0]
    assert page.filters.start == date(today().year, 1, 1)
    assert "before the start date" in response.content.decode()


def test_activity_type_filter(director, login, data) -> None:
    seminar = ActivityType.objects.get(name="Seminar")
    page = login(director).get(URL, {"activity_type": seminar.pk}).context["page"]
    assert page.filters.activity_type_id == seminar.pk
    assert page.cards["activities_conducted"]["value"] == 0
    assert [row["activity"].title for row in page.upcoming] == ["Soon Seminar"]


def test_htmx_filter_request_returns_only_the_body(director, login, data) -> None:
    response = login(director).get(URL, {"range": "last_year"}, **HTMX)
    names = [t.name for t in response.templates]
    assert "dashboard/_body.html" in names and "dashboard/index.html" not in names
    assert "<aside" not in response.content.decode()


def test_the_filter_form_updates_with_htmx_and_keeps_the_url(director, login) -> None:
    html = login(director).get(URL).content.decode()
    form = re.search(r'<form id="dashboard-filters"[^>]*>', html).group(0)
    assert 'hx-get="/"' in form and 'hx-target="#dashboard-body"' in form and 'hx-push-url="true"' in form
    assert "htmx-indicator" in html  # loading state


# --- Role-dependent parts ---------------------------------------------------------------------------------


def test_new_activity_button_only_with_can_create_activity(director, view_only, login) -> None:
    create_url = reverse("activities:create")
    assert create_url in login(director).get(URL).content.decode()
    assert create_url not in login(view_only).get(URL).content.decode()


def test_documents_card_for_a_reviewer_counts_my_review_queue(director, make_member, login, data, add_document) -> None:
    approver = make_member("Director", email="second.director@example.com")  # can_approve_documents (ASSUMPTIONS 344)
    add_document(data["conducted"], "Activity Design", DocumentStatus.FOR_REVIEW)  # uploaded by director
    response = login(approver).get(URL)
    page = response.context["page"]
    assert page.review["mine"] is True
    assert page.review["count"] == review_queue(approver).count() == 1
    assert kpis(response.content.decode())["Documents to review"] == "1"
    director_page = login(director).get(URL).context["page"]
    assert director_page.review["count"] == 1  # their own upload: they may approve it
    head_page = login(make_member("Incubation Project Head")).get(URL).context["page"]
    assert head_page.review["mine"] is False  # no can_approve_documents: the read-only S4 card


def test_documents_card_without_can_create_activity_shows_the_s4_total(view_only, login, data, add_document) -> None:
    add_document(data["conducted"], "Activity Design", DocumentStatus.FOR_REVIEW)
    response = login(view_only).get(URL)
    page = response.context["page"]
    html = response.content.decode()
    assert page.review["mine"] is False
    assert kpis(html)["Documents waiting for review"] == str(page.cards["documents_waiting_for_review"]["value"]) == "1"
    assert "Documents to review" not in html
    assert not any(item.action_label == "Review" for item in page.attention)
    assert "waiting for your review" not in html


# --- Needs attention ---------------------------------------------------------------------------------------


def test_needs_attention_lists_overdue_due_soon_review_and_signoff(make_member, login, data, add_document, make_activity) -> None:
    head = make_member("Director", email="second.director@example.com")  # reviews documents and certificates
    add_document(data["conducted"], "Activity Design", DocumentStatus.FOR_REVIEW)
    page = login(head).get(URL).context["page"]
    tones = [item.tone for item in page.attention]
    assert "danger" in tones  # the conducted activity's missing documents are overdue
    assert "warning" in tones  # "Soon Seminar" (starts in 8 days): Request Letter due in 3 days
    titles = " | ".join(item.title for item in page.attention)
    assert "Soon Seminar needs the Request Letter" in titles
    assert "Invitation Letter" not in titles  # optional since ASSUMPTIONS 386
    assert "1 document is waiting for your review" in titles
    assert "1 activity needs certificate approval" in titles
    assert tones.index("danger") < tones.index("warning") < tones.index("primary")
    html = login(head).get(URL).content.decode()
    # Row actions are secondary buttons; the red / amber icon chip shows how urgent a row is (ASSUMPTIONS 411)
    assert "chip-red" in html and "chip-amber" in html and "btn-danger" not in html and "btn-warning" not in html
    # ASSUMPTIONS 413: the card stands out (red accent: something is overdue) and the first approval / review the member
    # can do ("Review") is the card's one solid button; the other rows stay secondary.
    assert page.attention_tone == "danger" and 'class="card has-accent accent-danger' in html
    first = page.attention_primary_index
    assert page.attention[first].action_label == "Review"
    section = html[html.index('id="needs-attention"'):html.index("</section>", html.index('id="needs-attention"'))]
    assert section.count("btn-primary") == 1 and f'btn-primary btn-sm shrink-0">{page.attention[first].action_label}<' in section


def test_needs_attention_links_go_to_the_right_place(make_member, login, data, add_document) -> None:
    """Phase F4: documents -> the activity's Documents tab with the type preselected; my review queue -> /documents/review/;
    certificates of one activity -> its Certificates tab."""
    from documents.models import DocumentType

    head = make_member("Director", email="second.director@example.com")  # reviews documents and certificates
    add_document(data["conducted"], "Activity Design", DocumentStatus.FOR_REVIEW)
    page = login(head).get(URL).context["page"]
    by_title = {item.title: item.action_url for item in page.attention}
    soon = next(url for title, url in by_title.items() if title.startswith("Soon Seminar needs the Request Letter"))
    seminar = Activity.objects.get(title="Soon Seminar")
    request_letter = DocumentType.objects.get(name="Request Letter")
    assert soon == f"{reverse('activities:detail', args=[seminar.pk])}?tab=documents&type={request_letter.pk}"
    overdue = [item.action_url for item in page.attention if item.tone == "danger"]
    assert overdue and all(f"/activities/{data['conducted'].pk}/?tab=documents&type=" in url for url in overdue)
    assert by_title["1 document is waiting for your review"] == reverse("documents:review")
    assert (by_title["1 activity needs certificate approval"]
            == f"{reverse('activities:detail', args=[data['conducted'].pk])}?tab=certificates")


def test_certificates_of_several_activities_link_to_the_certificates_page(director, login, data, make_activity, register,
                                                                       make_certificate_row, evaluate) -> None:
    other = make_activity("Second Conducted", start=today() - timedelta(days=30))
    for name in ("Q1", "Q2", "Q3"):
        registration = register(other, name, attended=True)
        make_certificate_row(activity=other, registration=registration, status=Certificate.Status.GENERATED)
        if name != "Q3":  # Q3 has not evaluated: its activity still needs approval (ASSUMPTIONS 369)
            evaluate(registration, {})
    page = login(director).get(URL).context["page"]
    item = next(item for item in page.attention if "certificate approval" in item.title)
    assert item.title == "2 activities need certificate approval"  # activities, not certificates
    assert item.detail.startswith("Certificates · ") and item.action_label == "Approve"
    assert item.action_url == f"{reverse('certificates:list')}?status=generated"


@pytest.mark.parametrize(("role_name", "approves"), [("Incubation Project Head", True), ("Admin Staff", True),
                                                     (VIEW_ONLY_ROLE, False), ("System Administrator", True)])
def test_approvers_and_others_see_their_wording(make_member, login, data, role_name, approves) -> None:
    response = login(make_member(role_name)).get(URL)
    item = next(item for item in response.context["page"].attention if "certificate approval" in item.title)
    assert item.title == "1 activity needs certificate approval"  # the same title for everyone (ASSUMPTIONS 369)
    if approves:  # can_approve_certificates (ASSUMPTIONS 361, 369)
        assert item.action_label == "Approve" and "activity · need your approval" in response.content.decode()
    else:
        assert item.action_label == "Open" and "activity · need approval" in response.content.decode()


def test_needs_attention_empty_state(director, login) -> None:
    html = login(director).get(URL).content.decode()
    assert "Nothing needs attention" in html


def test_empty_states_without_data(director, login) -> None:
    html = login(director).get(URL).content.decode()
    assert "No upcoming activities" in html
    assert "No conducted activities in this period." in html
    assert "No changes yet" in html


def test_upcoming_list_shows_registered_and_slots(director, login, data, register) -> None:
    from activities.models import Activity

    seminar = Activity.objects.get(title="Soon Seminar")
    register(seminar, "Early bird")
    page = login(director).get(URL).context["page"]
    first = page.upcoming[0]
    assert first["activity"] == seminar and first["registered"] == 1 and first["percent"] == 5
    assert [row["activity"].title for row in page.upcoming] == ["Soon Seminar", "Far Training"]  # Scheduled only, no Draft


def test_recent_changes_come_from_the_audit_log(director, login, roles) -> None:
    from decimal import Decimal

    from activities import services as activity_services
    from activities.models import ParticipantCategory

    start = today() + timedelta(days=20)
    activity = activity_services.create_activity(director, {
        "title": "Audit Seminar", "description": "", "activity_type": ActivityType.objects.get(name="Seminar"),
        "participant_categories": [ParticipantCategory.objects.get(name="Faculty")], "start_date": start, "end_date": start,
        "start_time": time(8, 0), "end_time": time(17, 0), "venue": "Hall A", "max_slots": 10,
        "registration_deadline": timezone.now() + timedelta(days=10), "evaluation_deadline": timezone.now() + timedelta(days=30),
        "budget_amount": Decimal("100.00"),
    })
    activity_services.update_activity(director, activity, {"venue": "TBI Lounge"})
    html = login(director).get(URL).content.decode()
    assert "Test Director changed the venue of Audit Seminar to TBI Lounge" in html
    assert "Test Director created Audit Seminar as Draft" in html
    assert "just now" in html


# --- Restyled shell and dashboard (ASSUMPTIONS 258) ---------------------------------------------------


@pytest.mark.parametrize(("hour", "expected"), [(6, "Good morning"), (11, "Good morning"), (12, "Good afternoon"),
                                                (17, "Good afternoon"), (18, "Good evening"), (23, "Good evening")])
def test_greeting_follows_manila_time(hour, expected) -> None:
    from datetime import datetime
    from zoneinfo import ZoneInfo

    from reports.dashboard_page import greeting

    assert greeting(datetime(2026, 10, 25, hour, 30, tzinfo=ZoneInfo("Asia/Manila"))) == expected
    in_utc = datetime(2026, 10, 25, hour, 30, tzinfo=ZoneInfo("Asia/Manila")).astimezone(ZoneInfo("UTC"))
    assert greeting(in_utc) == expected  # the same moment given in UTC


def test_banner_greets_by_first_name_and_summarises_the_page(director, login, data) -> None:
    response = login(director).get(URL)
    page, html = response.context["page"], response.content.decode()
    assert f"{page.greeting}, Test</h2>" in html  # "Test Director"
    upcoming = page.cards["upcoming_activities"]["within_30_days"]
    summary = re.search(r'<p id="banner-summary"[^>]*>(.*?)</p>', html, re.S).group(1)
    assert f"{len(page.attention)} item" in summary and f"{upcoming} upcoming activit" in summary
    assert 'href="#needs-attention"' in html and 'id="needs-attention"' in html


def test_a_filter_change_refreshes_the_banner_summary_out_of_band(director, login, data) -> None:
    full = login(director).get(URL).content.decode()
    assert 'hx-swap-oob="true"' not in full  # only the HTMX response carries it
    partial = login(director).get(URL, {"range": "last_year"}, **HTMX).content.decode()
    assert re.search(r'<p id="banner-summary"[^>]*hx-swap-oob="true"', partial)


def test_next_activity_spotlight_and_the_rest_of_the_upcoming_list(director, login, data) -> None:
    response = login(director).get(URL)
    page, html = response.context["page"], response.content.decode()
    # Nothing is happening now in `data`: the spotlight is the first upcoming activity (ASSUMPTIONS 351)
    assert page.next_activity["activity"] == page.upcoming[0]["activity"] and not page.next_activity["ongoing"]
    assert page.upcoming_after_next == page.upcoming[1:]
    first = page.next_activity
    assert f'aria-label="{first["registered"]} of {first["activity"].max_slots} slots registered"' in html
    assert first["activity"].venue in html and "Open activity" in html


def test_recent_changes_show_the_members_initials(director, login, make_activity) -> None:
    from activities import services as activity_services

    activity = make_activity("Initials Seminar", status="Scheduled", start=today() + timedelta(days=20))  # Conducted is read-only
    activity_services.update_activity(director, activity, {"venue": "TBI Lounge"})
    page = login(director).get(URL).context["page"]
    change = next(c for c in page.recent_changes if "Initials Seminar" in c["sentence"] and "venue" in c["sentence"])
    assert change["initials"] == "TD" and change["member"] == "Test Director"


def test_donut_segments_leave_a_gap_for_their_rounded_ends() -> None:
    from reports.dashboard_page import DONUT_GAP, DONUT_STROKE, _categories

    one = _categories([{"name": "Faculty", "attended": 5}])
    assert one["segments"][0]["length"] == 100 and one["stroke"] == DONUT_STROKE  # a full ring: nothing to separate
    two = _categories([{"name": "Faculty", "attended": 3}, {"name": "MSMEs", "attended": 1}])
    inset = DONUT_STROKE / 2 + DONUT_GAP / 2
    assert [s["length"] for s in two["segments"]] == [75 - 2 * inset, 25 - 2 * inset]
    assert [row["attended"] for row in two["legend"]] == [3, 1] and two["total"] == 4  # numbers unchanged


@pytest.mark.parametrize("start, end, expected", [
    (date(2026, 11, 6), date(2026, 11, 6), "Nov 6, 2026"),
    (date(2026, 10, 30), date(2026, 10, 31), "Oct 30–31, 2026"),
    (date(2026, 10, 30), date(2026, 11, 2), "Oct 30 – Nov 2, 2026"),
    (date(2026, 12, 30), date(2027, 1, 2), "Dec 30, 2026 – Jan 2, 2027"),
])
def test_compact_activity_dates(start, end, expected) -> None:
    from types import SimpleNamespace

    from config.ui_tags import date_span

    assert date_span(SimpleNamespace(start_date=start, end_date=end)) == expected


def test_next_activity_and_upcoming_rows_use_compact_dates_and_a_title_tooltip(director, login, data) -> None:
    from config.ui_tags import date_span

    response = login(director).get(URL)
    page, html = response.context["page"], response.content.decode()
    for row in page.upcoming:
        assert date_span(row["activity"]) in html
    for row in page.upcoming_after_next:
        assert f'title="{row["activity"].title}"' in html
