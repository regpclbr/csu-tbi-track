"""The document page's Back link (ASSUMPTIONS 359): back to where the member came from (the Documents page with its
filters and view, or the activity's Documents tab) through a safe ?next= (internal paths only), and the review text."""

from urllib.parse import quote

import pytest
from django.test import Client, RequestFactory
from django.urls import reverse

from config.links import current_page, is_internal_path
from documents import pages

pytestmark = pytest.mark.django_db

HTMX = {"HTTP_HX_REQUEST": "true"}


@pytest.fixture
def login():
    def log_in(member) -> Client:
        client = Client()
        client.force_login(member)
        return client

    return log_in


def detail(document, next_url: str | None = None) -> str:
    url = reverse("documents:detail", args=[document.pk])
    return f"{url}?next={quote(next_url, safe='')}" if next_url is not None else url


def back_href(html: str) -> str:
    start = html.index('class="link inline-flex items-center gap-1.5 text-sm"')
    return html[html.rindex('href="', 0, start) + 6: html.index('"', html.rindex('href="', 0, start) + 6)]


def test_back_to_the_documents_page_keeps_filters_and_view(director, login, scheduled, upload) -> None:
    document = upload(director, scheduled)
    came_from = "/documents/?view=all&q=invitation&state=waiting"
    html = login(director).get(detail(document, came_from)).content.decode()
    assert back_href(html) == came_from.replace("&", "&amp;") and "Back to Documents" in html
    assert reverse("activities:detail", args=[scheduled.pk]) in html  # the separate link to the activity stays


def test_back_to_the_activity_documents_tab(director, login, scheduled, upload) -> None:
    document = upload(director, scheduled)
    came_from = f"{reverse('activities:detail', args=[scheduled.pk])}?tab=documents"
    html = login(director).get(detail(document, came_from)).content.decode()
    assert back_href(html) == came_from and f"Back to the documents of {scheduled.title}" in html


@pytest.mark.parametrize("bad", [
    "https://evil.example/documents/", "//evil.example/documents/", "/\\evil.example", "javascript:alert(1)",
    "http://testserver/documents/", "documents/",
])
def test_external_or_odd_next_falls_back_to_the_activity_tab(director, login, scheduled, upload, bad) -> None:
    document = upload(director, scheduled)
    html = login(director).get(detail(document, bad)).content.decode()
    assert back_href(html) == f"{reverse('activities:detail', args=[scheduled.pk])}?tab=documents"
    assert "evil.example" not in back_href(html)


def test_no_next_keeps_the_old_link(director, login, scheduled, upload) -> None:
    document = upload(director, scheduled)
    html = login(director).get(detail(document)).content.decode()
    assert f"Back to the documents of {scheduled.title}" in html


def test_lists_link_to_the_page_with_next(director, login, scheduled, upload) -> None:
    document = upload(director, scheduled)
    client = login(director)
    html = client.get("/documents/?view=all&q=Invitation").content.decode()
    assert f"{reverse('documents:detail', args=[document.pk])}?next=%2Fdocuments%2F%3Fview%3Dall%26q%3DInvitation" in html
    html = client.get(f"{reverse('activities:detail', args=[scheduled.pk])}?tab=documents").content.decode()
    expected_next = quote(f"{reverse('activities:detail', args=[scheduled.pk])}?tab=documents", safe="")
    assert f"{reverse('documents:detail', args=[document.pk])}?next={expected_next}" in html


def test_an_htmx_partial_links_back_to_the_browser_page(director, login, scheduled, upload) -> None:
    """The filter form swaps the list with HTMX: the link points to the page in the address bar (HX-Current-URL)."""
    document = upload(director, scheduled)
    html = login(director).get("/documents/", {"view": "all", "q": "Invitation"},
                               HTTP_HX_CURRENT_URL="http://testserver/documents/?view=all&q=Invitation", **HTMX).content.decode()
    assert f"{reverse('documents:detail', args=[document.pk])}?next=%2Fdocuments%2F%3Fview%3Dall%26q%3DInvitation" in html


def test_current_page_ignores_another_sites_hx_current_url() -> None:
    request = RequestFactory().get("/documents/?view=all", HTTP_HX_REQUEST="true", HTTP_HX_CURRENT_URL="https://evil.example/x")
    assert current_page(request) == "/documents/?view=all"
    assert is_internal_path(request, "/documents/") and not is_internal_path(request, "//evil.example")


def test_back_link_labels(director, scheduled, upload) -> None:
    document = upload(director, scheduled)
    factory = RequestFactory()
    assert pages.back_link(factory.get("/", {"next": "/documents/review/"}), document)["label"] == "Back to Documents"
    assert pages.back_link(factory.get("/", {"next": "/search/?q=x"}), document) == {"url": "/search/?q=x", "label": "Back"}


def test_review_text_names_the_director(director, head, login, scheduled, upload) -> None:
    document = upload(head, scheduled)
    html = login(head).get(detail(document)).content.decode()
    assert "Not reviewed yet. The Director (a member with approval rights) approves or returns it." in html
    assert "A member who can create activities (not the uploader)" not in html
