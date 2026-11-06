"""Activities list cards (ASSUMPTIONS 341): three open a dialog with their activities; the dialog, the card's number and
the list's ?preset= (the dialog's "Show all in the list") come from the same query (services.preset_activities).
"Open for registration" is the /register/ rule everywhere (ASSUMPTIONS 77)."""

from datetime import date, datetime, timedelta

import pytest
from django.test import Client
from django.urls import reverse

from activities import pages, services
from activities.models import Activity, ActivityStatus, ActivityType
from registrations.models import FormTemplate, FormType

from .conftest import TEST_NOW, at

pytestmark = pytest.mark.django_db

LIST = reverse("activities:list")
HTMX = {"HTTP_HX_REQUEST": "true"}
NOW = datetime(2026, 11, 15, 12, 0)  # Manila: "this month" is November 2026


@pytest.fixture
def client(director) -> Client:
    client = Client()
    client.force_login(director)
    return client


@pytest.fixture
def world(clock, director, valid_data, move_to, register) -> dict[str, Activity]:
    """One activity per case. Conducted: ended in November (counted this month), overlaps November but ended in
    December, ended in October (both not this month). Scheduled: open, no registration form, full, past its deadline. A Draft."""

    def make(title: str, start: date, end: date, deadline: date, status: str = "", form: bool = True,
             max_slots: int = 30) -> Activity:
        activity = services.create_activity(director, valid_data(
            title=title, start_date=start, end_date=end, max_slots=max_slots,
            registration_deadline=at(deadline), evaluation_deadline=at(end + timedelta(days=7)),
        ), now=TEST_NOW)
        if form:
            FormTemplate.objects.create(activity=activity, form_type=FormType.REGISTRATION, title="R", created_by=director)
        if status:
            move_to(activity, status)
        return activity

    activities = {
        "conducted_nov": make("Pitch Clinic", date(2026, 11, 2), date(2026, 11, 3), date(2026, 11, 1), "Conducted"),
        "conducted_oct": make("Pitch Bootcamp", date(2026, 10, 30), date(2026, 10, 31), date(2026, 10, 29), "Conducted"),
        "conducted_dec": make("Pitch Week", date(2026, 11, 28), date(2026, 12, 2), date(2026, 11, 27), "Conducted"),
        "open": make("Pitch Lab", date(2026, 12, 10), date(2026, 12, 11), date(2026, 12, 5), "Scheduled"),
        "no_form": make("Pitch Talk", date(2026, 12, 10), date(2026, 12, 11), date(2026, 12, 5), "Scheduled", form=False),
        "full": make("Pitch Night", date(2026, 12, 10), date(2026, 12, 11), date(2026, 12, 5), "Scheduled", max_slots=2),
        "past_deadline": make("Pitch Day", date(2026, 11, 20), date(2026, 11, 20), date(2026, 11, 10), "Scheduled"),
        "draft": make("Pitch Draft", date(2026, 12, 10), date(2026, 12, 11), date(2026, 12, 5)),
    }
    register(activities["full"], 2)
    clock(NOW)
    return activities


def rows(response) -> set[str]:
    return {row["activity"].title for row in response.context["rows"]}


PRESET_CARDS = {"open": "open_for_registration", "conducted_month": "conducted_this_month", "conducted": "conducted_total"}


@pytest.mark.parametrize("preset", list(PRESET_CARDS))
def test_each_preset_lists_exactly_the_cards_number(world, client, preset) -> None:
    stats = services.activity_stats()
    response = client.get(LIST, {"preset": preset})
    assert response.context["result"].total == stats[PRESET_CARDS[preset]] == len(rows(response))
    assert response.context["filters"].preset == preset


def test_the_presets_pick_the_right_activities(world, client) -> None:
    titles = lambda *keys: {world[key].title for key in keys}  # noqa: E731
    assert rows(client.get(LIST, {"preset": "open"})) == titles("open")  # not full, past deadline, no form
    assert rows(client.get(LIST, {"preset": "conducted_month"})) == titles("conducted_nov")  # not Oct / Dec ends
    assert rows(client.get(LIST, {"preset": "conducted"})) == titles("conducted_nov", "conducted_oct", "conducted_dec")
    # The date-range filter would also match the activities that overlap November: the preset does not.
    overlap = rows(client.get(LIST, {"date_from": "2026-11-01", "date_to": "2026-11-30", "status": ActivityStatus.objects.get(name="Conducted").pk}))
    assert overlap == titles("conducted_nov", "conducted_dec")


