"""Document rows in a real browser (ASSUMPTIONS 387; Playwright + Chromium). Opt-in, like the other browser tests:
    $env:BROWSER_TESTS = "1"; pytest -n 0 -s documents/tests/test_document_rows_browser.py; Remove-Item Env:BROWSER_TESTS
Long titles on the activity's Documents tab (Required and Other documents), the Documents page (by activity and all
documents) and the document page, at 1366 and 1024 px: a title or meta line wraps by WORDS (never more lines than words:
the old bug squeezed "Accomplishment report" to one or two letters per line), and the page never scrolls sideways.
Screenshots (the content element; the app scrolls inside its panel) go to pytest's tmp_path; with SAVE_SCREENS=1 the
1366 px Documents tab replaces docs/design/screens/documents-tab.png.
"""

import os
from pathlib import Path

import pytest
from django.conf import settings as django_settings
from django.test import Client
from django.urls import reverse

from documents.models import DocumentType

pytestmark = [
    pytest.mark.browser,
    pytest.mark.skipif(os.environ.get("BROWSER_TESTS") != "1", reason="browser test: set BROWSER_TESTS=1"),
]

VIEWPORTS = [(1366, 2400), (1024, 2400)]  # tall: the app scrolls inside its panel; only the width matters here
SCREENS = Path(django_settings.BASE_DIR) / "docs" / "design" / "screens"
LONG_TITLE = "Accomplishment report of the Regional Technology Business Incubation Summit and Investor Day 2026"

# Every document title link and meta line: line boxes (inline: getClientRects) vs words. Letter-by-letter wrapping
# gives far more lines than words.
MEASURE = r"""() => {
  const page = document.documentElement;
  const squeezed = [];
  const check = (el, label) => {
    const text = el.textContent.trim().replace(/\s+/g, ' ');
    if (!text || el.offsetParent === null) return;
    const words = text.split(' ').length;
    const range = document.createRange(); range.selectNodeContents(el);
    const lines = new Set([...range.getClientRects()].map(r => Math.round(r.top))).size;
    if (lines > words) squeezed.push(`${label}: ${lines} lines for ${words} words: ${text.slice(0, 40)}`);
  };
  document.querySelectorAll('a.font-semibold').forEach(a => {
    if (/\/documents\/\d+\//.test(a.getAttribute('href') || '')) check(a, 'title');  // links to a document's page
  });
  document.querySelectorAll('[data-document-title] p').forEach(p => check(p, 'meta'));
  return {pageScroll: page.scrollWidth - page.clientWidth, squeezed,
          titles: document.querySelectorAll('[data-document-title]').length,
          checked: [...document.querySelectorAll('a.font-semibold')].filter(a => /\/documents\/\d+\//.test(a.getAttribute('href') || '')).length};
}"""


@pytest.mark.django_db(transaction=True)
def test_document_rows_wrap_by_words(live_server, tmp_path, monkeypatch, director, make_activity, upload) -> None:
    from playwright.sync_api import sync_playwright

    activity = make_activity(title="Regional Technology Business Incubation Summit")
    upload(director, activity, document_type=DocumentType.objects.get(name="Other"), title=LONG_TITLE)
    upload(director, activity, document_type=DocumentType.objects.get(name="Other"), title="Accomplishment report")
    upload(director, activity, document_type=DocumentType.objects.get(name="Invitation Letter"),
           title="Signed invitation letter for the participants and the resource persons of the summit")
    required = upload(director, activity, title="Request letter to the Vice President for Research, Development "
                                                "and Extension for the use of the function hall")
    client = Client()
    client.force_login(director)
    cookie = client.cookies[django_settings.SESSION_COOKIE_NAME].value
    monkeypatch.setenv("DJANGO_ALLOW_ASYNC_UNSAFE", "true")  # the live server thread uses the ORM while Playwright runs

    pages = [
        ("documents-tab", f"{reverse('activities:detail', args=[activity.pk])}?tab=documents", "#documents-tab"),
        ("documents-by-activity", reverse("documents:list"), "main"),
        ("documents-all", reverse("documents:list") + "?view=all", "main"),
        ("document-detail", reverse("documents:detail", args=[required.pk]), "main"),
    ]
    save = os.environ.get("SAVE_SCREENS") == "1"
    problems = []
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        for width, height in VIEWPORTS:
            for name, url, ready in pages:
                context = browser.new_context(viewport={"width": width, "height": height})
                context.add_cookies([{"name": django_settings.SESSION_COOKIE_NAME, "value": cookie, "url": live_server.url}])
                page = context.new_page()
                page.goto(live_server.url + url)
                page.wait_for_selector(ready)
                box = page.evaluate(MEASURE)
                print(f"{name:22} {width}px  page scroll {box['pageScroll']}  rows {box['titles']}  titles checked {box['checked']}  squeezed {box['squeezed']}")
                if box["pageScroll"] > 0:
                    problems.append(f"{name} at {width}px scrolls sideways by {box['pageScroll']}px")
                problems += [f"{name} at {width}px: {item}" for item in box["squeezed"]]
                if name != "document-detail" and box["checked"] < 4:
                    problems.append(f"{name} at {width}px: only {box['checked']} document titles found")
                if name == "documents-tab" and box["titles"] != 4:
                    problems.append(f"{name} at {width}px shows {box['titles']} document rows, expected 4")
                target = SCREENS if save and width == 1366 and name == "documents-tab" else tmp_path
                # The app scrolls inside its panel: a picture of the content itself, not of the window.
                page.locator(ready).first.screenshot(path=target / f"{name}{'' if width == 1366 else f'-{width}'}.png")
                context.close()
        browser.close()
    assert not problems, "\n".join(problems)
