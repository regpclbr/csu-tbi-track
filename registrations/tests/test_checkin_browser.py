"""Drawing a signature and the QR check-in page in a real browser (ASSUMPTIONS 434; Playwright + Chromium). Opt-in:
    $env:BROWSER_TESTS = "1"; pytest -n 0 -s registrations/tests/test_checkin_browser.py; Remove-Item Env:BROWSER_TESTS
1. The public registration page: "Draw it on the screen", a stroke drawn with the pointer fills the hidden PNG, the
   registration is saved with a drawn signature (a real PNG with ink).
2. The check-in page on a phone-sized screen (390 x 844, touch): no sideways scrolling, the email step, the signature
   step (drawn), then "You're checked in"; a second scan says "Already checked in today at …".
Screenshots go to pytest's tmp_path.
"""

import os
from datetime import datetime, time

import pytest
from django.urls import reverse

from registrations import signatures
from registrations.models import ParticipantSignature, Registration

from .conftest import FIXED

pytestmark = [
    pytest.mark.browser,
    pytest.mark.skipif(os.environ.get("BROWSER_TESTS") != "1", reason="browser test: set BROWSER_TESTS=1"),
]

PHONE = {"viewport": {"width": 390, "height": 844}, "has_touch": True, "is_mobile": True, "device_scale_factor": 2}


def draw(page, selector="canvas") -> None:
    """A signature-like stroke across the canvas with the pointer."""
    box = page.locator(selector).first.bounding_box()
    x, y = box["x"] + 20, box["y"] + box["height"] / 2
    page.mouse.move(x, y)
    page.mouse.down()
    for step in range(1, 25):
        page.mouse.move(x + step * 10, y + (12 if step % 2 else -12), steps=2)
    page.mouse.up()


def sideways_scroll(page) -> int:
    return page.evaluate("() => document.documentElement.scrollWidth - document.documentElement.clientWidth")


@pytest.mark.django_db(transaction=True)
def test_drawing_a_signature_on_the_registration_page(live_server, tmp_path, monkeypatch, scheduled) -> None:
    from playwright.sync_api import sync_playwright

    monkeypatch.setenv("DJANGO_ALLOW_ASYNC_UNSAFE", "true")
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        page = browser.new_context(**PHONE).new_page()
        page.goto(live_server.url + reverse("register", args=[scheduled.pk]))
        page.fill("#id_name", "Ana Cruz")
        page.fill("#id_email", "ana@example.com")
        page.fill("#id_designation", FIXED["designation"])
        page.fill("#id_office_agency", FIXED["office_agency"])
        for field in page.locator("[name^=field_]").all():  # the test-only registration field, if any
            field.fill("CSU")
        page.check("input[name=signature_kind][value=draw]")
        page.wait_for_selector("[data-signature-draw] canvas", state="visible")
        draw(page, "[data-signature-draw] canvas")
        assert page.input_value("input[name=signature_drawn]").startswith("data:image/png;base64,")
        page.check("#id_privacy_consent")
        assert sideways_scroll(page) <= 0
        page.screenshot(path=str(tmp_path / "register-drawn.png"), full_page=True)
        page.click("button[type=submit]")
        page.wait_for_load_state()
        errors = [text for text in page.locator(".field-error, [role=alert]").all_inner_texts() if text.strip()]
        assert page.url.endswith("/registered/"), errors
        browser.close()
    signature = ParticipantSignature.objects.get(registration__email="ana@example.com")
    content = signatures.image_bytes(signature)
    assert signature.kind == "draw" and content.startswith(signatures.PNG_SIGNATURE)


@pytest.mark.django_db(transaction=True)
def test_check_in_page_on_a_phone(live_server, tmp_path, monkeypatch, scheduled, signup, clock) -> None:
    from playwright.sync_api import sync_playwright

    signup(scheduled, "ana@example.com")  # registered earlier, no signature yet
    clock(datetime.combine(scheduled.start_date, time(7, 30)))  # day 1, 30 minutes before the start: open
    monkeypatch.setenv("DJANGO_ALLOW_ASYNC_UNSAFE", "true")
    url = live_server.url + reverse("checkin", args=[scheduled.pk])
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        page = browser.new_context(**PHONE).new_page()
        page.goto(url)
        assert sideways_scroll(page) <= 0
        page.screenshot(path=str(tmp_path / "checkin-email.png"), full_page=True)
        page.fill("#id_email", "ana@example.com")
        page.click("button[type=submit]")
        page.wait_for_selector("[data-checkin-step=signature]")
        page.check("input[name=signature_kind][value=draw]")
        page.wait_for_selector("[data-signature-draw] canvas", state="visible")
        draw(page, "[data-signature-draw] canvas")
        page.check("#id_privacy_consent")
        assert sideways_scroll(page) <= 0
        page.screenshot(path=str(tmp_path / "checkin-signature.png"), full_page=True)
        page.click("button[type=submit]")
        page.wait_for_selector("[data-checkin-done]")
        assert "You're checked in for" in page.inner_text("[data-checkin-done]")
        assert sideways_scroll(page) <= 0
        page.screenshot(path=str(tmp_path / "checkin-done.png"), full_page=True)
        page.goto(url)
        page.fill("#id_email", "ana@example.com")
        page.click("button[type=submit]")
        page.wait_for_selector("[data-checkin-done]")
        assert "Already checked in today at" in page.inner_text("[data-checkin-done]")
        browser.close()
    person = Registration.objects.get(email="ana@example.com")
    assert person.attendance_confirmed and person.signature.kind == "draw"
    print(f"\nScreenshots: {tmp_path}")
