"""The time boxes in a real browser (Playwright + Chromium; ASSUMPTIONS 422, 432): typed freely, tidied on leaving,
Backspace works, the AM/PM choice is never overwritten, and every time box on the New and Edit forms saves PM.

Opt-in, like the other browser tests:
    $env:BROWSER_TESTS = "1"; pytest -n 0 -s activities/tests/test_typed_time_browser.py; Remove-Item Env:BROWSER_TESTS
"""

import os
from datetime import time, timedelta

import pytest
from django.conf import settings as django_settings
from django.test import Client
from django.urls import reverse
from django.utils import timezone

from activities.models import Activity

pytestmark = [
    pytest.mark.browser,
    pytest.mark.skipif(os.environ.get("BROWSER_TESTS") != "1", reason="browser test: set BROWSER_TESTS=1"),
]

# box id -> the name of its AM/PM select
BOXES = {"id_start_time": "start_time_period", "id_end_time": "end_time_period",
         "id_registration_deadline_time": "registration_deadline_period",
         "id_evaluation_deadline_time": "evaluation_deadline_period"}
# typed -> (while typing, after leaving the box, AM/PM set by the box or "" = untouched)
TYPED = {
    "11:00": ("11:00", "11:00", ""),
    "1100": ("11:00", "11:00", ""),
    "1:30": ("1:30", "01:30", ""),
    "130": ("130", "01:30", ""),
    "9": ("9", "09:00", ""),
    "12:15": ("12:15", "12:15", ""),
    "0140": ("01:40", "01:40", ""),
    "1430": ("14:30", "02:30", "PM"),
    "7:30 pm": ("7:30 pm", "07:30", "PM"),
}


def open_page(live_server, member, path, monkeypatch):
    from playwright.sync_api import sync_playwright

    client = Client()
    client.force_login(member)
    cookie = client.cookies[django_settings.SESSION_COOKIE_NAME].value
    monkeypatch.setenv("DJANGO_ALLOW_ASYNC_UNSAFE", "true")
    playwright = sync_playwright().start()
    browser = playwright.chromium.launch()
    context = browser.new_context(viewport={"width": 1366, "height": 900})
    context.add_cookies([{"name": django_settings.SESSION_COOKIE_NAME, "value": cookie, "url": live_server.url}])
    page = context.new_page()
    page.goto(live_server.url + path)
    return playwright, browser, page


def check_boxes(page) -> None:
    for box_id, period_name in BOXES.items():
        box, period = page.locator(f"#{box_id}"), page.locator(f"select[name={period_name}]")
        for typed, (while_typing, tidy, set_period) in TYPED.items():
            period.select_option("")
            box.fill("")
            box.press_sequentially(typed, delay=20)
            assert box.input_value() == while_typing, (box_id, typed, box.input_value())
            box.blur()
            assert box.input_value() == tidy, (box_id, typed, box.input_value())
            assert period.input_value() == set_period, (box_id, typed, period.input_value())
        # The choice made first is never overwritten by typing a 12-hour time.
        period.select_option("PM")
        box.fill("")
        box.press_sequentially("0140", delay=20)
        box.blur()
        assert (box.input_value(), period.input_value()) == ("01:40", "PM"), box_id
        # Backspace works: 11:00 -> 11:0 -> 11: -> 11, then typing 30 and leaving gives 11:30 (PM kept).
        box.fill("11:00")
        box.press("End")
        box.press("Backspace")
        assert box.input_value() == "11:0", box_id
        box.press("Backspace")
        box.press("Backspace")
        assert box.input_value() == "11", box_id
        box.press_sequentially("30", delay=20)
        box.blur()
        assert (box.input_value(), period.input_value()) == ("11:30", "PM"), box_id


@pytest.mark.django_db(transaction=True)
def test_new_form_time_boxes_and_saving_pm(live_server, director, valid_data, monkeypatch) -> None:
    playwright, browser, page = open_page(live_server, director, reverse("activities:create"), monkeypatch)
    try:
        check_boxes(page)
        values = valid_data(title="Browser PM")
        start = values["start_date"]
        page.fill("#id_title", "Browser PM")
        page.select_option("#id_activity_type", str(values["activity_type"].pk))
        for category in values["participant_categories"]:
            page.locator(f"input[name=participant_categories][value='{category.pk}']").check(force=True)
        for name, day in (("start_date", start), ("end_date", start), ("registration_deadline", start - timedelta(days=2)),
                          ("evaluation_deadline", start + timedelta(days=5))):
            page.fill(f"#id_{name}", f"{day:%B} {day.day}, {day.year}")
        for box_id, typed in (("id_start_time", "1:30"), ("id_end_time", "0340"),
                              ("id_registration_deadline_time", "1100"), ("id_evaluation_deadline_time", "9")):
            page.fill(f"#{box_id}", "")
            page.locator(f"#{box_id}").press_sequentially(typed, delay=20)
            page.locator(f"#{box_id}").blur()
            page.select_option(f"select[name={BOXES[box_id]}]", "PM")
        page.fill("#id_venue", "CSU Hall")
        page.fill("#id_max_slots", "30")
        page.fill("#id_budget_amount", "1000.00")
        page.get_by_role("button", name="Create activity").click()
        page.wait_for_url("**/activities/*/**")
        activity = Activity.objects.get(title="Browser PM")
        assert (activity.start_time, activity.end_time) == (time(13, 30), time(15, 40))
        assert timezone.localtime(activity.registration_deadline).time() == time(23, 0)
        assert timezone.localtime(activity.evaluation_deadline).time() == time(21, 0)
    finally:
        browser.close()
        playwright.stop()


@pytest.mark.django_db(transaction=True)
def test_edit_form_time_boxes_and_saving_pm(live_server, director, new_activity, monkeypatch) -> None:
    playwright, browser, page = open_page(live_server, director, reverse("activities:edit", args=[new_activity.pk]),
                                          monkeypatch)
    try:
        check_boxes(page)
        for box_id, typed in (("id_start_time", "130"), ("id_end_time", "04:15"),
                              ("id_registration_deadline_time", "10:00"), ("id_evaluation_deadline_time", "0800")):
            page.fill(f"#{box_id}", "")
            page.locator(f"#{box_id}").press_sequentially(typed, delay=20)
            page.locator(f"#{box_id}").blur()
            page.select_option(f"select[name={BOXES[box_id]}]", "PM")
        if not page.input_value("#id_evaluation_deadline"):
            day = new_activity.end_date + timedelta(days=5)
            page.fill("#id_evaluation_deadline", f"{day:%B} {day.day}, {day.year}")
        page.get_by_role("button", name="Save changes").click()
        page.wait_for_url(f"**/activities/{new_activity.pk}/**")
        new_activity.refresh_from_db()
        assert (new_activity.start_time, new_activity.end_time) == (time(13, 30), time(16, 15))
        assert timezone.localtime(new_activity.registration_deadline).time() == time(22, 0)
        assert timezone.localtime(new_activity.evaluation_deadline).time() == time(20, 0)
    finally:
        browser.close()
        playwright.stop()
