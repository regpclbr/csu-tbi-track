"""The one Reports page, the activity's Reports tab and the For payment cards in a real browser (Playwright +
Chromium), ASSUMPTIONS 461-463. Opt-in:
    $env:BROWSER_TESTS = "1"; pytest -n 0 -s reports/tests/test_reports_page_browser.py; Remove-Item Env:BROWSER_TESTS
At 1366 x 768 and 390 x 844 (touch): the "What do you want to make?" list and every kind's form fit without sideways
scrolling, the figures form keeps its Generate button and notes reachable, the activity's Reports tab shows only the
per-activity kinds, and the For payment cards (Documents page and Dashboard) show each activity's own Generate / Upload
buttons, visible and not covering each other. The clock is fixed (no dependence on today's date)."""

import os
from datetime import date, datetime

import pytest
from django.conf import settings as django_settings
from django.test import Client
from django.urls import reverse
from django.utils import timezone

pytestmark = [
    pytest.mark.browser,
    pytest.mark.skipif(os.environ.get("BROWSER_TESTS") != "1", reason="browser test: set BROWSER_TESTS=1"),
]

SIZES = [(1366, 768, False), (390, 844, True)]
FIT = """(selector) => {
  const panel = document.getElementById('content-panel');
  const visible = el => { const r = el.getBoundingClientRect(); return r.width > 0 && r.height > 0 };
  const root = document.querySelector(selector);
  const wide = root ? [...root.querySelectorAll('*')].filter(visible)
    .filter(el => el.getBoundingClientRect().right > innerWidth + 1).map(el => el.tagName + '.' + el.className).slice(0, 5) : ['missing'];
  return { sideways: document.documentElement.scrollWidth > innerWidth + 1 || panel.scrollWidth > panel.clientWidth + 1,
           wide };
}"""
OVERLAP = """(selector) => {
  const boxes = [...document.querySelectorAll(selector)].map(el => el.getBoundingClientRect()).filter(r => r.width > 0);
  const hits = [];
  boxes.forEach((a, i) => boxes.slice(i + 1).forEach(b => {
    if (a.left < b.right - 1 && b.left < a.right - 1 && a.top < b.bottom - 1 && b.top < a.bottom - 1) hits.push(i);
  }));
  return { count: boxes.length, hits };
}"""


def _contexts(browser, live_server, cookie):
    for width, height, phone in SIZES:
        options = {"viewport": {"width": width, "height": height}}
        if phone:
            options.update(is_mobile=True, has_touch=True, device_scale_factor=2)
        context = browser.new_context(**options)
        context.add_cookies([{"name": django_settings.SESSION_COOKIE_NAME, "value": cookie, "url": live_server.url}])
        yield f"{width}x{height}", context
        context.close()


def _cookie(member) -> str:
    client = Client()
    client.force_login(member)
    return client.cookies[django_settings.SESSION_COOKIE_NAME].value