def test_open_for_registration_agrees_everywhere(world, client, director) -> None:
    """The Activities card, the preset list, the dashboard KPI and /register/ count the same activities; a Scheduled
    activity without a registration form is counted nowhere."""
    card = services.activity_stats()["open_for_registration"]
    preset = rows(client.get(LIST, {"preset": "open"}))
    dashboard = client.get(reverse("home")).context["page"].open_count
    public = {item["activity"].title for item in Client().get(reverse("register_list")).context["cards"]}
    assert card == len(preset) == dashboard == len(public) == 1
    assert preset == public == {world["open"].title}
    assert world["no_form"].title not in public


def dialog(client, preset: str):
    return client.get(reverse("activities:card_dialog", args=[preset]), **HTMX)


def dialog_titles(response) -> list[str]:
    return [row["activity"].title for row in response.context["dialog"]["rows"]]


@pytest.mark.parametrize("preset", list(PRESET_CARDS))
def test_each_card_dialog_shows_exactly_the_cards_activities(world, client, preset) -> None:
    card = next(card for card in client.get(LIST).context["cards"] if card["preset"] == preset)
    response = dialog(client, preset)
    html = response.content.decode()
    assert "activities/_card_dialog.html" in [t.name for t in response.templates]
    assert response.context["dialog"]["count"] == card["value"] == len(dialog_titles(response))
    assert f"{card['label']} ({card['value']})" in html and 'id="card-dialog-title"' in html
    for title in dialog_titles(response):
        activity = Activity.objects.get(title=title)
        assert reverse("activities:detail", args=[activity.pk]) in html
    assert set(dialog_titles(response)) == rows(client.get(LIST, {"preset": preset}))


def test_dialog_rows_are_sorted_and_compact(world, client) -> None:
    assert dialog_titles(dialog(client, "conducted")) == ["Pitch Week", "Pitch Clinic", "Pitch Bootcamp"]  # newest first
    html = dialog(client, "open").content.decode()
    assert "CSU Hall" in html and "0/30" in html and "Scheduled" in html  # venue, registered/max, status badge


def test_open_dialog_is_soonest_first(world, client, director, valid_data, move_to) -> None:
    later = services.create_activity(director, valid_data(
        title="Pitch Later", start_date=date(2027, 1, 10), end_date=date(2027, 1, 11),
        registration_deadline=at(date(2027, 1, 5)), evaluation_deadline=at(date(2027, 1, 20)),
    ), now=TEST_NOW)
    FormTemplate.objects.create(activity=later, form_type=FormType.REGISTRATION, title="R", created_by=director)
    move_to(later, "Scheduled")
    assert dialog_titles(dialog(client, "open")) == ["Pitch Lab", "Pitch Later"]


def test_dialog_shows_at_most_the_limit_and_links_to_the_list(world, client, monkeypatch) -> None:
    monkeypatch.setattr(services, "PRESET_LIST_LIMIT", 2)
    response = dialog(client, "conducted")
    html = response.content.decode()
    assert len(dialog_titles(response)) == 2 and "Conducted in total (3)" in html
    assert "Show all in the list" in html and f'href="{LIST}?preset=conducted"' in html
    assert "Show all in the list" not in dialog(client, "conducted_month").content.decode()


@pytest.mark.parametrize("preset, text", [
    ("open", "No activities are open for registration right now."),
    ("conducted_month", "No activities were conducted this month."),
    ("conducted", "No activities have been conducted yet."),
])
def test_empty_dialogs(clock, director, preset, text) -> None:
    clock(NOW)  # before signing in: a later jump would end the session
    client = Client()
    client.force_login(director)
    html = dialog(client, preset).content.decode()
    assert text in html and f"{services.LIST_PRESETS[preset]} (0)" in html


