"""Back / forward keeps the filters a list was built with (ASSUMPTIONS 353), checked in a real browser (Playwright).

The bug: HTMX saves a copy of the page's HTML before each pushed URL and puts it back on Back / Forward. A <select>'s
current choice is not part of the HTML (only its `selected` attribute is), so after "change the sort, change another
filter, press Back" the list was the "Start date: oldest first" list while the dropdown said the first option. The fix
(templates/base.html, on `htmx:afterSettle`) writes the current values of every filter form (form[hx-push-url]) into
its attributes after each swap, so the copy HTMX saves before the next swap matches the list it shows. (Writing them on
`htmx:beforeHistorySave` is too late: the control that triggered the request already holds the next value.)

Opt-in like the modal test:
    $env:BROWSER_TESTS = "1"; pytest -n 0 config/tests/test_history_browser.py; Remove-Item Env:BROWSER_TESTS
"""

import os

import pytest
from django.conf import settings as django_settings
from django.core.management import call_command
from django.test import Client
from django.urls import reverse

pytestmark = [
    pytest.mark.browser,
    pytest.mark.skipif(os.environ.get("BROWSER_TESTS") != "1", reason="browser test: set BROWSER_TESTS=1"),
]


@pytest.mark.django_db(transaction=True)
def test_back_restores_the_sort_the_list_was_built_with(live_server, settings, monkeypatch) -> None:
    from playwright.sync_api import sync_playwright

    from accounts.models import TBIMember
    from activities.models import ActivityStatus

    settings.DEBUG = True  # seed_demo is demo-only
    call_command("seed_demo", verbosity=0)
    director = TBIMember.objects.get(email="director@demo.example.com")
    client = Client()
    client.force_login(director)
    cookie = client.cookies[django_settings.SESSION_COOKIE_NAME].value
    conducted = str(ActivityStatus.objects.get(name=ActivityStatus.CONDUCTED).pk)
    monkeypatch.setenv("DJANGO_ALLOW_ASYNC_UNSAFE", "true")

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        context = browser.new_context(viewport={"width": 1366, "height": 768})
        context.add_cookies([{"name": django_settings.SESSION_COOKIE_NAME, "value": cookie, "url": live_server.url}])
        page = context.new_page()
        page.goto(live_server.url + reverse("activities:list"))
        assert page.locator("#filter-sort").input_value() == "grouped"

        page.select_option("#filter-sort", "start_date")  # oldest first
        page.wait_for_url("**sort=start_date**")
        page.wait_for_load_state("networkidle")
        oldest_first = page.locator("#activity-results h3 a").all_inner_texts()

        page.select_option("#filter-status", conducted)  # another filter: HTMX saves the oldest-first page
        page.wait_for_url(f"**status={conducted}**")
        page.wait_for_load_state("networkidle")

        page.go_back()
        page.wait_for_url("**sort=start_date**")
        page.wait_for_load_state("networkidle")
        assert page.locator("#activity-results h3 a").all_inner_texts() == oldest_first
        assert page.locator("#filter-status").input_value() == ""
        assert page.locator("#filter-sort").input_value() == "start_date"  # was "grouped" before the fix
        browser.close()
