"""Phase F2a pages: Activities list + calendar, create / edit, the activity page (tabs, status change,
registration link and notice, resource persons). The rules themselves are tested with the services; these
tests check that the pages call them, show their messages in the right place and respect the permissions."""

import re
from html import unescape
from datetime import date, datetime, timedelta

import pytest
from django.test import Client
from django.urls import reverse
from django.utils import timezone

from activities import pages, services
from activities.models import Activity, ActivityType, ParticipantCategory, ResourcePerson
from registrations.models import FormTemplate, FormType

from conftest import creation_document_files, typed_schedule_fields

from .conftest import at

pytestmark = pytest.mark.django_db

LIST = reverse("activities:list")
CREATE = reverse("activities:create")
HTMX = {"HTTP_HX_REQUEST": "true"}


def detail_url(activity, **params) -> str:
    query = "&".join(f"{key}={value}" for key, value in params.items())
    return reverse("activities:detail", args=[activity.pk]) + (f"?{query}" if query else "")


@pytest.fixture
def login():
    def log_in(member) -> Client:
        client = Client()
        client.force_login(member)
        return client

    return log_in


@pytest.fixture
def post_data(valid_data):
    """Factory: the create/edit form's POST data for valid_data(**overrides), as the browser sends it."""

    def make(**overrides) -> dict:
        values = valid_data(**overrides)
        return {
            "title": values["title"],
            "description": values["description"],
            "activity_type": values["activity_type"].pk,
            "participant_categories": [category.pk for category in values["participant_categories"]],
            **typed_schedule_fields(values),  # typed dates, hh:mm + AM/PM (ASSUMPTIONS 422)
            "venue": values["venue"],
            "max_slots": values["max_slots"],
            "budget_amount": str(values["budget_amount"]),
        }

    return make


def messages_of(response) -> list[str]:
    return [str(message) for message in response.context["messages"]]


def field_error(html: str, field: str) -> str:
    """The error text shown under a field (components/form_field.html / choice_pills.html)."""
    match = re.search(rf'id="id_{field}-error"[^>]*>(.*?)</p>', html, re.S)
    return unescape(match.group(1).strip()) if match else ""


def with_registration_form(activity: Activity, member) -> Activity:
    FormTemplate.objects.get_or_create(
        activity=activity, form_type=FormType.REGISTRATION, defaults={"title": "Registration", "created_by": member}
    )
    return activity


# --- List -----------------------------------------------------------------------------------------------------


def test_list_shows_the_stats_and_the_rows(director, login, new_activity) -> None:
    response = login(director).get(LIST)
    html = response.content.decode()
    assert response.status_code == 200 and "activities/list.html" in [t.name for t in response.templates]
    stats = services.activity_stats()
    assert [card["value"] for card in response.context["cards"]] == [
        stats["total"], stats["open_for_registration"], stats["conducted_this_month"], stats["conducted_total"]
    ]
    assert new_activity.title in html and detail_url(new_activity) in html
    assert "0/30" in html  # registered / max slots


def test_list_is_for_every_active_staff_member_but_only_creators_see_new_activity(director, view_only, login) -> None:
    assert CREATE in login(director).get(LIST).content.decode()
    response = login(view_only).get(LIST)
    assert response.status_code == 200 and CREATE not in response.content.decode()
    assert Client().get(LIST).status_code == 302  # login page


def test_filters_reach_the_service_and_the_url_keeps_them(director, login, valid_data) -> None:
    services.create_activity(director, valid_data(title="Pitching Workshop"))
    services.create_activity(director, valid_data(title="Design Seminar", activity_type=ActivityType.objects.get(name="Seminar")))
    client = login(director)
    html = client.get(LIST, {"q": "pitch"}).content.decode()
    assert "Pitching Workshop" in html and "Design Seminar" not in html and "for “pitch”" in html
    seminar = ActivityType.objects.get(name="Seminar")
    html = client.get(LIST, {"activity_type": seminar.pk}).content.decode()
    assert "Design Seminar" in html and "Pitching Workshop" not in html
    category = ParticipantCategory.objects.get(name="Faculty")
    assert "No activities match these filters" in client.get(LIST, {"participant_category": category.pk}).content.decode()
    far = timezone.localdate() + timedelta(days=300)
    assert "No activities match" in client.get(LIST, {"date_from": far.isoformat()}).content.decode()


