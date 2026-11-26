"""The Documents page organised by activity (ASSUMPTIONS 352): services.activity_document_overview (document_checklist's
rule for many activities in fixed queries), services.document_state_counts (the summary cards), and the page: cards,
open/closed activities, missing rows with Upload, Approve/Return only for approvers, filters kept in the URL, the flat
"All documents" view."""

import re

import pytest
from django.db import connection
from django.test import Client
from django.test.utils import CaptureQueriesContext
from django.urls import reverse

from activities.models import ActivityStatus
from documents import pages, services
from documents.models import Document, DocumentType
from reports import metrics

from .conftest import TEST_NOW, make_file

pytestmark = pytest.mark.django_db

URL = reverse("documents:list")
HTMX = {"HTTP_HX_REQUEST": "true"}
REQUIRED = 3  # seeded required types (accounts/seed.py; Invitation Letter and Accomplishment Report optional, 386)


@pytest.fixture
def login():
    def log_in(member) -> Client:
        client = Client()
        client.force_login(member)
        return client

    return log_in


def type_named(name: str) -> DocumentType:
    return DocumentType.objects.get(name=name)


def cards_of(html: str) -> dict[str, str]:
    """Summary card label -> number, as the page shows them (components/kpi_card.html)."""
    found = re.findall(r'text-muted[^"]*">([^<]+)</p>\s*</div>\s*<div[^>]*>\s*<p class="text-2xl[^"]*">([^<]+)</p>', html)
    return {label.strip(): value.strip() for label, value in found}


@pytest.fixture
def world(director, head, make_activity, upload):
    """Scheduled "Pitching Workshop": Activity Design approved, Request Letter returned (2 of the 3 required types
    uploaded, 1 approved), an Invitation Letter waiting (optional: one of the other documents) and an Evaluation Form.
    Ongoing "Market Training": nothing uploaded (3 missing). Draft "Idea Draft": nothing (never listed).
    Draft "Draft With Files": one waiting document (listed; its missing types are not counted)."""
    workshop = make_activity(title="Pitching Workshop")
    approved = upload(head, workshop, document_type=type_named("Activity Design"), title="Design final")
    services.approve_document(director, approved, now=TEST_NOW)
    waiting = upload(head, workshop, document_type=type_named("Invitation Letter"), title="Invitation draft")
    returned = upload(head, workshop, document_type=type_named("Request Letter"), title="Request v1")
    services.return_document(director, returned, "Wrong date.", now=TEST_NOW)
    extra = upload(head, workshop, document_type=type_named("Evaluation Form"), title="Evaluation form copy")
    ongoing = make_activity(ActivityStatus.ONGOING, title="Market Training")
    make_activity(ActivityStatus.DRAFT, title="Idea Draft")
    draft = make_activity(ActivityStatus.DRAFT, title="Draft With Files")
    draft_doc = upload(head, draft, document_type=type_named("Invitation Letter"), title="Early invitation")
    return {"workshop": workshop, "ongoing": ongoing, "draft": draft, "approved": approved, "waiting": waiting,
            "returned": returned, "extra": extra, "draft_doc": draft_doc}


# --- Services ------------------------------------------------------------------------------------------------


def test_overview_matches_document_checklist(world) -> None:
    activities = list(services.overview_activities())
    for entry in services.activity_document_overview(activities, TEST_NOW):
        checklist = services.document_checklist(entry.activity, TEST_NOW)
        expected = [(i.document_type.pk, i.due_date, i.state, i.is_overdue, [d.pk for d in i.documents])
                    for i in checklist["required"]]
        assert [(i.document_type.pk, i.due_date, i.state, i.is_overdue, [d.pk for d in i.documents])
                for i in entry.required] == expected
    workshop = next(e for e in services.activity_document_overview(activities, TEST_NOW) if e.activity == world["workshop"])
    assert (workshop.approved_count, workshop.missing_count, len(workshop.required)) == (1, 1, REQUIRED)
    assert sorted(d.title for d in workshop.other) == ["Evaluation form copy", "Invitation draft"]


