"""Certificates → Signatories in a real browser (ASSUMPTIONS 407; Playwright + Chromium). Opt-in, like the other browser tests:
    $env:BROWSER_TESTS = "1"; pytest -n 0 -s certificates/tests/test_signatories_browser.py; Remove-Item Env:BROWSER_TESTS
The old bug (UAT 2026-11-17): next to the signature image and up to four buttons, the name and the position were
squeezed into a narrow column, one letter per line. At 1280, 1024 and 390 px every name and position wraps by WORDS
(never more lines than words), the text column is at least 150 px wide and the page never scrolls sideways.
Screenshots of the list go to pytest's tmp_path.
"""

import os

import pytest
from django.conf import settings as django_settings
from django.test import Client
from django.urls import reverse

from certificates import services
from conftest import image_bytes

from .conftest import upload

pytestmark = [
    pytest.mark.browser,
    pytest.mark.skipif(os.environ.get("BROWSER_TESTS") != "1", reason="browser test: set BROWSER_TESTS=1"),
]

VIEWPORTS = [(1280, 1600), (1024, 1600), (390, 2400)]

MEASURE = r"""() => {
  const page = document.documentElement;
  const squeezed = [];
  // Found by the page's structure (not data- hooks), so the check also runs on the old template.
  const blocks = [...document.querySelectorAll('section[aria-labelledby="people-title"] ul > li p.font-semibold')].map(p => p.parentElement);
  blocks.forEach(block => {
    if (block.getBoundingClientRect().width < 150) squeezed.push(`text column ${Math.round(block.getBoundingClientRect().width)}px wide`);
    block.querySelectorAll('p').forEach(p => {
      const text = p.textContent.trim().replace(/\s+/g, ' ');
      if (!text) return;
      const words = text.split(' ').length;
      const range = document.createRange(); range.selectNodeContents(p);
      const lines = new Set([...range.getClientRects()].map(r => Math.round(r.top))).size;
      if (lines > words) squeezed.push(`${lines} lines for ${words} words: ${text.slice(0, 40)}`);
    });
  });
  return {pageScroll: page.scrollWidth - page.clientWidth, squeezed, rows: blocks.length};
}"""


@pytest.mark.django_db(transaction=True)
def test_signatory_names_and_positions_are_readable(live_server, tmp_path, monkeypatch, director) -> None:
    from playwright.sync_api import sync_playwright

    services.add_signatory(director, "Dr. Alexander Bennett", "University President, Caraga State University",
                           upload("sig.png", image_bytes(40, 20)), keep_background=True)  # every button shows
    services.add_signatory(director, "Engr. Maria Clara Dela Cruz-Santos",
                           "Director, CSU Technology Business Incubator", upload("sig2.png", image_bytes(40, 20)))
    services.add_signatory(director, "Prof. Juan Dela Cruz", "Vice President for Research, Development and Extension")
    client = Client()
    client.force_login(director)
    cookie = client.cookies[django_settings.SESSION_COOKIE_NAME].value
    monkeypatch.setenv("DJANGO_ALLOW_ASYNC_UNSAFE", "true")  # the live server thread uses the ORM while Playwright runs

    problems = []
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        for width, height in VIEWPORTS:
            context = browser.new_context(viewport={"width": width, "height": height})
            context.add_cookies([{"name": django_settings.SESSION_COOKIE_NAME, "value": cookie, "url": live_server.url}])
            page = context.new_page()
            page.goto(live_server.url + reverse("certificates:signatories"))
            page.wait_for_selector("section[aria-labelledby=people-title] ul > li")
            box = page.evaluate(MEASURE)
            print(f"signatories {width}px  page scroll {box['pageScroll']}  rows {box['rows']}  squeezed {box['squeezed']}")
            if box["pageScroll"] > 0:
                problems.append(f"{width}px scrolls sideways by {box['pageScroll']}px")
            if box["rows"] != 3:
                problems.append(f"{width}px shows {box['rows']} rows, expected 3")
            problems += [f"{width}px: {item}" for item in box["squeezed"]]
            page.locator("#people-title").locator("xpath=ancestor::section").screenshot(path=tmp_path / f"signatories-{width}.png")
            context.close()
        browser.close()
    assert not problems, "\n".join(problems)