def test_a_filter_change_with_htmx_gets_only_the_results(director, login, new_activity) -> None:
    response = login(director).get(LIST, {"q": "pitch"}, **HTMX)
    names = [t.name for t in response.templates]
    assert "activities/_results.html" in names and "layouts/app.html" not in names


def test_pages_keep_the_filters(director, login, valid_data) -> None:
    for number in range(services.DEFAULT_PAGE_SIZE + 1):
        services.create_activity(director, valid_data(title=f"Workshop {number:02d}"))
    html = login(director).get(LIST, {"q": "workshop", "sort": "title"}).content.decode()
    assert "Page 1 of 2" in html and "q=workshop&amp;sort=title&amp;page=2" in html


def test_bad_filter_values_are_ignored_with_a_message(director, login, new_activity) -> None:
    html = login(director).get(LIST, {"status": "abc", "sort": "venue", "date_from": "2026-13-40"}).content.decode()
    assert "Ignored the status filter" in html and "Ignored an unknown sort order" in html and "Ignored the From date" in html
    assert new_activity.title in html  # the rest of the list still shows


def test_sort_choices_are_exactly_the_services_sort_options() -> None:
    assert {value for value, _ in pages.SORT_CHOICES} == {services.GROUPED_SORT, *services.SORT_OPTIONS}
    assert pages.SORT_CHOICES[0] == (services.GROUPED_SORT, "Grouped (recommended)")


def test_to_before_from_is_ignored() -> None:
    filters = pages.parse_list_filters({"date_from": "2026-11-10", "date_to": "2026-11-01"})
    assert filters.date_from is None and filters.date_to is None and filters.errors


# --- Calendar -------------------------------------------------------------------------------------------------


def test_calendar_shows_each_activity_as_a_chip_that_opens_it(director, login, new_activity) -> None:
    month = new_activity.start_date.strftime("%Y-%m")
    html = login(director).get(LIST, {"view": "calendar", "month": month}).content.decode()
    assert "badge-draft" in html and detail_url(new_activity) in html
    assert new_activity.start_date.strftime("%B %Y") in html


def test_month_grid_starts_on_monday_and_puts_a_multi_day_activity_on_each_day(director, valid_data) -> None:
    start = date(2026, 11, 30)
    activity = services.create_activity(
        director, valid_data(start_date=start, end_date=start + timedelta(days=2), registration_deadline=at(start - timedelta(days=1)),
                             evaluation_deadline=at(start + timedelta(days=9))),
        now=timezone.make_aware(datetime(2026, 11, 1, 9)),
    )
    grid = pages.month_grid(date(2026, 11, 1), today=date(2026, 11, 15))
    assert all(week[0]["date"].weekday() == 0 for week in grid["weeks"])
    days = {day["date"]: day for week in grid["weeks"] for day in week}
    assert activity in days[date(2026, 11, 30)]["activities"] and activity in days[date(2026, 12, 2)]["activities"]
    assert days[date(2026, 11, 15)]["is_today"] and not days[date(2026, 12, 2)]["in_month"]
    assert (grid["previous"], grid["next"]) == (date(2026, 10, 1), date(2026, 12, 1))


# --- Create ----------------------------------------------------------------------------------------------------


def test_create_page_needs_can_create_activity(director, view_only, login) -> None:
    assert login(director).get(CREATE).status_code == 200
    assert login(view_only).get(CREATE).status_code == 403
    assert login(view_only).post(CREATE, {}).status_code == 403