@pytest.mark.django_db(transaction=True)
def test_the_reports_page_and_the_activity_tab(live_server, make_member, make_activity, clock, monkeypatch) -> None:
    from playwright.sync_api import sync_playwright

    from reports import hub

    clock(timezone.make_aware(datetime(2026, 11, 26, 9, 0)))
    member = make_member("Admin Staff")
    activity = make_activity("Pitching Workshop with a rather long title to check the wrapping", start=date(2026, 10, 20))
    cookie = _cookie(member)
    monkeypatch.setenv("DJANGO_ALLOW_ASYNC_UNSAFE", "true")
    base = live_server.url + reverse("reports:list")
    tab = live_server.url + reverse("activities:detail", args=[activity.pk]) + "?tab=reports"
    problems = []
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        for label, context in _contexts(browser, live_server, cookie):
            page = context.new_page()
            for choice in hub.kind_choices(None):
                page.goto(f"{base}?kind={choice.key}")
                fit = page.evaluate(FIT, "#new-report")
                if fit["sideways"] or fit["wide"]:
                    problems.append(f"{label} {choice.key}: something scrolls sideways ({fit})")
            page.goto(f"{base}?kind=figures-accomplishment")
            page.locator("summary", has_text="More settings").first.click()
            for selector in ("[name='notes']", "[name='report_files']"):
                field = page.locator(selector).first
                field.scroll_into_view_if_needed()
                if not field.is_visible():
                    problems.append(f"{label}: {selector} should be reachable on the figures form")
            button = page.get_by_role("button", name="Generate").last
            button.scroll_into_view_if_needed()
            if not button.is_visible():
                problems.append(f"{label}: the figures form's Generate button should be reachable")

            page.goto(tab)
            links = page.locator("[data-activity-report-kinds] a")
            keys = [links.nth(i).get_attribute("data-kind") for i in range(links.count())]
            if keys != list(hub.PER_ACTIVITY):
                problems.append(f"{label}: the tab should offer only the per-activity kinds ({keys})")
            fit = page.evaluate(FIT, "[data-activity-report-kinds]")
            overlap = page.evaluate(OVERLAP, "[data-activity-report-kinds] a")
            if fit["sideways"] or fit["wide"] or overlap["hits"]:
                problems.append(f"{label}: the tab's kinds overflow or overlap ({fit}, {overlap})")
            links.first.click()
            page.wait_for_load_state()
            if f"activity={activity.pk}" not in page.url or "kind=activity_report" not in page.url:
                problems.append(f"{label}: a tab kind should open the Reports page with the activity ({page.url})")
            page.goto(f"{base}?kind={hub.FORM_ATTENDANCE}&activity={activity.pk}")
            fit = page.evaluate(FIT, "#new-report")
            if fit["sideways"] or fit["wide"]:
                problems.append(f"{label}: the attendance forms overflow ({fit})")
        browser.close()
    assert not problems, "\n".join(problems)


@pytest.mark.django_db(transaction=True)
def test_the_for_payment_cards(live_server, make_member, roles, clock, monkeypatch) -> None:
    from datetime import time, timedelta

    from playwright.sync_api import sync_playwright

    from activities.models import Activity, ActivityStatus, ActivityType, ParticipantCategory

    clock(timezone.make_aware(datetime(2026, 12, 10, 9, 0)))  # the payment documents of Dec 4 are overdue
    director = make_member("Director")
    for title in ("Pitching Workshop", "Mentoring Day with a long title that has to wrap on a phone"):
        activity = Activity.objects.create(
            created_by=director, status=ActivityStatus.objects.get(name=ActivityStatus.CONDUCTED),
            activity_type=ActivityType.objects.get(name="Workshop"), title=title, start_date=date(2026, 12, 4),
            end_date=date(2026, 12, 4), start_time=time(8, 0), end_time=time(17, 0), venue="CSU Hall", max_slots=30, budget_amount=1000,
            registration_deadline=timezone.make_aware(datetime(2026, 12, 2, 17, 0)),
            evaluation_deadline=timezone.make_aware(datetime(2026, 12, 4, 17, 0) + timedelta(days=7)))
        activity.participant_categories.add(ParticipantCategory.objects.get(name="Faculty"))
    cookie = _cookie(director)
    monkeypatch.setenv("DJANGO_ALLOW_ASYNC_UNSAFE", "true")
    problems = []
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        for label, context in _contexts(browser, live_server, cookie):
            page = context.new_page()
            for name, url, root in [("Documents", reverse("documents:list") + "?view=payment", "[data-payment-cards]"),
                                    ("Dashboard", reverse("home"), "[data-payment-card]")]:
                page.goto(live_server.url + url)
                cards = page.locator(f"{root} [data-payment-activity]").count()
                if cards != 2:
                    problems.append(f"{label} {name}: two activity cards expected ({cards})")
                fit = page.evaluate(FIT, root)
                overlap = page.evaluate(OVERLAP, f"{root} [data-payment-action]")
                if fit["sideways"] or fit["wide"] or overlap["hits"] or overlap["count"] < 8:
                    problems.append(f"{label} {name}: cards overflow or buttons overlap ({fit}, {overlap})")
                generate = page.locator(f"{root} [data-payment-action='generate']").first
                generate.scroll_into_view_if_needed()
                if not generate.is_visible():
                    problems.append(f"{label} {name}: Generate should be visible")
            page.goto(live_server.url + reverse("documents:list") + "?view=payment")
            page.locator("[data-payment-cards] [data-payment-action='generate']").first.click()
            page.wait_for_load_state()
            if reverse("reports:list") not in page.url or "activity=" not in page.url:
                problems.append(f"{label}: Generate should open the Reports page with the activity ({page.url})")
        browser.close()
    assert not problems, "\n".join(problems)
