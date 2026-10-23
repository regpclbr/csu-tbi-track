"""The one test with REAL WeasyPrint: the renderer may load only data: URIs, so text on a certificate
or a report can never make it read a local file or call a web address. Skipped automatically when
WeasyPrint's system libraries (Pango/GObject) are not installed (see CLAUDE.md, check_pdf)."""

import base64
import io
import logging

import pytest

from certificates.pdf import WeasyPrintRenderer
from conftest import image_bytes

pytestmark = pytest.mark.real_weasyprint


@pytest.fixture
def weasyprint():
    try:
        import weasyprint as module

        module.HTML(string="<p>check</p>").write_pdf()
    except (ImportError, OSError) as error:
        pytest.skip(f"WeasyPrint's system libraries are not available: {error}")
    return module


def image_count(pdf: bytes) -> int:
    """How many images the PDF contains (pypdf, already used by the assistant)."""
    from pypdf import PdfReader

    return sum(len(page.images) for page in PdfReader(io.BytesIO(pdf)).pages)


def page(src: str) -> str:
    return f'<html><body><p>Certificate</p><img src="{src}" style="width:4cm"></body></html>'


def test_data_uri_images_are_embedded(weasyprint) -> None:
    data_uri = "data:image/png;base64," + base64.b64encode(image_bytes(40, 30)).decode("ascii")
    pdf = WeasyPrintRenderer().render(page(data_uri))
    assert pdf.startswith(b"%PDF-") and image_count(pdf) == 1


@pytest.mark.parametrize("kind", ["file", "http"])
def test_other_urls_are_refused(weasyprint, tmp_path, caplog, kind) -> None:
    secret = tmp_path / "secret.png"
    secret.write_bytes(image_bytes(40, 30))
    # A real local file (would load without the restriction) or a web address (never contacted).
    src = secret.as_uri() if kind == "file" else "http://127.0.0.1:9/tracker.png"
    with caplog.at_level(logging.WARNING, logger="weasyprint"):
        pdf = WeasyPrintRenderer().render(page(src))
    assert pdf.startswith(b"%PDF-")  # the PDF is still made...
    assert image_count(pdf) == 0  # ...but nothing was loaded from the URL
    assert src in caplog.text  # WeasyPrint reports the refused URL


def test_without_the_restriction_the_local_file_would_load(weasyprint, tmp_path) -> None:
    """Control: proves the refused test above is meaningful (plain WeasyPrint does read the file)."""
    secret = tmp_path / "secret.png"
    secret.write_bytes(image_bytes(40, 30))
    pdf = weasyprint.HTML(string=page(secret.as_uri())).write_pdf()
    assert image_count(pdf) == 1