def test_create_saves_it_as_scheduled_and_opens_the_forms_tab_with_a_toast(director, login, post_data) -> None:
    data = {**post_data(title="New Pitch Clinic"), **creation_document_files()}  # 405, 431: both -> Scheduled
    response = login(director).post(CREATE, data, follow=True)
    activity = Activity.objects.get(title="New Pitch Clinic")
    assert response.redirect_chain[-1][0] == detail_url(activity) + "?tab=forms"  # ASSUMPTIONS 409
    assert activity.status.name == "Scheduled" and activity.audit_logs.filter(field_changed="Status").count() == 2
    assert any(text.startswith('"New Pitch Clinic" was created as Scheduled with 2 documents.')
               for text in messages_of(response))


@pytest.mark.parametrize("overrides, field, message", [
    ({"start_date": date(2020, 1, 1)}, "start_date", "Start date cannot be in the past."),
    ({"registration_deadline": at(date(2020, 1, 1))}, "registration_deadline", "Registration deadline cannot be in the past."),
    ({"participant_categories": []}, "participant_categories", "Choose at least one participant category."),
    ({"max_slots": 0}, "max_slots", "Max slots must be at least 1."),
    ({"title": ""}, "title", "This field is required."),
])
def test_create_shows_each_error_under_its_field(director, login, post_data, overrides, field, message) -> None:
    response = login(director).post(CREATE, post_data(**overrides))
    html = response.content.decode()
    assert response.status_code == 200 and field_error(html, field) == message
    assert 'aria-invalid="true"' in html and not Activity.objects.exists()


def test_end_before_start_and_deadline_rules_are_shown_on_their_fields(director, login, post_data) -> None:
    start = timezone.localdate() + timedelta(days=30)
    data = post_data(end_date=start - timedelta(days=1))
    data.update(registration_deadline=(start + timedelta(days=1)).isoformat(), registration_deadline_time="05:00",
                registration_deadline_period="PM")
    html = login(director).post(CREATE, data).content.decode()
    assert field_error(html, "end_date") == "End date must be on or after the start date."
    assert field_error(html, "registration_deadline") == "Registration deadline must be on or before the start date."


def test_one_day_end_time_before_start_time_is_shown_on_end_time(director, login, post_data) -> None:
    start = timezone.localdate() + timedelta(days=30)
    data = post_data(end_date=start)
    data.update(start_time="05:00", start_time_period="PM", end_time="08:00", end_time_period="AM")
    html = login(director).post(CREATE, data).content.decode()
    assert field_error(html, "end_time") == "End time must be after the start time."
    assert not Activity.objects.exists()


def test_times_are_required_on_the_form(director, login, post_data) -> None:
    data = post_data()
    data.update(start_time="", end_time="")
    html = login(director).post(CREATE, data).content.decode()
    assert field_error(html, "start_time") == "This field is required."
    assert not Activity.objects.exists()


def test_an_inactive_type_gets_the_services_message(director, login, post_data) -> None:
    old = ActivityType.objects.create(name="Old Format", is_active=False)
    client = login(director)
    assert "Old Format" not in client.get(CREATE).content.decode()  # not offered
    html = client.post(CREATE, post_data(activity_type=old)).content.decode()
    assert field_error(html, "activity_type") == 'Activity type "Old Format" is inactive. Choose an active type.'


def test_deadlines_are_typed_and_shown_in_manila_time(director, login, post_data) -> None:
    data = {**post_data(title="Manila Time"), **creation_document_files()}
    data.update(registration_deadline_time="05:30", registration_deadline_period="PM")
    login(director).post(CREATE, data)
    activity = Activity.objects.get(title="Manila Time")
    assert timezone.localtime(activity.registration_deadline).strftime("%H:%M") == "17:30"
    html = login(director).get(reverse("activities:edit", args=[activity.pk])).content.decode()
    assert f'value="{data["registration_deadline"]}"' in html and 'value="05:30"' in html


