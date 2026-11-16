"""Reports -> New report in a real browser (Playwright + Chromium; ASSUMPTIONS 384): type, check, generate.

Opt-in, like the other browser tests:
    $env:BROWSER_TESTS = "1"; pytest -n 0 reports/tests/test_report_form_browser.py; Remove-Item Env:BROWSER_TESTS
Gemini is the FakeGemini of the root conftest (never the real API). Checks at 1366x768: More settings is closed; Send is
disabled while empty; Shift + Enter adds a line and sends nothing; Enter sends ("Reading your request…" and a disabled
Send meanwhile); the prompt box and the summary card fit without scrolling; the summary card and the full form mirror each
other both ways; Custom opens More settings; Start over and an example chip; Generate makes the report with those values.
"""

import os
import time

import pytest
from django.conf import settings as django_settings
from django.test import Client
from django.urls import reverse

from activities.models import ActivityType
from assistant.testing import FakeGemini
from reports import request_reader, services
from reports.models import GeneratedReport

pytestmark = [
    pytest.mark.browser,
    pytest.mark.skipif(os.environ.get("BROWSER_TESTS") != "1", reason="browser test: set BROWSER_TESTS=1"),
]

FITS = "(id) => { const r = document.getElementById(id).getBoundingClientRect(); return r.bottom + window.scrollY <= window.innerHeight }"
IS_OPEN = "() => document.getElementById('report-more').open"


def slow_tool(**args):
    def reply(**kwargs):
        time.sleep(0.8)  # long enough to see the waiting state
        return FakeGemini.call(request_reader.TOOL_NAME, **args)
    return reply


@pytest.mark.django_db(transaction=True)
def test_type_check_generate(live_server, director, fake_gemini, monkeypatch) -> None:
    from playwright.sync_api import sync_playwright

    monkeypatch.setattr(services, "queue_build", lambda report_id: None)
    training = ActivityType.objects.get(name="Training")
    fake_gemini.replies += [
        slow_tool(report_type="participation", period_words="this quarter", activity_type="Training", format="excel"),
        FakeGemini.call(request_reader.TOOL_NAME, report_type="evaluation"),
    ]
    client = Client()
    client.force_login(director)
    cookie = client.cookies[django_settings.SESSION_COOKIE_NAME].value
    monkeypatch.setenv("DJANGO_ALLOW_ASYNC_UNSAFE", "true")

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        context = browser.new_context(viewport={"width": 1366, "height": 768})
        context.add_cookies([{"name": django_settings.SESSION_COOKIE_NAME, "value": cookie, "url": live_server.url}])
        page = context.new_page()
        page.goto(live_server.url + reverse("reports:list"))
        page.wait_for_timeout(300)

        box, send = page.locator("#report-request"), page.locator("#report-send")
        assert not page.evaluate(IS_OPEN), "More settings starts closed"
        assert page.evaluate(FITS, "report-request") and page.locator(".report-example").first.is_visible()
        assert send.is_disabled()
        box.press("Enter")  # empty: nothing is sent
        box.type("Line one")
        box.press("Shift+Enter")
        box.type("line two")
        assert box.input_value() == "Line one\nline two" and send.is_enabled()
        assert fake_gemini.generate_calls == []

        box.fill("Participation report for this quarter, trainings only, in Excel")
        box.press("Enter")  # sends
        page.wait_for_selector("#report-fill-loading:visible >> text=Reading your request")
        assert send.is_disabled()
        page.wait_for_selector("#report-summary")
        page.wait_for_timeout(300)
        assert [turn.text for turn in fake_gemini.generate_calls[0]["turns"]] == [
            "Participation report for this quarter, trainings only, in Excel"]

        # Visible without scrolling at 1366x768, More settings still closed.
        assert page.evaluate(FITS, "report-summary"), "the summary card must fit in the window"
        assert not page.evaluate(IS_OPEN)

        # The summary card shows the form's values ...
        assert page.locator("#summary-type").input_value() == "participation"
        assert page.locator("input[name=report_type]:checked").input_value() == "participation"
        assert page.locator("#summary-range").input_value() == page.locator("input[name=range]:checked").input_value() == "this_quarter"
        assert page.locator("#summary-activity-type").input_value() == page.locator("#report-activity-type").input_value() == str(training.pk)
        assert page.locator("button[data-format=excel]").get_attribute("aria-pressed") == "true"

        # ... changing the summary changes the form ...
        page.locator("#summary-type").select_option("evaluation")
        assert page.locator("input[name=report_type]:checked").input_value() == "evaluation"
        assert page.locator("input[name=included_sections]:checked").count() == 3  # the type's usual sections
        assert page.locator("#summary-sections").inner_text().startswith("3 sections")
        page.locator("button[data-format=pdf]").click()
        assert page.locator("input[name=format]:checked").input_value() == "pdf"
        page.locator("#summary-activity-type").select_option("")
        assert page.locator("#report-activity-type").input_value() == ""

        # ... and changing the form changes the summary.
        page.locator("#summary-sections").click()  # opens More settings at the sections
        assert page.evaluate(IS_OPEN)
        page.locator("label:has(input[name=format][value=excel])").click()
        assert page.locator("button[data-format=excel]").get_attribute("aria-pressed") == "true"
        page.locator("label:has(input[name=report_type][value=participation])").click()
        assert page.locator("#summary-type").input_value() == "participation"
        page.locator("#report-activity-type").select_option(str(training.pk))
        assert page.locator("#summary-activity-type").input_value() == str(training.pk)

        # Custom in the summary opens More settings at the dates.
        page.evaluate("() => { document.getElementById('report-more').open = false }")
        page.locator("#summary-range").select_option("custom")
        assert page.evaluate(IS_OPEN)
        page.locator("#summary-range").select_option("this_quarter")

        # Generate: the unchanged request_report with these values.
        page.locator("#summary-generate").click()
        page.wait_for_url("**" + reverse("reports:list"))
        report = GeneratedReport.objects.get()
        assert (report.report_type, report.format, report.activity_type_id) == ("participation", "excel", training.pk)

        # Start over: a clean page; an example chip fills the box and sends it.
        page.goto(live_server.url + reverse("reports:list"))
        chip = page.locator(".report-example").nth(2)
        text = chip.get_attribute("value")
        chip.click()
        page.wait_for_selector("#report-summary")
        assert page.locator("#report-request").input_value() == text
        assert [turn.text for turn in fake_gemini.generate_calls[1]["turns"]] == [text]
        assert page.locator("#summary-type").input_value() == "evaluation"
        page.get_by_role("link", name="Start over").click()
        page.wait_for_selector(".report-example")
        assert page.locator("#report-summary").count() == 0
        browser.close()
