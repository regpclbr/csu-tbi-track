"""The report generator page in a real browser (Playwright + Chromium), ASSUMPTIONS 441-442. Opt-in:
    $env:BROWSER_TESTS = "1"; pytest -n 0 -s reports/tests/test_generator_browser.py; Remove-Item Env:BROWSER_TESTS
At 1366 x 768 and 390 x 844 (touch): the same compact chat bar as the AI Assistant ([box][Send], one line, Send on the
right, 44 px), the conversation gets most of its card, the draft's sections and buttons are reachable, nothing scrolls
sideways and the page itself does not scroll; on a phone a Chat / Draft switch shows one at a time. The New draft form
shows each kind's own fields (ASSUMPTIONS 446-447) without sideways scrolling. Fake Gemini only."""

import json
import os
from datetime import date

import pytest
from django.conf import settings as django_settings
from django.test import Client
from django.urls import reverse

from assistant.testing import FakeGemini
from reports import generator
from reports.models import ReportDraft

pytestmark = [
    pytest.mark.browser,
    pytest.mark.skipif(os.environ.get("BROWSER_TESTS") != "1", reason="browser test: set BROWSER_TESTS=1"),
]

MEASURE = """() => {
  const box = document.getElementById('gen-message'), send = document.getElementById('gen-send');
  const b = box.getBoundingClientRect(), s = send.getBoundingClientRect();
  const chat = document.getElementById('gen-chat'), scroll = document.getElementById('gen-scroll');
  const panel = document.getElementById('content-panel');
  const visible = el => { const r = el.getBoundingClientRect(); return r.width > 0 && r.height > 0 };
  return {
    boxHeight: b.height, lineHeight: parseFloat(getComputedStyle(box).lineHeight),
    sendRight: s.left >= b.right - 1 && s.top < b.bottom && s.bottom > b.top, sendSize: Math.min(s.width, s.height),
    chatVisible: visible(chat), draftVisible: visible(document.getElementById('gen-draft')),
    conversationShare: visible(chat) ? scroll.getBoundingClientRect().height / chat.getBoundingClientRect().height : 0,
    sideways: document.documentElement.scrollWidth > innerWidth + 1 || panel.scrollWidth > panel.clientWidth + 1,
    pageScrolls: panel.scrollHeight > panel.clientHeight + 1 || document.documentElement.scrollHeight > innerHeight + 1,
    sendVisible: s.top >= 0 && s.bottom <= innerHeight && s.right <= innerWidth,
  };
}"""


@pytest.mark.django_db(transaction=True)
def test_the_generator_page_at_1366_and_390(live_server, make_member, make_activity, fake_gemini, monkeypatch) -> None:
    from playwright.sync_api import sync_playwright

    monkeypatch.setattr("django_q.tasks.async_task", lambda *a, **k: None)
    member = make_member("Admin Staff")
    activity = make_activity("Pitching Workshop", start=date(2026, 10, 20))
    draft = generator.create_draft(member, kind=ReportDraft.Kind.ACTIVITY_REPORT, activity_id=activity.pk,
                                   message="Partners were DTI Agusan and the city LGU.")
    fake_gemini.replies.append(FakeGemini.say(json.dumps({"sections": {
        "highlights": "A long highlight paragraph. " * 40, "accomplishments": "One\nTwo\nThree",
        "problems": "None reported.", "recommendations": "Earlier registration\nMore MSMEs"}})))
    generator.generate_version(generator.latest_version(draft).pk)
    client = Client()
    client.force_login(member)
    cookie = client.cookies[django_settings.SESSION_COOKIE_NAME].value
    monkeypatch.setenv("DJANGO_ALLOW_ASYNC_UNSAFE", "true")
    url = live_server.url + reverse("reports:generator_draft", args=[draft.pk])

    problems = []
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        for width, height, phone in [(1366, 768, False), (390, 844, True)]:
            options = {"viewport": {"width": width, "height": height}}
            if phone:
                options.update(is_mobile=True, has_touch=True, device_scale_factor=2)
            context = browser.new_context(**options)
            context.add_cookies([{"name": django_settings.SESSION_COOKIE_NAME, "value": cookie, "url": live_server.url}])
            page = context.new_page()
            page.goto(url)
            page.wait_for_timeout(400)
            label = f"{width}x{height}"
            fit = page.evaluate(MEASURE)
            print(f"\n{label}: {fit}")
            if fit["boxHeight"] > 1.5 * fit["lineHeight"] + 24:
                problems.append(f"{label}: the box should start one line tall ({fit['boxHeight']} px)")
            if not fit["sendRight"] or fit["sendSize"] < 44 or not fit["sendVisible"]:
                problems.append(f"{label}: Send should be visible on the right of the box, 44 px ({fit})")
            if fit["conversationShare"] < 0.68:
                problems.append(f"{label}: the conversation should fill most of its card ({fit['conversationShare']:.2f})")
            if fit["sideways"] or fit["pageScrolls"]:
                problems.append(f"{label}: the page scrolls or something scrolls sideways ({fit})")
            if phone:
                if fit["draftVisible"]:
                    problems.append(f"{label}: on a phone the draft is behind the Draft switch")
                page.get_by_role("tab", name="Draft").click()
                page.wait_for_timeout(200)
                after = page.evaluate(MEASURE)
                if not after["draftVisible"] or after["chatVisible"] or after["sideways"]:
                    problems.append(f"{label}: the Draft switch should show only the draft ({after})")
            elif not fit["draftVisible"]:
                problems.append(f"{label}: the draft should be next to the conversation")
            pdf = page.locator("#gen-draft a", has_text="PDF").first
            pdf.scroll_into_view_if_needed()
            if not pdf.is_visible():
                problems.append(f"{label}: the PDF download should be reachable")
            save = page.get_by_role("button", name="Save as a new version").first
            save.scroll_into_view_if_needed()
            if not save.is_visible():
                problems.append(f"{label}: a section's Save button should be reachable")
            context.close()
        browser.close()
    assert not problems, "\n".join(problems)