def test_overview_lists_ongoing_first_and_skips_drafts_without_documents(world) -> None:
    titles = [a.title for a in services.overview_activities()]
    assert titles[0] == "Market Training" and "Idea Draft" not in titles
    assert set(titles) == {"Market Training", "Pitching Workshop", "Draft With Files"}


def test_overview_takes_a_fixed_number_of_queries(world, make_activity, upload, head) -> None:
    def count() -> int:
        with CaptureQueriesContext(connection) as queries:
            services.activity_document_overview(list(services.overview_activities()), TEST_NOW)
        return len(queries)

    before = count()
    for i in range(3):
        upload(head, make_activity(title=f"More {i}"))
    assert count() == before == 3


def test_state_counts(world) -> None:
    overview = services.activity_document_overview(list(services.overview_activities()), TEST_NOW)
    counts = services.document_state_counts(overview)
    # waiting: invitation draft, evaluation form copy, early invitation (Draft); missing: 1 + 3 (Draft not counted)
    assert counts == {"waiting_for_review": 3, "returned": 1, "approved": 1, "missing": 4}
    s4 = metrics.documents_now(metrics.ReportFilters(TEST_NOW.date(), TEST_NOW.date()), TEST_NOW)
    assert (counts["waiting_for_review"], counts["returned"]) == (s4["waiting_for_review"], s4["returned"])


# --- The page ------------------------------------------------------------------------------------------------


def test_summary_cards_and_the_waiting_label_by_permission(world, director, view_only, login) -> None:
    html = login(director).get(URL).content.decode()
    assert cards_of(html) == {"Waiting for approval": "3", "Returned": "1", "Missing": "4", "Approved": "1"}
    html = login(view_only).get(URL).content.decode()
    assert "Waiting for the Director" in html and "Waiting for approval" not in html


def test_by_activity_is_the_default_with_progress_and_open_state(world, director, login) -> None:
    response = login(director).get(URL)
    html = response.content.decode()
    cards = {card["activity"].title: card for card in response.context["cards"]}
    assert list(cards) == ["Market Training", "Draft With Files", "Pitching Workshop"]  # ongoing, then newest (same date: title)
    assert cards["Pitching Workshop"]["open"] and "1 of 3</span> approved" in html
    assert cards["Pitching Workshop"]["missing"] == 1 and cards["Pitching Workshop"]["percent"] == 33
    # rows: required types in checklist (due-date) order, then the other documents
    types = [line["type_name"] for line in cards["Pitching Workshop"]["lines"]]
    assert set(types[REQUIRED:]) == {"Evaluation Form", "Invitation Letter"} and types[:REQUIRED] == [i.document_type.name for i in
                                                           services.document_checklist(world["workshop"], TEST_NOW)["required"]]


def test_a_complete_activity_starts_closed(director, head, make_activity, upload, login) -> None:
    activity = make_activity(ActivityStatus.CONDUCTED, title="All Done")
    # The activity ends before PAYMENT_DOCUMENTS_FROM: Activity Report and Event Photos do not apply (ASSUMPTIONS 425).
    for document_type in DocumentType.objects.filter(is_active=True, is_required=True).exclude(
            name__in=["Activity Report", "Event Photos"]):
        services.approve_document(director, upload(head, activity, document_type=document_type), now=TEST_NOW)
    response = login(director).get(URL)
    card = response.context["cards"][0]
    assert card["complete"] and not card["open"]
    assert re.search(r'<details class="group card overflow-hidden"\s*>', response.content.decode())


def test_missing_rows_have_an_upload_link(world, director, login) -> None:
    response = login(director).get(URL, {"activity": world["ongoing"].pk})
    lines = response.context["cards"][0]["lines"]
    assert [line["kind"] for line in lines] == ["missing"] * REQUIRED
    design = type_named("Activity Design").pk
    assert f'{reverse("activities:detail", args=[world["ongoing"].pk])}?tab=documents&amp;type={design}#upload' in response.content.decode()


