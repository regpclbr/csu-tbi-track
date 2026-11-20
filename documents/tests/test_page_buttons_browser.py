"""Buttons on every main page in a real browser (UI revision after the UAT, ASSUMPTIONS 411; Playwright + Chromium). Opt-in:
    $env:BROWSER_TESTS = "1"; pytest -n 0 -s documents/tests/test_page_buttons_browser.py; Remove-Item Env:BROWSER_TESTS
(It lives here because it reuses the documents fixtures: members, activities and uploads.)
At 1366, 1280, 1024 and 390 px, on the Dashboard, Activities list, the activity page (every tab), New activity, Documents
(both lists), Certificates, Reports, AI Assistant, the Admin Console screens, sign-in and the public pages:
  - no two visible buttons overlap (buttons = .btn, <button>, and the tab / segmented-control links);
  - no button sticks out of the window;
  - from 1280 px no button is hidden in a sideways-scrolling box (a table, the tab strip) (the Admin Console Members row used to hide
    "Deactivate" past the table's right edge at 1366 px);
  - the page itself never scrolls sideways.
Screenshots of each page go to pytest's tmp_path (the -s output prints the folder).
"""

import os

import pytest
from django.conf import settings as django_settings
from django.test import Client
from django.urls import reverse

from documents.models import DocumentType

pytestmark = [
    pytest.mark.browser,
    pytest.mark.skipif(os.environ.get("BROWSER_TESTS") != "1", reason="browser test: set BROWSER_TESTS=1"),
]

WIDTHS = [1366, 1280, 1024, 390]

MEASURE = r"""(strictScroll) => {
  const doc = document.documentElement;
  const visible = el => {
    const r = el.getBoundingClientRect();
    if (r.width < 1 || r.height < 1) return false;
    for (let n = el; n; n = n.parentElement) {
      const s = getComputedStyle(n);
      if (s.display === 'none' || s.visibility === 'hidden') return false;
      if (n.tagName === 'DIALOG' && !n.open) return false;
    }
    if (el.closest('.sr-only, [aria-hidden="true"]')) return false;
    const aside = el.closest('aside');  // the phone menu is off-screen until opened
    return !(aside && aside.getBoundingClientRect().right <= 0);
  };
  // A sideways-scrolling box (tables, the tab strip on phones): computed overflow, not only the class name.
  const scrollerOf = el => {
    for (let n = el.parentElement; n && n !== document.body; n = n.parentElement) {
      const o = getComputedStyle(n).overflowX;
      if (o === 'auto' || o === 'scroll') return n;
    }
    return null;
  };
  const label = el => (el.getAttribute('aria-label') || el.textContent || el.title || el.tagName).trim().replace(/\s+/g, ' ').slice(0, 30);
  const buttons = [...document.querySelectorAll('.btn, button, a.tab, .segmented-item')].filter(visible)
    .filter(el => !el.closest('.hp-field'));
  const problems = [];
  for (let i = 0; i < buttons.length; i++) {
    const a = buttons[i], ra = a.getBoundingClientRect();
    const scroller = scrollerOf(a);
    if ((ra.left < -1 || ra.right > doc.clientWidth + 1) && !scroller) problems.push(`"${label(a)}" sticks out of the window`);
    if (strictScroll && scroller && scroller.scrollWidth > scroller.clientWidth + 1) {
      const rs = scroller.getBoundingClientRect();
      if (ra.right > rs.right + 1) problems.push(`"${label(a)}" is hidden past the right edge of a scrolling table`);
    }
    for (let j = i + 1; j < buttons.length; j++) {
      const b = buttons[j];
      if (a.contains(b) || b.contains(a)) continue;
      const rb = b.getBoundingClientRect();
      const x = Math.min(ra.right, rb.right) - Math.max(ra.left, rb.left);
      const y = Math.min(ra.bottom, rb.bottom) - Math.max(ra.top, rb.top);
      if (x > 1 && y > 1) problems.push(`"${label(a)}" overlaps "${label(b)}"`);
    }
  }
  return {count: buttons.length, problems, pageScroll: doc.scrollWidth - doc.clientWidth};
}"""


def cookie_for(member) -> str:
    client = Client()
    client.force_login(member)
    return client.cookies[django_settings.SESSION_COOKIE_NAME].value


@pytest.mark.django_db(transaction=True)
def test_no_button_overlaps_or_sticks_out(live_server, tmp_path, monkeypatch, director, head, system_admin, make_activity, upload) -> None:
    from playwright.sync_api import sync_playwright

    # An Ongoing activity with uploads waiting for the Director (Approve / Return), and a Scheduled one.
    activity = make_activity(status="Ongoing", title="Regional Technology Business Incubation Summit and Investor Day")
    upload(head, activity, document_type=DocumentType.objects.get(name="Activity Design"), title="Activity design of the summit")
    upload(head, activity, title="Request letter for the use of the function hall")
    make_activity(title="Pitching Workshop")
    monkeypatch.setenv("DJANGO_ALLOW_ASYNC_UNSAFE", "true")  # the live server thread uses the ORM while Playwright runs

    detail = reverse("activities:detail", args=[activity.pk])
    staff_pages = [reverse("home"), reverse("activities:list"), reverse("activities:create"), reverse("documents:list"),
                   reverse("documents:review"), reverse("certificates:list"), reverse("reports:list"), reverse("assistant:chat")]
    staff_pages += [f"{detail}?tab={tab}" for tab in ("overview", "history", "forms", "registrations", "evaluation", "documents", "certificates")]
    admin_pages = [reverse(name) for name in ("accounts:console", "accounts:console_roles", "accounts:console_positions",
                                               "accounts:console_certificates", "accounts:console_system")]
    public_pages = [reverse("accounts:login"), reverse("register_list"), reverse("evaluation_request", args=[activity.pk])]
    visits = [(cookie_for(director), staff_pages), (cookie_for(system_admin), admin_pages), (None, public_pages)]

    problems = []
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        for width in WIDTHS:
            for cookie, urls in visits:
                context = browser.new_context(viewport={"width": width, "height": 900}, reduced_motion="reduce")
                if cookie:
                    context.add_cookies([{"name": django_settings.SESSION_COOKIE_NAME, "value": cookie, "url": live_server.url}])
                page = context.new_page()
                for url in urls:
                    page.goto(live_server.url + url)
                    page.wait_for_load_state("networkidle")
                    box = page.evaluate(MEASURE, width >= 1280)
                    print(f"{width:5}px {url:45} buttons {box['count']:3}  problems {box['problems']}  page scroll {box['pageScroll']}")
                    problems += [f"{url} at {width}px: {item}" for item in box["problems"]]
                    if box["pageScroll"] > 0:
                        problems.append(f"{url} at {width}px scrolls sideways by {box['pageScroll']}px")
                    if box["count"] == 0 and url != reverse("register_list"):  # nothing open: no buttons
                        problems.append(f"{url} at {width}px: no buttons found (did the page load?)")
                    name = url.strip("/").replace("/", "-").replace("?tab=", "-") or "dashboard"
                    page.screenshot(path=tmp_path / f"{name}-{width}.png")
                context.close()
        browser.close()
    print("screenshots:", tmp_path)
    assert not problems, "\n".join(problems)