FORM_MEASURE = """() => {
  const panel = document.getElementById('content-panel');
  const visible = el => { const r = el.getBoundingClientRect(); return r.width > 0 && r.height > 0 };
  const wide = [...document.querySelectorAll('#new-draft-title ~ form *')].filter(visible)
    .filter(el => el.getBoundingClientRect().right > innerWidth + 1).map(el => el.tagName + '#' + el.id).slice(0, 5);
  return { sideways: document.documentElement.scrollWidth > innerWidth + 1 || panel.scrollWidth > panel.clientWidth + 1,
           wide };
}"""


@pytest.mark.django_db(transaction=True)
def test_the_new_draft_form_for_every_kind_at_1366_and_390(live_server, make_member, make_activity, monkeypatch) -> None:
    """ASSUMPTIONS 446-447: the Accomplishment Report's period and program name, the draft letter / design's activity
    choice and typed details (with the budget lines) are visible for their kind only, and nothing scrolls sideways."""
    from playwright.sync_api import sync_playwright

    from activities.models import ActivityStatus

    member = make_member("Admin Staff")
    make_activity("Planned Pitch Night", status=ActivityStatus.DRAFT, start=date(2026, 12, 3))
    client = Client()
    client.force_login(member)
    cookie = client.cookies[django_settings.SESSION_COOKIE_NAME].value
    monkeypatch.setenv("DJANGO_ALLOW_ASYNC_UNSAFE", "true")
    url = live_server.url + reverse("reports:generator")
    problems = []
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        for width, height, phone in [(1366, 768, False), (390, 844, True)]:
            options = {"viewport": {"width": width, "height": height}}
            if phone:
                options.update(is_mobile=True, has_touch=True, device_scale_factor=2)
            context = browser.new_context(**options)
            context.add_cookies([{"name": django_settings.SESSION_COOKIE_NAME, "value": cookie, "url": live_server.url}])
            page = context.new_page()
            page.goto(url)
            label = f"{width}x{height}"
            for kind, shown, hidden in [
                ("Accomplishment Report", "#gen-program", "#gen-proposal-activity"),
                ("Request Letter (draft)", "#gen-proposal-activity", "#gen-program"),
                ("Activity Design (draft)", "#d-appr", "#m-for"),
                ("Activity Report", "#gen-activity", "#gen-program"),
            ]:
                page.locator("label", has_text=kind).first.click()
                page.wait_for_timeout(150)
                if not page.locator(shown).is_visible() or page.locator(hidden).is_visible():
                    problems.append(f"{label} {kind}: {shown} should show and {hidden} hide")
                fit = page.evaluate(FORM_MEASURE)
                if fit["sideways"] or fit["wide"]:
                    problems.append(f"{label} {kind}: something scrolls sideways ({fit})")
            page.locator("label", has_text="Activity Design (draft)").first.click()
            page.get_by_text("Type the details (no activity yet)").click()
            page.get_by_text("Budget lines").first.click()
            page.wait_for_timeout(150)
            for selector in ("#p-title", "[name='budget-0-particulars']", "[name='budget-7-unit_price']"):
                field = page.locator(selector)
                field.scroll_into_view_if_needed()
                if not field.is_visible():
                    problems.append(f"{label}: {selector} should be reachable")
            fit = page.evaluate(FORM_MEASURE)
            if fit["sideways"] or fit["wide"]:
                problems.append(f"{label} typed details: something scrolls sideways ({fit})")
            context.close()
        browser.close()
    assert not problems, "\n".join(problems)
