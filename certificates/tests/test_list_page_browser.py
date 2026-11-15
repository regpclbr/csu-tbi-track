"""The Certificates page in a real browser (ASSUMPTIONS 372; Playwright + Chromium). Opt-in, like the other browser tests:
    $env:BROWSER_TESTS = "1"; pytest -n 0 certificates/tests/test_list_page_browser.py -s; Remove-Item Env:BROWSER_TESTS
It fills the TEST database with the demo data (seed_demo: invented people, every email @demo.example.com; never the
development database), signs in the demo Director with force_login and opens both views at 1366, 1280 and 1024 px:
laptop widths (1280 and wider) never scroll sideways (the page or a table); at 1024 px only a table may scroll inside its
card; no status label cut off; the activity groups newest first. Screenshots (1366x1400, the app scrolls inside its
panel) go to pytest's tmp_path; with SAVE_SCREENS=1 the 1366 px ones replace
docs/design/screens/certificates-by-activity.png and certificates-all.png.
"""

import os
from pathlib import Path

import pytest
from django.conf import settings as django_settings
from django.core.management import call_command
from django.test import Client
from django.urls import reverse

pytestmark = [
    pytest.mark.browser,
    pytest.mark.skipif(os.environ.get("BROWSER_TESTS") != "1", reason="browser test: set BROWSER_TESTS=1"),
]

VIEWPORTS = [(1366, 1400), (1280, 800), (1024, 768)]
LAPTOP = 1280
SCREENS = Path(django_settings.BASE_DIR) / "docs" / "design" / "screens"

MEASURE = """() => {
  const page = document.documentElement;
  const tables = [...document.querySelectorAll('#certificate-results .overflow-x-auto')];
  const clipped = [...document.querySelectorAll('#certificate-results td .badge')]
    .filter(b => b.scrollWidth > b.clientWidth + 1).map(b => b.textContent.trim());
  return {pageScroll: page.scrollWidth - page.clientWidth,
          tableScroll: Math.max(0, ...tables.map(t => t.scrollWidth - t.clientWidth)),
          clipped,
          groups: [...document.querySelectorAll('#certificate-results h3 a')].map(a => a.firstChild.textContent.trim())};
}"""


@pytest.mark.django_db(transaction=True)
def test_certificates_page_in_a_browser(live_server, settings, tmp_path, monkeypatch) -> None:
    from playwright.sync_api import sync_playwright

    from accounts.models import TBIMember
    from certificates import services

    settings.DEBUG = True  # seed_demo is demo-only
    call_command("seed_demo", verbosity=0)
    director = TBIMember.objects.get(email="director@demo.example.com")
    client = Client()
    client.force_login(director)
    cookie = client.cookies[django_settings.SESSION_COOKIE_NAME].value
    expected = [entry.activity.title for entry in services.certificates_by_activity().items]
    assert expected, "seed_demo made no certificates"
    monkeypatch.setenv("DJANGO_ALLOW_ASYNC_UNSAFE", "true")  # the live server thread uses the ORM while Playwright runs

    save = os.environ.get("SAVE_SCREENS") == "1"
    problems = []
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        for width, height in VIEWPORTS:
            for view, name in (("", "certificates-by-activity"), ("?view=all", "certificates-all")):
                context = browser.new_context(viewport={"width": width, "height": height})
                context.add_cookies([{"name": django_settings.SESSION_COOKIE_NAME, "value": cookie, "url": live_server.url}])
                page = context.new_page()
                page.goto(live_server.url + reverse("certificates:list") + view)
                page.wait_for_selector("#certificate-results")
                box = page.evaluate(MEASURE)
                print(f"{name:26} {width}px  page scroll {box['pageScroll']}  table scroll {box['tableScroll']}  "
                      f"clipped {box['clipped']}  groups {box['groups'][:3]}")
                if box["pageScroll"] > 0 or (width >= LAPTOP and box["tableScroll"] > 0):
                    problems.append(f"{name} at {width}px scrolls sideways: {box}")
                if box["clipped"]:
                    problems.append(f"{name} at {width}px cuts off {box['clipped']}")
                if not view and box["groups"] != expected:
                    problems.append(f"groups {box['groups']} != {expected}")
                path = (SCREENS if save and width == 1366 else tmp_path) / f"{name}{'' if width == 1366 else f'-{width}'}.png"
                page.screenshot(path=path)
                context.close()
        browser.close()
    print(f"\nScreenshots: {SCREENS if save else tmp_path}")
    assert not problems, "\n".join(problems)