# --- Edit ------------------------------------------------------------------------------------------------------


def test_edit_saves_only_changes_with_an_audit_row(director, login, new_activity, post_data) -> None:
    url = reverse("activities:edit", args=[new_activity.pk])
    client = login(director)
    assert f'value="{new_activity.venue}"' in client.get(url).content.decode()
    response = client.post(url, post_data(venue="TBI Lounge"), follow=True)
    assert response.redirect_chain[-1][0] == detail_url(new_activity) and "Changes saved." in messages_of(response)
    assert list(new_activity.audit_logs.values_list("field_changed", flat=True)) == ["Venue", "Status"]


def test_max_slots_below_registrations_is_shown_on_max_slots(director, login, new_activity, post_data, register) -> None:
    register(new_activity, 3)
    html = login(director).post(reverse("activities:edit", args=[new_activity.pk]), post_data(max_slots=2)).content.decode()
    assert field_error(html, "max_slots") == "Max slots cannot be lower than the 3 people already registered."


def test_an_untouched_deadline_with_seconds_is_not_seen_as_changed(director, new_activity) -> None:
    with_seconds = new_activity.registration_deadline.replace(second=42)
    Activity.objects.filter(pk=new_activity.pk).update(registration_deadline=with_seconds)
    new_activity.refresh_from_db()
    sent = {"registration_deadline": with_seconds.replace(second=0), "evaluation_deadline": new_activity.evaluation_deadline}
    assert pages.keep_stored_minutes(sent, new_activity)["registration_deadline"] == with_seconds


def test_edit_needs_can_create_activity(view_only, login, new_activity) -> None:
    assert login(view_only).get(reverse("activities:edit", args=[new_activity.pk])).status_code == 403


def test_a_conducted_activity_is_read_only(director, login, new_activity, move_to, post_data) -> None:
    move_to(new_activity, "Conducted")
    client = login(director)
    url = reverse("activities:edit", args=[new_activity.pk])
    response = client.get(url)
    html = response.content.decode()
    assert "activities/read_only.html" in [t.name for t in response.templates]
    assert "This activity is Conducted, so it is read-only." in html and 'name="venue"' not in html
    html = client.post(url, post_data(venue="Somewhere else")).content.decode()
    assert "A Conducted activity is read-only: editing is not allowed." in html
    new_activity.refresh_from_db()
    assert new_activity.venue == "CSU Hall"
    page = client.get(detail_url(new_activity)).content.decode()
    assert url not in page and "Change status" not in page


# --- The activity page -------------------------------------------------------------------------------------------


def test_detail_for_a_creator_has_the_actions(director, login, new_activity) -> None:
    html = login(director).get(detail_url(new_activity)).content.decode()
    assert reverse("activities:edit", args=[new_activity.pk]) in html and "Change status" in html
    assert "Add a resource person" in html and "Pitching Workshop" in html and "₱30,000.00" in html


def test_detail_is_read_only_without_can_create_activity(view_only, login, new_activity) -> None:
    response = login(view_only).get(detail_url(new_activity))
    html = response.content.decode()
    assert response.status_code == 200 and "View only" in html
    assert reverse("activities:edit", args=[new_activity.pk]) not in html
    assert "Change status" not in html and "Add a resource person" not in html


def test_status_modal_offers_only_the_allowed_statuses(director, login, new_activity, move_to) -> None:
    html = login(director).get(detail_url(new_activity)).content.decode()
    offered = re.findall(r'name="status" value="(\w+)"', html)
    assert offered == services.allowed_next_statuses(new_activity) == ["Scheduled"]


def test_history_tab_shows_sentences_newest_first(director, login, new_activity) -> None:
    services.update_activity(director, new_activity, {"venue": "TBI Lounge"})
    response = login(director).get(detail_url(new_activity, tab="history"))
    html = response.content.decode()
    first = html.index("changed the venue of Pitching Workshop to TBI Lounge")
    assert first < html.index("created Pitching Workshop as Draft")