def test_dialog_url_without_htmx_goes_to_the_list_and_refuses_unknown_cards(client) -> None:
    response = client.get(reverse("activities:card_dialog", args=["open"]))
    assert response.status_code == 302 and response["Location"] == f"{LIST}?preset=open"
    assert client.get(reverse("activities:card_dialog", args=["everything"]), **HTMX).status_code == 404
    assert Client().get(reverse("activities:card_dialog", args=["open"])).status_code == 302  # login page


def test_cards_open_the_dialog_and_never_filter_the_list(world, client) -> None:
    response = client.get(LIST)
    html = response.content.decode()
    grid = html[html.index('<div class="grid grid-cols-2'):html.index('<dialog id="card-dialog"')]
    # Activities in total: a plain card, not a link, no hover effect.
    total = grid[:grid.index("<a ")]
    assert "<a " not in total and "Activities in total" in total and "hover-lift" not in total
    # The other three: links (no-JavaScript fallback: the list with the preset) that load the dialog.
    assert grid.count('class="stat-card-link"') == 3 and grid.count('aria-haspopup="dialog"') == 3
    for preset in PRESET_CARDS:
        assert f'href="{LIST}?preset={preset}"' in grid
        assert f'hx-get="{reverse("activities:card_dialog", args=[preset])}"' in grid
    assert 'hx-target="#card-dialog-body"' in grid and "#activity-results" not in grid
    # No highlight, no chip in the normal flow.
    assert "aria-current=\"true\"" not in grid and "ring-2" not in grid and "Showing:" not in html


def test_keyboard_focus_ring_only(client) -> None:
    from django.conf import settings

    assert "stat-card-link" in client.get(LIST).content.decode()
    css = (settings.BASE_DIR / "static" / "css" / "app.css").read_text(encoding="utf-8")
    assert ".stat-card-link:focus-visible" in css


def test_a_preset_combines_with_search_type_category_and_sort(world, client) -> None:
    response = client.get(LIST, {"preset": "conducted", "q": "clinic", "sort": "title"})
    assert rows(response) == {world["conducted_nov"].title}
    workshop = ActivityType.objects.get(name="Workshop").pk
    assert rows(client.get(LIST, {"preset": "conducted", "activity_type": workshop, "q": "week"})) == {world["conducted_dec"].title}


def test_a_contradicting_filter_drops_the_preset(world, client) -> None:
    draft = ActivityStatus.objects.get(name="Draft").pk
    response = client.get(LIST, {"preset": "conducted", "status": draft})
    assert response.context["filters"].preset == "" and rows(response) == {world["draft"].title}
    dated = client.get(LIST, {"preset": "conducted_month", "date_from": "2026-10-01"})
    assert dated.context["filters"].preset == ""
    assert client.get(LIST, {"preset": "conducted", "date_from": "2026-11-01"}).context["filters"].preset == "conducted"


def test_chip_and_clear(world, client) -> None:
    response = client.get(LIST, {"preset": "open", "q": "pitch"})
    html = response.content.decode()
    assert "Showing:" in html and "Open for registration</strong>" in html
    assert f'href="{LIST}?q=pitch"' in html  # Clear keeps the other filters
    cleared = client.get(LIST, {"q": "pitch"})
    assert cleared.context["filters"].preset == "" and "Showing:" not in cleared.content.decode()
    assert len(rows(cleared)) == len(world)


def test_unknown_preset_is_ignored_with_a_note(world, client) -> None:
    response = client.get(LIST, {"preset": "everything"})
    assert response.status_code == 200 and response.context["filters"].preset == ""
    assert "Ignored an unknown list shortcut." in response.content.decode()


def test_htmx_filter_change_refreshes_only_the_hidden_preset_out_of_band(world, client) -> None:
    response = client.get(LIST, {"preset": "open"}, **HTMX)
    html = response.content.decode()
    assert "activities/_results.html" in [t.name for t in response.templates]
    assert 'id="filter-preset"' in html and html.count('hx-swap-oob="true"') == 1 and "stat-card-link" not in html


def test_the_service_refuses_an_unknown_preset() -> None:
    with pytest.raises(services.ActivityError, match="Unknown list preset"):
        services.list_activities({"preset": "everything"})


def test_stat_cards_match_the_presets() -> None:
    assert [preset for preset, *_ in pages.STAT_CARDS] == ["", *services.LIST_PRESETS]
