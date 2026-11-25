"""Every modal is centred in the viewport (ASSUMPTIONS 343), checked in a real browser (Playwright + Chromium).

Opt-in, because it needs the Chromium download and takes about a minute:
    $env:BROWSER_TESTS = "1"; pytest -n 0 config/tests/test_modals_browser.py; Remove-Item Env:BROWSER_TESTS
It fills the TEST database with the demo data (seed_demo), signs in with force_login (no password), opens each modal
at 1366x768 and 390x844 and checks: centred (equal space left/right and top/bottom, 1 px tolerance), at least 16 px from
every edge, at most 85 % of the screen high, the backdrop dimmed, Escape closes it and the focus goes back to the
trigger. Screenshots: modal-<name>-<width>.png in pytest's tmp_path (the path is printed with -s).
Since ASSUMPTIONS 432 uploads are approved copies, so the demo data has nothing to Return: the test switches needs_review
on for the Invitation Letter type and uploads one as another member (`_waiting_document`), so the Return dialog is tested.
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

VIEWPORTS = [(1366, 768), (390, 844)]
PDF_BYTES = b"%PDF-1.4\n1 0 obj << >> endobj\ntrailer << >>\n%%EOF\n"


def _targets(director, admin) -> list[dict]:
    """(name, member, url, how to open it) for every modal, found in the demo data through the services."""
    from activities.models import Activity
    from certificates.models import Certificate
    from documents import services as document_services
    from documents.models import Document
    from registrations import services as registration_services

    def detail(activity, tab: str = "") -> str:
        return reverse("activities:detail", args=[activity.pk]) + (f"?tab={tab}" if tab else "")

    not_final = Activity.objects.exclude(status__name="Conducted").order_by("pk").first()
    with_registrations = Activity.objects.filter(registrations__isnull=False).distinct().order_by("pk")
    attendance = next(a for a in with_registrations if registration_services.attendance_closed_reason(a) is None)
    returnable = next(
        d for d in Document.objects.activity_documents().select_related("status").order_by("pk")
        if document_services.allowed_actions(director, d).get("return")
    )
    evidence_activity = Certificate.objects.order_by("pk").first().activity
    with_form = with_registrations.filter(form_templates__isnull=False).first()
    return [
        {"name": "stat-card", "member": director, "url": reverse("activities:list"), "click": ".stat-card-link >> nth=2"},
        {"name": "status-change", "member": director, "url": detail(not_final), "click": "button[aria-haspopup=dialog]:has-text('Change status')"},
        {"name": "return-document", "member": director, "url": detail(returnable.activity, "documents"),
         "click": f"a[href^='{reverse('documents:action_form', args=[returnable.pk, 'return'])}']"},
        # (No "mark-attended" case: "Mark attended" and "Mark selected as attended" are direct submit buttons, no dialog.)
        {"name": "registration-details", "member": director, "url": detail(attendance, "registrations"), "click": "button:has-text('View') >> nth=0"},
        {"name": "evidence", "member": director, "url": detail(evidence_activity, "certificates"), "click": "a:has-text('Evidence') >> nth=0"},
        {"name": "form-preview", "member": director, "url": detail(with_form, "forms"), "click": "button[aria-haspopup=dialog]:has-text('Preview') >> nth=0"},
        {"name": "reset-password", "member": admin, "url": reverse("accounts:console"),
         "click": "button[aria-haspopup=dialog]:has-text('Reset password') >> nth=0"},
        {"name": "deactivate-member", "member": admin, "url": reverse("accounts:console"), "click": "button[aria-haspopup=dialog]:has-text('Deactivate') >> nth=0"},
    ]


def _waiting_document(director):
    """One upload waiting for the Director's review (needs_review on, set in this test's data only, ASSUMPTIONS 432)."""
    from django.core.files.uploadedfile import SimpleUploadedFile

    from accounts.models import TBIMember
    from activities.models import Activity
    from documents import services as document_services
    from documents.models import DocumentType

    document_type = DocumentType.objects.get(name="Invitation Letter")
    document_type.needs_review = True
    document_type.save(update_fields=["needs_review"])
    uploader = TBIMember.objects.filter(is_active=True, role__can_create_activity=True).exclude(pk=director.pk).order_by("pk").first()
    activity = Activity.objects.exclude(status__name="Conducted").order_by("pk").first()
    pdf = SimpleUploadedFile("Invitation.pdf", PDF_BYTES, content_type="application/pdf")
    return document_services.upload_document(uploader, activity, document_type, pdf, title="Invitation to the partners")


MEASURE = """() => {
  const dialog = [...document.querySelectorAll('dialog')].find(d => d.open);
  const box = dialog.getBoundingClientRect();
  const backdrop = getComputedStyle(dialog, '::backdrop').backgroundColor;
  return {top: box.top, left: box.left, bottom: innerHeight - box.bottom, right: innerWidth - box.right,
          height: box.height, vh: innerHeight, backdrop, id: dialog.id,
          css: (s => [s.position, s.top, s.bottom, s.marginTop, s.marginBottom, s.height, s.maxHeight, s.transform, s.translate].join(' '))(getComputedStyle(dialog))};
}"""


@pytest.mark.django_db(transaction=True)
def test_every_modal_is_centred(live_server, settings, tmp_path, monkeypatch) -> None:
    from playwright.sync_api import sync_playwright

    from accounts.models import TBIMember

    settings.DEBUG = True  # seed_demo is demo-only
    call_command("seed_demo", verbosity=0)
    director = TBIMember.objects.get(email="director@demo.example.com")
    admin = TBIMember.objects.get(email="system-administrator@demo.example.com")
    _waiting_document(director)
    targets = _targets(director, admin)
    cookies = {}
    for member in (director, admin):
        client = Client()
        client.force_login(member)
        cookies[member.pk] = client.cookies[django_settings.SESSION_COOKIE_NAME].value
    monkeypatch.setenv("DJANGO_ALLOW_ASYNC_UNSAFE", "true")  # the live server thread uses the ORM while Playwright runs

    results = []
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        for width, height in VIEWPORTS:
            for target in targets:
                context = browser.new_context(viewport={"width": width, "height": height})
                context.add_cookies([{"name": django_settings.SESSION_COOKIE_NAME, "value": cookies[target["member"].pk],
                                      "url": live_server.url}])
                page = context.new_page()
                page.goto(live_server.url + target["url"])
                trigger = page.locator(target["click"])
                trigger.scroll_into_view_if_needed()
                trigger.click()
                page.wait_for_selector("dialog[open]")
                page.wait_for_timeout(300)  # HTMX content settled
                box = page.evaluate(MEASURE)
                page.screenshot(path=tmp_path / f"modal-{target['name']}-{width}.png")
                page.keyboard.press("Escape")
                page.wait_for_selector("dialog[open]", state="detached")
                focus_back = page.evaluate("el => document.activeElement === el", trigger.element_handle())
                results.append((target["name"], width, box, focus_back))
                context.close()
        browser.close()
    print(f"\nScreenshots: {tmp_path}")
    problems = []
    for name, width, box, focus_back in results:
        print(f"{name:22} {width:5}px  top {box['top']:6.1f}  bottom {box['bottom']:6.1f}  left {box['left']:6.1f}  "
              f"right {box['right']:6.1f}  height {box['height']:6.1f}/{box['vh']}  focus back: {focus_back}  {box['css']}")
        if abs(box["top"] - box["bottom"]) > 1 or abs(box["left"] - box["right"]) > 1:
            problems.append(f"{name} at {width}px is not centred: {box}")
        if min(box["top"], box["bottom"], box["left"], box["right"]) < 16:
            problems.append(f"{name} at {width}px is closer than 16 px to an edge: {box}")
        if box["height"] > 0.85 * box["vh"] + 1:
            problems.append(f"{name} at {width}px is higher than 85 % of the screen: {box}")
        if box["backdrop"] in ("rgba(0, 0, 0, 0)", "transparent"):
            problems.append(f"{name} at {width}px has no dimmed backdrop")
        if not focus_back:
            problems.append(f"{name} at {width}px: the focus did not go back to the trigger")
    assert not problems, "\n".join(problems)
