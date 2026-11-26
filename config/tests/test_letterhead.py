"""The CSU letterhead on every office-style PDF (ASSUMPTIONS 469): config/letterhead.py + pdf/_letterhead*.html."""

import io

import pytest
from django.template.loader import render_to_string
from PIL import Image

from config import letterhead


@pytest.fixture(autouse=True)
def fresh_logo_cache():
    letterhead._logo_parts.cache_clear()
    letterhead._mark_uri.cache_clear()
    yield
    letterhead._logo_parts.cache_clear()
    letterhead._mark_uri.cache_clear()


def png(width: int, height: int, boxes: list[tuple[int, int, int, int]]) -> bytes:
    """A transparent PNG with black boxes (a stand-in logo)."""
    image = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    for box in boxes:
        image.paste((0, 0, 0, 255), box)
    out = io.BytesIO()
    image.save(out, "PNG")
    return out.getvalue()


def test_the_real_logo_is_cut_into_the_seal_and_the_wordmark() -> None:
    parts = letterhead.logo_parts()
    assert parts is not None and parts.seal.startswith("data:image/png;base64,")
    assert parts.wordmark.startswith("data:image/png;base64,")
    import base64

    wordmark = Image.open(io.BytesIO(base64.b64decode(parts.wordmark.split(",", 1)[1])))
    seal = Image.open(io.BytesIO(base64.b64decode(parts.seal.split(",", 1)[1])))
    assert wordmark.width / wordmark.height > 8  # one line of text ("Caraga State University"), the "C" included
    assert seal.height > seal.width  # the tall seal with its rays


def test_split_column_finds_the_gap_before_the_wordmark_band(tmp_path, settings) -> None:
    # A tall "seal" (rows 0-99) touching a short "C" by one empty column, then the rest of the wordmark (rows 40-60).
    settings.LETTERHEAD_DIR = tmp_path
    (tmp_path / letterhead.LOGO_FILE).write_bytes(png(300, 100, [(0, 0, 50, 100), (51, 40, 60, 60), (62, 40, 300, 60)]))
    from config.letterhead import _ink_mask

    mask, _flat, _rgba = _ink_mask(Image.open(tmp_path / letterhead.LOGO_FILE))
    assert letterhead.split_column(mask) == 50  # not 60: the "C" stays with the wordmark


def test_a_logo_without_a_gap_is_kept_whole(tmp_path, settings) -> None:
    settings.LETTERHEAD_DIR = tmp_path
    (tmp_path / letterhead.LOGO_FILE).write_bytes(png(200, 80, [(0, 0, 200, 80)]))
    parts = letterhead.logo_parts()
    assert parts.seal and parts.wordmark == ""


def test_without_the_logo_file_the_header_is_text(tmp_path, settings) -> None:
    settings.LETTERHEAD_DIR = tmp_path
    context = letterhead.context()
    assert context["seal"] == "" and context["wordmark"] == ""
    html = render_to_string("pdf/_letterhead.html", {"lh": context})
    assert "Caraga State University" in html and "Republic of the Philippines" in html
    assert "Ampayon, Butuan City 8600, Philippines" in html


def test_official_marks_appear_only_when_their_files_exist(tmp_path, settings) -> None:
    settings.LETTERHEAD_DIR = tmp_path
    assert letterhead.marks() == []
    (tmp_path / "bagong-pilipinas.png").write_bytes(png(40, 40, [(5, 5, 35, 35)]))
    found = letterhead.marks()
    assert [mark["alt"] for mark in found] == ["Bagong Pilipinas"]
    html = render_to_string("pdf/_letterhead.html", {"lh": letterhead.context()})
    assert 'alt="Bagong Pilipinas"' in html and "SOCOTEC" not in html and "PAB" not in html


def test_office_line_default_upper_case_and_the_motto(settings) -> None:
    context = letterhead.context()
    assert context["office_line"] == "NAVIGATU – TECHNOLOGY BUSINESS INCUBATOR"
    assert letterhead.context(office_line="Vice President for Research")["office_line"] == "VICE PRESIDENT FOR RESEARCH"
    html = render_to_string("pdf/_letterhead.html", {"lh": letterhead.context(label="ACTIVITY REPORT")})
    assert '<span class="lh-initial">C</span>ompetence' in html and "ACTIVITY REPORT" in html
    assert "EXTENTION" not in settings.LETTERHEAD_VP_OFFICE_LINE and "EXTENSION" in settings.LETTERHEAD_VP_OFFICE_LINE


def test_sample_line_setting_and_the_forced_sample(settings) -> None:
    styles = render_to_string("pdf/_letterhead_styles.html", {"lh": letterhead.context(form_code="F-COR-001")})
    assert '@bottom-center { content: "SAMPLE – FOR SYSTEM DEMONSTRATION"' in styles
    assert '@bottom-left { content: "F-COR-001"' in styles and 'counter(page) " of " counter(pages)' in styles
    settings.LETTERHEAD_SAMPLE_LINE = False
    assert letterhead.context()["sample"] == ""
    assert letterhead.context(sample=True)["sample"] == "SAMPLE – FOR SYSTEM DEMONSTRATION"  # the seed_defense files


def test_footer_strings_cannot_break_out_of_the_css() -> None:
    escaped = letterhead.css_string('a"b\\c</style><script>')
    assert '"' not in escaped.replace('\\"', "") and "</style>" not in escaped and "<" not in escaped
    styles = render_to_string("pdf/_letterhead_styles.html",
                              {"lh": letterhead.context(form_code='F"1</style>')})
    assert "</style>" not in styles


@pytest.mark.django_db
def test_every_office_pdf_uses_the_letterhead(member, activity) -> None:
    from reports.content import ReportContent  # noqa: F401 - imported for the type only
    from registrations import attendance_forms

    html = attendance_forms.form_html(activity, attendance_forms.CONFIRMATION, member)
    assert 'class="lh"' in html and "position: running(letterhead)" in html
    assert "F-PROC-COA-005" in html and "SAMPLE – FOR SYSTEM DEMONSTRATION" in html