def test_tabs_with_htmx_get_only_the_tab_and_unknown_tabs_show_the_overview(director, login, new_activity) -> None:
    client = login(director)
    response = client.get(detail_url(new_activity, tab="history"), **HTMX)
    names = [t.name for t in response.templates]
    assert "activities/_tab.html" in names and "layouts/app.html" not in names
    assert client.get(detail_url(new_activity, tab="nope")).context["tab"] == "overview"


@pytest.mark.parametrize("tab", ["documents", "certificates"])
def test_every_tab_is_built(director, login, new_activity, tab) -> None:
    """Phase F4 built the last two tabs: no placeholder, no phase badge."""
    html = login(director).get(detail_url(new_activity, tab=tab)).content.decode()
    assert "coming in phase" not in html and f'id="{tab}-tab"' in html


# --- Status changes ----------------------------------------------------------------------------------------------


def test_an_allowed_status_change_shows_the_admins_message(director, login, new_activity) -> None:
    url = reverse("activities:change_status", args=[new_activity.pk])
    response = login(director).post(url, {"status": "Scheduled"}, follow=True)
    assert 'Status of "Pitching Workshop" changed from Draft to Scheduled.' in messages_of(response)
    new_activity.refresh_from_db()
    assert new_activity.status.name == "Scheduled"


def test_a_refused_status_change_shows_why(director, login, new_activity) -> None:
    url = reverse("activities:change_status", args=[new_activity.pk])
    response = login(director).post(url, {"status": "Conducted"}, follow=True)
    [message] = messages_of(response)
    assert message.startswith("Status not changed: Cannot change status from Draft to Conducted.")
    new_activity.refresh_from_db()
    assert new_activity.status.name == "Draft"


def test_status_change_needs_can_create_activity(view_only, login, new_activity) -> None:
    url = reverse("activities:change_status", args=[new_activity.pk])
    assert login(view_only).post(url, {"status": "Scheduled"}).status_code == 403
    assert login(view_only).get(url).status_code in (403, 405)


# --- Registration link and notice ---------------------------------------------------------------------------------


def test_registration_link_only_while_registration_is_open(director, login, new_activity, move_to) -> None:
    client = login(director)
    register_url = reverse("register", args=[new_activity.pk])
    html = client.get(detail_url(new_activity)).content.decode()
    assert "Copy registration link" not in html and pages.FORM_MISSING_NOTICE not in html  # Draft: no notice
    move_to(new_activity, "Scheduled")
    html = client.get(detail_url(new_activity)).content.decode()
    assert pages.FORM_MISSING_NOTICE in html and "Copy registration link" not in html
    # The Share card still shows the link of a Scheduled activity, marked Closed (owner decision, ASSUMPTIONS 332).
    assert register_url in html and ">Closed<" in html
    with_registration_form(new_activity, director)
    html = client.get(detail_url(new_activity)).content.decode()
    assert "Copy registration link" in html and register_url in html and pages.FORM_MISSING_NOTICE not in html


def test_a_full_scheduled_activity_shows_the_closed_reason(director, login, valid_data, move_to, register) -> None:
    activity = services.create_activity(director, valid_data(max_slots=1))
    move_to(activity, "Scheduled")
    with_registration_form(activity, director)
    register(activity, 1)
    html = login(director).get(detail_url(activity)).content.decode()
    assert "Registration is closed: all slots are taken." in html and "Copy registration link" not in html


# --- Resource persons ---------------------------------------------------------------------------------------------


def test_add_a_new_resource_person(director, login, new_activity) -> None:
    url = reverse("activities:add_resource_person", args=[new_activity.pk])
    data = {"mode": "new", "name": "Dr. Ana Reyes", "email": "ana@example.com", "topic": "Pitching"}
    response = login(director).post(url, data, follow=True)
    assert "Dr. Ana Reyes was added as a resource person." in messages_of(response)
    assert new_activity.resource_person_links.get().topic == "Pitching"
    assert "Dr. Ana Reyes" in response.content.decode()