def test_approve_and_return_only_for_approvers(world, director, view_only, login) -> None:
    approve_url = reverse("documents:approve", args=[world["waiting"].pk])
    assert approve_url in login(director).get(URL).content.decode()
    html = login(view_only).get(URL).content.decode()
    assert approve_url not in html and "/return/form/" not in html
    assert reverse("documents:download", args=[world["waiting"].pk]) in html


def test_state_filter_keeps_only_matching_rows_and_activities(world, director, login) -> None:
    response = login(director).get(URL, {"state": "returned"}, **HTMX)
    assert [c["activity"].title for c in response.context["cards"]] == ["Pitching Workshop"]
    assert [line["document"].title for line in response.context["cards"][0]["lines"]] == ["Request v1"]
    assert "documents/_repository.html" in [t.name for t in response.templates]
    assert "layouts/app.html" not in [t.name for t in response.templates]


def test_missing_filter_counts_non_draft_only_and_switches_to_by_activity(world, director, login) -> None:
    response = login(director).get(URL, {"state": "missing", "view": "all"})
    assert response.context["filters"].view == "activity"
    cards = response.context["cards"]
    assert [c["activity"].title for c in cards] == ["Market Training", "Pitching Workshop"]
    assert sum(len(c["lines"]) for c in cards) == 4


def test_search_type_and_ai_search_filters(world, director, login) -> None:
    client = login(director)
    by_title = client.get(URL, {"q": "market"}).context["cards"]
    assert [c["activity"].title for c in by_title] == ["Market Training"] and len(by_title[0]["lines"]) == REQUIRED
    by_document = client.get(URL, {"q": "request"}).context["cards"]
    assert [line.get("document").title for c in by_document for line in c["lines"] if line["kind"] == "document"] == ["Request v1"]
    by_type = client.get(URL, {"document_type": type_named("Activity Design").pk}).context["cards"]
    assert {c["activity"].title for c in by_type} == {"Market Training", "Pitching Workshop", "Draft With Files"}
    assert {line["type_name"] for c in by_type for line in c["lines"]} == {"Activity Design"}
    assert client.get(URL, {"searchable": "true"}).context["cards"] == []  # nothing indexed in tests


def test_filters_stay_in_the_url_cards_and_pager(world, director, login) -> None:
    html = login(director).get(URL, {"q": "pitch", "searchable": "false"}).content.decode()
    assert 'value="pitch"' in html and '<option value="false" selected>AI search: not included</option>' in html
    filters = pages.parse_repository_filters(_query("q=pitch&searchable=false&state=returned"))
    returned_card = next(c for c in pages.summary_cards(dict.fromkeys(["waiting_for_review", "returned", "missing", "approved"], 0),
                                                         filters, True) if c["key"] == "returned")
    assert returned_card["selected"] and returned_card["url"] == "/documents/?q=pitch&searchable=false"  # again = clear
    assert filters.query() == "q=pitch&state=returned&searchable=false"


def test_all_documents_view_keeps_the_flat_list(world, director, login) -> None:
    html = login(director).get(URL, {"view": "all"}).content.decode()
    assert "5 documents" in html and "<table" in html and "Early invitation" in html
    html = login(director).get(URL, {"view": "all", "review_state": "approved"}).content.decode()  # older links
    assert "Design final" in html and "Invitation draft" not in html


def test_unreadable_filters_are_ignored_with_a_note(world, director, login) -> None:
    html = login(director).get(URL, {"view": "grid", "searchable": "maybe", "state": "lost"}).content.decode()
    assert "Some filters could not be read and were ignored: view, state, AI search." in html


def test_certificate_evidence_is_never_shown(world, director, login, certificate_for) -> None:
    certificate = certificate_for(world["workshop"])
    services.upload_document(director, world["workshop"], type_named("Certificate"), make_file(), "Signed copy",
                             related_certificate=certificate)
    assert "Signed copy" not in login(director).get(URL).content.decode()


