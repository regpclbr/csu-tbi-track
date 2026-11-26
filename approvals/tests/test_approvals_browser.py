"""The "For your approval" inbox and the Assign dialog in a real browser (ASSUMPTIONS 437; Playwright + Chromium). Opt-in:
    $env:BROWSER_TESTS = "1"; pytest -n 0 -s approvals/tests/test_approvals_browser.py; Remove-Item Env:BROWSER_TESTS
At 1366 and 390 px:
  - the inbox lists every kind with an Open button; no two buttons overlap, none sticks out, the page never scrolls sideways;
  - the Assign dialog opens from the activity page, fits the window (inside the viewport, its own scrolling), its fields
    and buttons are visible and do not overlap, and assigning through it creates the assignment;
  - "Assigned to me" shows it for the assignee;
  - the general "Assign work" dialog (ASSUMPTIONS 468) assigns every kind and fits the window.
Screenshots go to pytest's tmp_path (the -s output prints the folder).
"""

import os

import pytest
from django.conf import settings as django_settings
from django.test import Client
from django.urls import reverse

from approvals.models import WorkAssignment
from documents.tests.test_page_buttons_browser import MEASURE

from .conftest import NOW, pdf_file

pytestmark = [
    pytest.mark.browser,
    pytest.mark.skipif(os.environ.get("BROWSER_TESTS") != "1", reason="browser test: set BROWSER_TESTS=1"),
]

WIDTHS = [1366, 390]

DIALOG = r"""() => {
  const dialog = document.querySelector('dialog[data-assign-dialog]');
  const r = dialog.getBoundingClientRect();
  const fields = [...dialog.querySelectorAll('select, textarea, input:not([type=hidden]), button')]
    .map(el => el.getBoundingClientRect()).filter(b => b.width > 0);
  const outside = fields.filter(b => b.left < r.left - 1 || b.right > r.right + 1).length;
  return {open: dialog.open, left: r.left, right: r.right, top: r.top, bottom: r.bottom,
          vw: document.documentElement.clientWidth, vh: window.innerHeight, outside};
}"""


def cookie_for(member) -> str:
    client = Client()
    client.force_login(member)
    return client.cookies[django_settings.SESSION_COOKIE_NAME].value


@pytest.mark.django_db(transaction=True)
def test_inbox_and_assign_dialog(live_server, tmp_path, monkeypatch, director, staff, view_only, activity, submit) -> None:
    from playwright.sync_api import sync_playwright

    from activities.models import Activity

    # Something of each kind waits for the Director: a submission, prepared certificates.
    submit(staff, title="Request letter for the use of the function hall at the CSU Hiraya building", activity=activity)
    submit(staff, title="Budget proposal")
    Activity.objects.filter(pk=activity.pk).update(certificates_prepared_by=director, certificates_prepared_at=NOW)
    monkeypatch.setenv("DJANGO_ALLOW_ASYNC_UNSAFE", "true")
    director_cookie, assignee_cookie = cookie_for(director), cookie_for(view_only)
    detail = reverse("activities:detail", args=[activity.pk])

    problems = []
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        for width in WIDTHS:
            context = browser.new_context(viewport={"width": width, "height": 800}, reduced_motion="reduce")
            context.add_cookies([{"name": django_settings.SESSION_COOKIE_NAME, "value": director_cookie,
                                  "url": live_server.url}])
            page = context.new_page()

            page.goto(live_server.url + reverse("approvals:inbox"))
            page.wait_for_load_state("networkidle")
            assert page.locator("[data-inbox-item]").count() == 3  # 2 submissions + 1 certificate approval
            box = page.evaluate(MEASURE, width >= 1280)
            page.screenshot(path=str(tmp_path / f"inbox-{width}.png"), full_page=True)
            problems += [f"inbox at {width}px: {item}" for item in box["problems"]]
            if box["pageScroll"] > 0:
                problems.append(f"inbox at {width}px scrolls sideways by {box['pageScroll']}px")

            page.goto(live_server.url + detail)
            page.wait_for_load_state("networkidle")
            page.click("[data-assign-trigger]")
            page.wait_for_selector("dialog[data-assign-dialog][open]")
            dialog = page.evaluate(DIALOG)
            page.screenshot(path=str(tmp_path / f"assign-dialog-{width}.png"))
            if not (dialog["left"] >= 0 and dialog["right"] <= dialog["vw"] + 1 and dialog["top"] >= 0
                    and dialog["bottom"] <= dialog["vh"] + 1):
                problems.append(f"Assign dialog at {width}px is outside the window: {dialog}")
            if dialog["outside"]:
                problems.append(f"Assign dialog at {width}px: {dialog['outside']} controls stick out of it")
            box = page.evaluate(MEASURE, False)
            problems += [f"Assign dialog at {width}px: {item}" for item in box["problems"] if "Assign" in item or "Cancel" in item]

            if width == WIDTHS[0]:  # assign once, through the dialog
                page.select_option("#assign-kind", "activity")
                page.select_option("#assign-member", str(view_only.pk))
                page.fill("#assign-note", "Please check the venue")
                page.click("dialog[data-assign-dialog] button[type=submit]")
                page.wait_for_load_state("networkidle")
            context.close()

        assert WorkAssignment.objects.filter(assigned_to=view_only, status="open").count() == 1
        context = browser.new_context(viewport={"width": 390, "height": 800}, reduced_motion="reduce")
        context.add_cookies([{"name": django_settings.SESSION_COOKIE_NAME, "value": assignee_cookie, "url": live_server.url}])
        page = context.new_page()
        page.goto(live_server.url + reverse("approvals:assigned"))
        page.wait_for_load_state("networkidle")
        assert "Please check the venue" in page.content()
        box = page.evaluate(MEASURE, False)
        problems += [f"Assigned to me at 390px: {item}" for item in box["problems"]]
        page.screenshot(path=str(tmp_path / "assigned-390.png"), full_page=True)
        browser.close()
    print(f"\nScreenshots: {tmp_path}")
    assert not problems, "\n".join(problems)