def test_add_an_existing_person_and_refuse_a_duplicate(director, login, new_activity) -> None:
    person = services.create_resource_person(director, "Ben Cruz")
    url = reverse("activities:add_resource_person", args=[new_activity.pk])
    client = login(director)
    client.post(url, {"mode": "existing", "resource_person": person.pk, "topic": "Finance"})
    response = client.post(url, {"mode": "existing", "resource_person": person.pk}, follow=True)
    assert "Ben Cruz is already a resource person for this activity." in messages_of(response)
    assert new_activity.resource_person_links.count() == 1


def test_a_new_person_with_a_known_email_is_refused_and_not_created(director, login, new_activity) -> None:
    services.create_resource_person(director, "Ana Reyes", "ana@example.com")
    url = reverse("activities:add_resource_person", args=[new_activity.pk])
    response = login(director).post(url, {"mode": "new", "name": "Ana R.", "email": "ANA@example.com"}, follow=True)
    assert "A resource person with this email already exists. Pick them from the list instead." in messages_of(response)
    assert ResourcePerson.objects.count() == 1


def test_a_new_person_is_not_kept_when_linking_fails(director, login, new_activity, move_to) -> None:
    move_to(new_activity, "Conducted")
    url = reverse("activities:add_resource_person", args=[new_activity.pk])
    response = login(director).post(url, {"mode": "new", "name": "Late Speaker"}, follow=True)
    assert "A Conducted activity is read-only: changing resource persons is not allowed." in messages_of(response)
    assert not ResourcePerson.objects.filter(name="Late Speaker").exists()


def test_add_without_a_name_or_a_choice_says_what_is_missing(director, login, new_activity) -> None:
    url = reverse("activities:add_resource_person", args=[new_activity.pk])
    client = login(director)
    assert "Name: This field is required." in messages_of(client.post(url, {"mode": "new", "name": " "}, follow=True))
    assert "Choose a resource person from the list." in messages_of(client.post(url, {"mode": "existing"}, follow=True))


def test_remove_a_resource_person(director, login, new_activity) -> None:
    person = services.create_resource_person(director, "Ben Cruz")
    services.add_resource_person(director, new_activity, person)
    url = reverse("activities:remove_resource_person", args=[new_activity.pk, person.pk])
    response = login(director).post(url, follow=True)
    assert "Ben Cruz was removed as a resource person." in messages_of(response)
    assert not new_activity.resource_person_links.exists() and ResourcePerson.objects.filter(pk=person.pk).exists()


def test_resource_person_changes_need_can_create_activity(view_only, director, login, new_activity) -> None:
    person = services.create_resource_person(director, "Ben Cruz")
    client = login(view_only)
    assert client.post(reverse("activities:add_resource_person", args=[new_activity.pk]), {"mode": "new", "name": "X"}).status_code == 403
    assert client.post(reverse("activities:remove_resource_person", args=[new_activity.pk, person.pk])).status_code == 403
    assert client.get(reverse("activities:resource_person_options")).status_code == 403


def test_resource_person_search_list(director, login) -> None:
    services.create_resource_person(director, "Ana Reyes")
    services.create_resource_person(director, "Ben Cruz")
    assert [p.name for p in services.search_resource_persons("an")] == ["Ana Reyes"]
    assert [p.name for p in services.search_resource_persons("")] == ["Ana Reyes", "Ben Cruz"]
    html = login(director).get(reverse("activities:resource_person_options"), {"q": "zz"}, **HTMX).content.decode()
    assert "No one called “zz”" in html


# --- Elsewhere ------------------------------------------------------------------------------------------------------


def test_the_dashboard_opens_the_activity_page(director, login, new_activity, move_to) -> None:
    move_to(new_activity, "Scheduled")
    html = login(director).get(reverse("home")).content.decode()
    assert detail_url(new_activity) in html
