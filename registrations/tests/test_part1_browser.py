"""Owner feedback Part 1 in a real browser (Playwright + Chromium) at 1366 x 768 and 390 x 844. Opt-in:
    $env:BROWSER_TESTS = "1"; pytest -n 0 -s registrations/tests/test_part1_browser.py; Remove-Item Env:BROWSER_TESTS
1. ASSUMPTIONS 450: Enter in a field of the New activity form does not submit it; Enter in a textarea makes a new line;
   clicking "Create activity" still submits (the server then answers with its checks).
2. ASSUMPTIONS 453: the three signature choices are equal tiles (one row at 1366, stacked at 390, nothing sideways) and
   "Upload a picture" shows its file box.
3. ASSUMPTIONS 454: after the start + 60 minutes the check-in page says to ask the staff (390 px).
Screenshots go to pytest's tmp_path. Every time is set with the `clock` fixture.
"""

import os
from datetime import datetime, time

import pytest
from django.conf import settings as django_settings
from django.test import Client
from django.urls import reverse

from activities.models import Activity

pytestmark = [
    pytest.mark.browser,
    pytest.mark.skipif(os.environ.get("BROWSER_TESTS") != "1", reason="browser test: set BROWSER_TESTS=1"),
]

SIZES = [({"viewport": {"width": 1366, "height": 768}}, "1366"),
         ({"viewport": {"width": 390, "height": 844}, "has_touch": True, "is_mobile": True, "device_scale_factor": 2}, "390")]


def sideways(page) -> int:
    return page.evaluate("() => document.documentElement.scrollWidth - document.documentElement.clientWidth")


@pytest.mark.django_db(transaction=True)
def test_enter_never_submits_the_activity_form(live_server, director, monkeypatch, tmp_path) -> None:
    from playwright.sync_api import sync_playwright

    monkeypatch.setenv("DJANGO_ALLOW_ASYNC_UNSAFE", "true")
    client = Client()
    client.force_login(director)
    cookie = client.cookies[django_settings.SESSION_COOKIE_NAME].value
    url = live_server.url + reverse("activities:create")
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        for options, label in SIZES:
            context = browser.new_context(**options)
            context.add_cookies([{"name": django_settings.SESSION_COOKIE_NAME, "value": cookie, "url": live_server.url}])
            page = context.new_page()
            page.goto(url)
            title = page.locator("input[name=title]")
            title.fill("Enter key test")
            title.press("Enter")
            page.wait_for_timeout(500)
            assert page.url == url, f"{label}: Enter in the title submitted the form"
            description = page.locator("textarea[name=description]")
            if description.count():
                description.fill("Line one")
                description.press("Enter")
                description.type("Line two")
                assert description.input_value() == "Line one\nLine two", label
            assert not Activity.objects.filter(title="Enter key test").exists()
            assert sideways(page) <= 0, label
            page.screenshot(path=str(tmp_path / f"activity-form-{label}.png"))
            page.get_by_role("button", name="Create activity").click()  # clicking still submits (refused or created)
            page.wait_for_load_state()
            assert page.locator(".field-error, [role=alert]").count() or page.url != url, label
            context.close()
        browser.close()


@pytest.mark.django_db(transaction=True)
def test_the_three_signature_choices(live_server, scheduled, monkeypatch, tmp_path) -> None:
    from playwright.sync_api import sync_playwright

    monkeypatch.setenv("DJANGO_ALLOW_ASYNC_UNSAFE", "true")
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        for options, label in SIZES:
            page = browser.new_context(**options).new_page()
            page.goto(live_server.url + reverse("register", args=[scheduled.pk]))
            tiles = page.locator("[data-signature-choice]")
            assert tiles.count() == 3, label
            boxes = [tiles.nth(n).bounding_box() for n in range(3)]
            assert all(box["width"] > 0 and box["height"] >= 44 for box in boxes), label
            widths = {round(box["width"]) for box in boxes}
            assert max(widths) - min(widths) <= 2, f"{label}: tiles of unequal width {widths}"
            same_row = len({round(box["y"]) for box in boxes}) == 1
            assert same_row if label == "1366" else not same_row, label
            page.locator("[data-signature-choice=upload]").click()
            assert page.locator("#id_signature_file").is_visible(), label
            assert sideways(page) <= 0, label
            page.screenshot(path=str(tmp_path / f"signature-choices-{label}.png"), full_page=True)
        browser.close()


@pytest.mark.django_db(transaction=True)
def test_the_check_in_page_after_the_window(live_server, scheduled, clock, monkeypatch, tmp_path) -> None:
    from playwright.sync_api import sync_playwright

    monkeypatch.setenv("DJANGO_ALLOW_ASYNC_UNSAFE", "true")
    clock(datetime.combine(scheduled.start_date, time(9, 30)))  # starts 8:00: closed at 9:00
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        page = browser.new_context(**SIZES[1][0]).new_page()
        page.goto(live_server.url + reverse("checkin", args=[scheduled.pk]))
        text = page.inner_text("body")
        assert "Check-in is closed" in text and "closed at 9:00 AM" in text and "ask the TBI staff" in text
        assert sideways(page) <= 0
        page.screenshot(path=str(tmp_path / "checkin-closed-390.png"), full_page=True)
        browser.close()