GENERAL_DIALOG = r"""() => {
  const dialog = document.querySelector('dialog[data-assign-any-dialog]');
  const r = dialog.getBoundingClientRect();
  const fields = [...dialog.querySelectorAll('select, textarea, input:not([type=hidden]), button')]
    .map(el => el.getBoundingClientRect()).filter(b => b.width > 0);
  const outside = fields.filter(b => b.left < r.left - 1 || b.right > r.right + 1).length;
  return {open: dialog.open, left: r.left, right: r.right, top: r.top, bottom: r.bottom,
          vw: document.documentElement.clientWidth, vh: window.innerHeight, outside,
          sideways: document.documentElement.scrollWidth > document.documentElement.clientWidth + 1};
}"""


@pytest.mark.django_db(transaction=True)
def test_the_director_assigns_every_kind_from_the_general_dialog(live_server, monkeypatch, director, staff, head, activity,
                                                                 submit, director_signature) -> None:
    """ASSUMPTIONS 468: Dashboard -> "Assign work": WHAT (each kind in plain words), the target, the member, Assign; at
    1366 and 390 px the dialog fits the window, and each For-your-approval item's Assign opens it with its work chosen."""
    from playwright.sync_api import sync_playwright

    from activities.models import Activity
    from documents import services as document_services
    from documents.models import DocumentType

    Activity.objects.filter(pk=activity.pk).update(certificates_prepared_by=director, certificates_prepared_at=NOW)
    DocumentType.objects.filter(name="Invitation Letter").update(needs_review=True)
    document_services.upload_document(staff, activity, DocumentType.objects.get(name="Invitation Letter"), pdf_file(),
                                      "Invitation", now=NOW)
    submit(staff, title="Terminal report")
    monkeypatch.setenv("DJANGO_ALLOW_ASYNC_UNSAFE", "true")
    cookie = cookie_for(director)
    problems = []
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        for width in WIDTHS:
            WorkAssignment.objects.all().delete()
            phone = width < 640
            context = browser.new_context(viewport={"width": width, "height": 844 if phone else 768}, is_mobile=phone,
                                          has_touch=phone)
            context.add_cookies([{"name": django_settings.SESSION_COOKIE_NAME, "value": cookie, "url": live_server.url}])
            page = context.new_page()
            for kind in WorkAssignment.Kind.values:
                page.goto(live_server.url + reverse("home"))
                page.locator("[data-assign-any-trigger]").click()
                page.locator("#assign-any-kind").wait_for()
                box = page.evaluate(GENERAL_DIALOG)
                if (not box["open"] or box["left"] < 0 or box["right"] > box["vw"] + 1 or box["top"] < 0
                        or box["bottom"] > box["vh"] + 1 or box["outside"] or box["sideways"]):
                    problems.append(f"{width}px {kind}: the dialog does not fit ({box})")
                page.select_option("#assign-any-kind", kind)
                if kind == "report":
                    page.fill("#assign-any-report-title", "Accomplishment Report - Q4 2026")
                page.select_option("#assign-any-member", str(head.pk))
                page.locator("[data-assign-any-submit]").click()
                page.wait_for_load_state()
                if not WorkAssignment.objects.filter(kind=kind, assigned_to=head).exists():
                    problems.append(f"{width}px {kind}: not assigned ({page.locator('[role=status], .toast').all_inner_texts()})")
            WorkAssignment.objects.all().delete()
            page.goto(live_server.url + reverse("approvals:inbox"))
            buttons = page.locator("[data-inbox-assign]")
            if buttons.count() != 3:
                problems.append(f"{width}px: each inbox item should have Assign ({buttons.count()})")
            page.locator("[data-inbox-assign='submission_review']").first.click()
            page.locator("#assign-any-kind").wait_for()
            if page.locator("#assign-any-kind").input_value() != "submission_review":
                problems.append(f"{width}px: an inbox item's Assign should choose its work")
            context.close()
        browser.close()
    assert not problems, "\n".join(problems)
