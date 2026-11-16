"""In-page preview (ASSUMPTIONS 329): the inline file view (same login rule as Download, inline, nosniff, the only view
that may be framed and only by this site), the preview panel (PDF in an iframe, images as <img>, other types: Download)
and the Preview button that opens the shared dialog."""

import pytest
from django.urls import reverse

from documents import pages
from documents.models import Document

from .conftest import JPG_BYTES, PDF_BYTES, PNG_BYTES, make_file, office_bytes

pytestmark = pytest.mark.django_db

HTMX = {"HTTP_HX_REQUEST": "true"}


@pytest.fixture
def pdf(upload, director, scheduled) -> Document:
    return upload(director, scheduled)


def view_url(document: Document) -> str:
    return reverse("documents:view", args=[document.pk])


def preview_url(document: Document) -> str:
    return reverse("documents:preview", args=[document.pk])


# --- Who may see it ------------------------------------------------------------------------------------------------


def test_preview_without_login_is_refused(client, pdf) -> None:
    for url in (view_url(pdf), preview_url(pdf)):
        response = client.get(url)
        assert response.status_code == 302 and "/accounts/login/" in response.url
    assert client.get(preview_url(pdf), **HTMX).status_code == 401  # HTMX: 401 + HX-Redirect


def test_preview_with_a_deactivated_account_is_refused(client, pdf, view_only) -> None:
    client.force_login(view_only)
    view_only.is_active = False
    view_only.save()
    for url in (view_url(pdf), preview_url(pdf)):
        response = client.get(url)
        assert response.status_code == 302 and "/accounts/login/" in response.url


def test_certificate_evidence_has_no_preview(client, pdf, director, scheduled, certificate_for) -> None:
    Document.objects.filter(pk=pdf.pk).update(related_certificate=certificate_for(scheduled))
    client.force_login(director)
    assert client.get(view_url(pdf)).status_code == 404
    assert client.get(preview_url(pdf)).status_code == 404
    pdf.refresh_from_db()
    assert pages.document_row(director, pdf)["preview_url"] is None


# --- The inline file view ------------------------------------------------------------------------------------------


@pytest.mark.parametrize("name, content, content_type", [
    ("Invitation.pdf", PDF_BYTES, "application/pdf"),
    ("Photo.png", PNG_BYTES, "image/png"),
    ("Photo.jpeg", JPG_BYTES, "image/jpeg"),
])
def test_any_active_staff_member_sees_the_file_inline(client, upload, director, view_only, scheduled,
                                                      name, content, content_type) -> None:
    document = upload(director, scheduled, file=make_file(name, content))
    client.force_login(view_only)  # not the uploader, no can_create_activity: the same rule as Download
    response = client.get(view_url(document))
    assert response.status_code == 200
    assert response["Content-Type"] == content_type
    assert response["Content-Disposition"].startswith("inline;") and name in response["Content-Disposition"]
    assert response["X-Content-Type-Options"] == "nosniff"
    assert b"".join(response.streaming_content) == content


def test_only_the_inline_view_may_be_framed_and_only_by_this_site(client, pdf, director) -> None:
    client.force_login(director)
    assert client.get(view_url(pdf))["X-Frame-Options"] == "SAMEORIGIN"
    for url in (reverse("documents:download", args=[pdf.pk]), preview_url(pdf), reverse("documents:detail", args=[pdf.pk]),
                reverse("documents:list"), reverse("activities:detail", args=[pdf.activity_id])):
        assert client.get(url)["X-Frame-Options"] == "DENY", url


def test_missing_file_gives_404(client, pdf, director) -> None:
    pdf.file_path.delete(save=False)
    client.force_login(director)
    assert client.get(view_url(pdf)).status_code == 404


def test_word_and_excel_files_are_never_shown_inline(client, upload, director, scheduled) -> None:
    client.force_login(director)
    for kind in ("docx", "xlsx"):
        document = upload(director, scheduled, file=make_file(f"Design.{kind}", office_bytes(kind)))
        assert client.get(view_url(document)).status_code == 404


def test_the_type_comes_from_the_confirmed_content_not_the_name(client, pdf, director) -> None:
    """preview_kind follows file_extension (confirmed by validate_upload), and the stored bytes are checked again."""
    Document.objects.filter(pk=pdf.pk).update(file_name="Looks-like-a.png")
    pdf.refresh_from_db()
    assert pages.preview_kind(pdf) == "pdf"
    client.force_login(director)
    with pdf.file_path.storage.open(pdf.file_path.name, "wb") as stored:  # the stored bytes are no PDF any more
        stored.write(b"<html><script>alert(1)</script></html>")
    assert client.get(view_url(pdf)).status_code == 404


# --- The preview panel and the dialog ------------------------------------------------------------------------------


def test_pdf_panel_shows_an_iframe_with_title_type_state_and_download(client, pdf, director) -> None:
    client.force_login(director)
    html = client.get(preview_url(pdf), **HTMX).content.decode()
    assert f'<iframe src="{view_url(pdf)}" title="Preview of {pdf.title}"' in html
    assert 'id="document-preview-title"' in html and pdf.title in html and "Request Letter" in html
    assert "Waiting for review" in html and reverse("documents:download", args=[pdf.pk]) in html
    assert "this.closest('dialog').close()" in html and "autofocus" in html  # Close, and the focus moves into the dialog
    assert "<html" not in html  # a partial for the dialog


def test_image_panel_shows_an_img(client, upload, director, scheduled) -> None:
    document = upload(director, scheduled, file=make_file("Photo.png", PNG_BYTES))
    client.force_login(director)
    html = client.get(preview_url(document), **HTMX).content.decode()
    assert f'<img src="{view_url(document)}" alt="Preview of {document.title}"' in html and "<iframe" not in html


def test_other_types_show_the_fallback_with_download(client, upload, director, scheduled) -> None:
    document = upload(director, scheduled, file=make_file("Design.docx", office_bytes("docx")))
    client.force_login(director)
    html = client.get(preview_url(document), **HTMX).content.decode()
    assert "Preview isn't available for this file type (DOCX)." in html and "data-preview-fallback" in html
    assert "<iframe" not in html and "<img" not in html
    assert html.count(reverse("documents:download", args=[document.pk])) == 2  # header and fallback


def test_without_javascript_the_panel_is_its_own_page(client, pdf, director) -> None:
    client.force_login(director)
    html = client.get(preview_url(pdf)).content.decode()
    assert "<html" in html and "<iframe" in html and reverse("documents:detail", args=[pdf.pk]) in html


def test_lists_have_a_preview_button_that_opens_the_shared_dialog(client, pdf, director) -> None:
    client.force_login(director)
    for url in (reverse("activities:detail", args=[pdf.activity_id]) + "?tab=documents", reverse("documents:list"),
                reverse("documents:detail", args=[pdf.pk])):
        html = client.get(url).content.decode()
        assert f'hx-get="{preview_url(pdf)}" hx-target="#document-preview-body"' in html, url
        assert 'aria-haspopup="dialog"' in html
        assert '<dialog id="document-preview" class="modal max-w-5xl" aria-labelledby="document-preview-title"' in html
        assert "window.documentPreviewOpener.focus()" in html  # the focus goes back to the Preview button
