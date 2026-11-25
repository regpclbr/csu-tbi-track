"""Uploading photos with the evaluation on a phone (ASSUMPTIONS 435; Playwright + Chromium). Opt-in:
    $env:BROWSER_TESTS = "1"; pytest -n 0 -s registrations/tests/test_evaluation_photos_browser.py; Remove-Item Env:BROWSER_TESTS
On a 390 x 844 touch screen: the evaluation form, the review step with "Upload your photos from the event", two files
chosen (a JPEG with a GPS location and a PDF), the privacy checkbox, "Confirm and submit" -> "Thank you!". No sideways
scrolling on either step; both files saved with the submission and the photo's location removed. Screenshots go to
pytest's tmp_path.
"""

import os
from io import BytesIO

import pytest
from django.urls import reverse
from PIL import Image

from registrations import evaluation_photos, services
from registrations.models import EvaluationAttachment, EvaluationSubmission

from .test_evaluation_photos import GPS_IFD, PDF, jpeg
from .test_public_pages import open_window

pytestmark = [
    pytest.mark.browser,
    pytest.mark.skipif(os.environ.get("BROWSER_TESTS") != "1", reason="browser test: set BROWSER_TESTS=1"),
]

PHONE = {"viewport": {"width": 390, "height": 844}, "has_touch": True, "is_mobile": True, "device_scale_factor": 2}


def sideways_scroll(page) -> int:
    return page.evaluate("() => document.documentElement.scrollWidth - document.documentElement.clientWidth")


@pytest.mark.django_db(transaction=True)
def test_uploading_photos_with_the_evaluation_on_a_phone(live_server, tmp_path, monkeypatch, attended, scheduled) -> None:
    from playwright.sync_api import sync_playwright

    person = attended(1)[0]
    open_window(scheduled)
    photo_path, pdf_path = tmp_path / "IMG_0042.jpg", tmp_path / "My output.pdf"
    photo_path.write_bytes(jpeg(1200, 900, gps=True))
    pdf_path.write_bytes(PDF)

    monkeypatch.setenv("DJANGO_ALLOW_ASYNC_UNSAFE", "true")
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        page = browser.new_context(**PHONE).new_page()
        page.goto(live_server.url + reverse("evaluate", args=[services.evaluation_token(person)]))
        for radio_group in sorted({name for name in page.eval_on_selector_all(
                "input[type=radio][name^=field_]", "els => els.map(e => e.name)")}):
            page.check(f"input[name='{radio_group}'][value='5']", force=True)  # the radios are sr-only pills
        for box in page.locator("textarea[name^=field_]").all():
            box.fill("How to pitch.")
        assert sideways_scroll(page) <= 0
        page.click("button[type=submit]")
        page.wait_for_selector("#id_photos")
        assert page.is_visible("text=Upload your photos from the event")
        page.set_input_files("#id_photos", [str(photo_path), str(pdf_path)])
        page.check("#id_privacy_consent")
        assert sideways_scroll(page) <= 0
        box = page.locator("#id_photos").bounding_box()
        assert box["x"] >= 0 and box["x"] + box["width"] <= 390 and box["height"] >= 44  # fits, big enough to tap
        page.screenshot(path=str(tmp_path / "evaluate-review-photos.png"), full_page=True)
        page.click("button[name=step][value=confirm]")
        page.wait_for_load_state()
        errors = [text for text in page.locator(".field-error").all_inner_texts() if text.strip()]
        assert page.url.endswith("/thanks/"), errors
        assert page.is_visible("text=Thank you!") and sideways_scroll(page) <= 0
        page.screenshot(path=str(tmp_path / "evaluate-done.png"), full_page=True)
        browser.close()

    submission = EvaluationSubmission.objects.get(registration=person)
    photo, pdf = EvaluationAttachment.objects.filter(submission=submission).order_by("pk")
    assert (photo.file_name, pdf.file_name) == ("IMG_0042.jpg", "My output.pdf")
    image = Image.open(BytesIO(evaluation_photos.file_bytes(photo)))
    assert not image.getexif().get_ifd(GPS_IFD) and image.size == (1200, 900)
    assert evaluation_photos.file_bytes(pdf) == PDF


@pytest.mark.django_db(transaction=True)
def test_staff_tick_use_in_reports_and_open_the_larger_view(live_server, tmp_path, monkeypatch, attended, scheduled,
                                                            director) -> None:
    """The Evaluation tab at 1280 px: the thumbnail grid, ticking "Use in reports" (HTMX, saved with who), the larger
    view in the dialog; no sideways scrolling."""
    from django.conf import settings as django_settings
    from django.core.files.uploadedfile import SimpleUploadedFile
    from django.test import Client
    from playwright.sync_api import sync_playwright

    person = attended(1)[0]
    open_window(scheduled)
    services.submit_evaluation(services.evaluation_token(person), certificate_name="Ana Cruz",
                               certificate_email="ana@example.com", answers=_answers(scheduled),
                               attachments=[SimpleUploadedFile("IMG_0042.jpg", jpeg(1200, 900)),
                                            SimpleUploadedFile("My output.pdf", PDF)])
    photo = EvaluationAttachment.objects.order_by("pk").first()
    client = Client()
    client.force_login(director)
    cookie = client.cookies[django_settings.SESSION_COOKIE_NAME].value

    monkeypatch.setenv("DJANGO_ALLOW_ASYNC_UNSAFE", "true")
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        context = browser.new_context(viewport={"width": 1280, "height": 900})
        context.add_cookies([{"name": django_settings.SESSION_COOKIE_NAME, "value": cookie, "url": live_server.url}])
        page = context.new_page()
        page.goto(live_server.url + reverse("activities:detail", args=[scheduled.pk]) + "?tab=evaluation#photos")
        item = page.locator(f"#photo-{photo.pk}")
        item.scroll_into_view_if_needed()
        page.wait_for_function(  # the thumbnail loads (lazy: once it is on screen)
            f"() => {{ const img = document.querySelector('#photo-{photo.pk} img'); return img && img.complete && img.naturalWidth > 0; }}",
            timeout=10_000)
        assert sideways_scroll(page) <= 0
        with page.expect_response(lambda response: "use-in-reports" in response.url):
            item.locator("input[type=checkbox]").check()
        page.wait_for_selector(f"#photo-{photo.pk} >> text=Marked by Test Director")
        assert page.inner_text("#photos-summary") == "2 files uploaded · 1 used in reports"  # updated out of band
        page.locator("#photos").screenshot(path=str(tmp_path / "evaluation-tab-photos.png"))
        page.locator(f"#photo-{photo.pk} a[aria-haspopup=dialog]").click()
        page.wait_for_selector("#photo-dialog[open] img")
        assert page.locator("#photo-dialog a:has-text(\"Download\")").is_visible()
        assert page.locator("#photo-dialog").is_visible()
        page.screenshot(path=str(tmp_path / "evaluation-photo-dialog.png"))
        browser.close()

    photo.refresh_from_db()
    assert photo.use_in_reports is True and photo.marked_by == director


def _answers(activity):
    from .conftest import evaluation_answers

    return evaluation_answers(activity)