def _query(text: str):
    from django.http import QueryDict

    return QueryDict(text)


def test_summary_cards_use_the_shared_card_link_style(world, director, login) -> None:
    """No square browser outline: .stat-card-link (outline none, rounded ring on keyboard focus and when selected)."""
    from pathlib import Path

    from django.conf import settings

    html = login(director).get(URL, {"state": "returned"}).content.decode()
    links = re.findall(r'<a href="[^"]*"[^>]*class="stat-card-link"[^>]*>', html)
    assert len(links) == 4 and sum('aria-pressed="true"' in link for link in links) == 1
    assert "Filtered" in html and 'md:grid-cols-4' in html
    css = (Path(settings.BASE_DIR) / "static/css/app.css").read_text(encoding="utf-8").replace(" ", "")
    assert ".stat-card-link:focus,.stat-card-link:focus-visible{outline:none}" in css
    assert ".stat-card-link:focus-visible{box-shadow:0002px#1f6b3e99}" in css  # the primary at 60% (413, green since 459)
    assert ".stat-card-link[aria-pressed=true]{box-shadow:0002pxvar(--color-primary)}" in css
    assert ".stat-card-link[aria-pressed=true].chip{" in css and ".stat-cards-selected.stat-card-link[aria-pressed=false]{" in css


def pressed(html: str) -> dict[str, str]:
    """Summary card label -> its aria-pressed value."""
    found = re.findall(r'aria-pressed="(true|false)" class="stat-card-link".*?text-muted[^"]*">([^<]+)</p>', html, re.S)
    return {label.strip(): value for value, label in found}


def test_the_card_of_the_state_in_the_url_is_pressed(world, director, login) -> None:
    client = login(director)
    assert set(pressed(client.get(URL).content.decode()).values()) == {"false"}  # no filter: nothing pressed
    html = client.get(URL, {"state": "missing"}).content.decode()  # a refresh / a shared link
    assert pressed(html) == {"Waiting for approval": "false", "Returned": "false", "Missing": "true", "Approved": "false"}
    assert "stat-cards-selected" in html
    assert "{#" not in html and "#}" not in html  # a template comment never shows on the page


def test_changing_the_state_dropdown_moves_the_pressed_card(world, director, login) -> None:
    client = login(director)
    # The State dropdown sends the whole filter form with HTMX; the answer re-renders the cards from it.
    response = client.get(URL, {"state": "approved", "q": "", "activity": "", "searchable": ""}, **HTMX)
    assert response.templates[0].name == "documents/_repository.html"
    assert pressed(response.content.decode())["Approved"] == "true"
    response = client.get(URL, {"state": "returned"}, **HTMX)
    marks = pressed(response.content.decode())
    assert marks["Returned"] == "true" and marks["Approved"] == "false"


def test_the_pressed_card_clears_the_filter_again(world, director, login) -> None:
    response = login(director).get(URL, {"state": "returned", "q": "pitch"})
    card = next(c for c in response.context["summary_cards"] if c["selected"])
    assert card["key"] == "returned" and card["url"] == f"{URL}?q=pitch"
    html = login(director).get(card["url"]).content.decode()
    assert set(pressed(html).values()) == {"false"} and "stat-cards-selected" not in html


def test_back_and_forward_get_the_whole_page(world, director, login) -> None:
    """HTMX's history restore (page not in its cache) asks with HX-Request too: it must get the full page, with the
    card of that URL pressed, or a partial would replace the page (also the Activities list)."""
    client = login(director)
    restore = {"HTTP_HX_REQUEST": "true", "HTTP_HX_HISTORY_RESTORE_REQUEST": "true"}
    response = client.get(URL, {"state": "missing"}, **restore)
    assert "layouts/app.html" in [t.name for t in response.templates]
    assert pressed(response.content.decode())["Missing"] == "true"
    activities = client.get(reverse("activities:list"), {"search": "x"}, **restore)
    assert "layouts/app.html" in [t.name for t in activities.templates]
