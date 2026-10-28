"""Phase F5: global search (/search/?q=): the first results of the Activities, Documents and Startups lists, with
their totals, "See all" links to the filtered lists, and the empty states. Uses the lists' own services."""

import pytest
from django.test import Client
from django.urls import reverse

from activities import services as activity_services
from activities.tests.conftest import director, new_activity, valid_data  # noqa: F401  (re-exported fixtures)
from config.search import GROUP_SIZE
from documents import services as document_services
from documents.models import DocumentType
from documents.tests.conftest import certificate_for, make_file  # noqa: F401  (fixture re-exported)
from startups.models import Startup

pytestmark = pytest.mark.django_db


@pytest.fixture
def login():
    def log_in(member) -> Client:
        client = Client()
        client.force_login(member)
        return client

    return log_in


def search(client, q: str) -> str:
    return client.get(reverse("search"), {"q": q}).content.decode()


def test_results_are_grouped_with_see_all_links(director, new_activity, login) -> None:
    document = document_services.upload_document(
        director, new_activity, DocumentType.objects.get(name="Invitation Letter"), make_file(), "Pitching invitation"
    )
    Startup.objects.create(name="Pitch Perfect Labs", email="pitch@demo.example.com")
    Startup.objects.create(name="Unrelated Co", email="other@demo.example.com")
    html = search(login(director), "pitch")
    assert reverse("activities:detail", args=[new_activity.pk]) in html and "Pitching Workshop" in html
    assert reverse("documents:detail", args=[document.pk]) in html and "Pitching invitation" in html
    assert "Pitch Perfect Labs" in html and "Unrelated Co" not in html
    for list_name in ("activities:list", "documents:list", "startups:list"):
        assert f'href="{reverse(list_name)}?q=pitch"' in html


def test_each_group_shows_at_most_five_and_the_total(director, valid_data, login) -> None:
    for number in range(GROUP_SIZE + 2):
        activity_services.create_activity(director, valid_data(title=f"Demo Day {number}"))
    html = search(login(director), "demo day")
    assert html.count('class="hover-row block rounded-xl px-2 py-2"') == GROUP_SIZE
    assert '<span class="badge badge-neutral">7</span>' in html and "See all<span class=\"sr-only\"> 7 activities" in html


def test_certificate_evidence_is_never_found(director, new_activity, login, certificate_for) -> None:
    certificate = certificate_for(new_activity)
    from certificates.services import upload_evidence

    upload_evidence(director, certificate, make_file(), "Secret evidence")
    assert "Secret evidence" not in search(login(director), "secret")


def test_empty_states(director, login) -> None:
    client = login(director)
    assert "Type something to search" in search(client, "  ")
    assert "Nothing matches" in search(client, "zzzz-nothing")


def test_the_top_bar_searches_everything(director, login) -> None:
    html = login(director).get(reverse("home")).content.decode()
    assert f'<form action="{reverse("search")}" method="get" role="search"' in html
    assert "Search activities, documents and startups" in html


def test_search_needs_a_staff_login(client) -> None:
    assert client.get(reverse("search"), {"q": "x"}).status_code == 302
