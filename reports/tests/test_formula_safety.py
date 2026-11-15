"""ASSUMPTIONS 373 (confirms items 207 and 211): a title like =HYPERLINK("https://example.com","Formula test") (old test
data, kept as it is) is plain text everywhere. Excel: every text value that starts with = + - @ tab or CR is written as
text with Excel's quote prefix (reports.excel.safe_cell), never as a formula; real numbers stay numbers. HTML and the
report PDF: the title is escaped by the templates and is never a link."""

import zipfile
from datetime import date
from io import BytesIO

import pytest
from django.test import Client
from django.urls import reverse
from openpyxl import Workbook, load_workbook

from reports import services
from reports.excel import FORMULA_STARTS, safe_cell

from .conftest import TEST_NOW

pytestmark = pytest.mark.django_db

DEMO_TITLE = '=HYPERLINK("https://example.com","Formula test")'  # the seed_demo title (item 211)
ESCAPED = "=HYPERLINK(&quot;https://example.com&quot;,&quot;Formula test&quot;)"
STARTS = ["=1+1", "+1+1", "-1+1", "@SUM(A1:A2)", "\t=1+1", "\r=1+1"]


def reloaded(values: list) -> tuple[list, list[str]]:
    """Write `values` in column A through safe_cell, save, reopen: (the cells, the raw worksheet XML)."""
    workbook = Workbook()
    sheet = workbook.active
    for row, value in enumerate(values, start=1):
        safe_cell(sheet.cell(row=row, column=1), value)
    buffer = BytesIO()
    workbook.save(buffer)
    with zipfile.ZipFile(BytesIO(buffer.getvalue())) as archive:
        xml = [archive.read(name).decode() for name in archive.namelist() if name.startswith("xl/worksheets/")]
    buffer.seek(0)
    cells = [load_workbook(buffer).active.cell(row=row, column=1) for row in range(1, len(values) + 1)]
    return cells, xml


def test_every_formula_start_is_covered() -> None:
    assert set(FORMULA_STARTS) == {"=", "+", "-", "@", "\t", "\r"}


@pytest.mark.parametrize("value", STARTS + [DEMO_TITLE])
def test_formula_like_text_stays_text_with_the_quote_prefix(value) -> None:
    (cell,), xml = reloaded([value])
    assert cell.value == value and cell.data_type == "s" and cell.quotePrefix is True
    assert all("<f>" not in sheet for sheet in xml)  # no formula in the file


def test_numbers_and_plain_text_are_unchanged() -> None:
    cells, _ = reloaded([-5, -2.5, 0, "Pitching Workshop", "5-day bootcamp"])
    assert [c.value for c in cells] == [-5, -2.5, 0, "Pitching Workshop", "5-day bootcamp"]
    assert [c.data_type for c in cells] == ["n", "n", "n", "s", "s"]
    assert not any(c.quotePrefix for c in cells)  # a negative NUMBER is a number, not a formula


def test_excel_report_with_a_minus_title(director, make_activity) -> None:
    """The existing report test covers = + @; this one covers "-" through a real report build."""
    make_activity("-2+3 Workshop", start=date(2026, 3, 10))
    report = services.request_report(director, {"report_type": "accomplishment", "format": "excel",
                                                "date_range_start": "2026-01-01", "date_range_end": "2026-12-31",
                                                "included_sections": ["activities"]}, now=TEST_NOW)
    services.build_report(report.pk, now=TEST_NOW)
    report.refresh_from_db()
    cell = load_workbook(report.file_path.path)["Activities"].cell(row=8, column=1)
    assert (cell.value, cell.data_type, cell.quotePrefix) == ("-2+3 Workshop", "s", True)
    with zipfile.ZipFile(report.file_path.path) as archive:
        assert all("<f>" not in archive.read(n).decode() for n in archive.namelist() if n.startswith("xl/worksheets/"))


def test_report_pdf_html_escapes_the_title(director, make_activity, fake_pdf_renderer) -> None:
    make_activity(DEMO_TITLE, start=date(2026, 3, 10))
    report = services.request_report(director, {"report_type": "accomplishment", "format": "pdf",
                                                "date_range_start": "2026-01-01", "date_range_end": "2026-12-31",
                                                "included_sections": ["activities"]}, now=TEST_NOW)
    services.build_report(report.pk, now=TEST_NOW)
    html = fake_pdf_renderer.last
    assert ESCAPED in html and 'href="https://example.com"' not in html


def test_pages_show_the_title_as_plain_text(director, make_activity) -> None:
    activity = make_activity(DEMO_TITLE, start=date(2026, 3, 10))
    client = Client()
    client.force_login(director)
    pages = [
        reverse("activities:detail", args=[activity.pk]),
        reverse("activities:detail", args=[activity.pk]) + "?tab=certificates",
        reverse("activities:detail", args=[activity.pk]) + "?tab=history",
        reverse("activities:list") + "?q=HYPERLINK",
        reverse("search") + "?q=HYPERLINK",
    ]
    for url in pages:
        response = client.get(url)
        html = response.content.decode()
        assert response.status_code == 200, url
        assert ESCAPED in html, url
        assert DEMO_TITLE not in html and 'href="https://example.com"' not in html, url
    home = client.get(reverse("home")).content.decode()  # the dashboard: wherever the title appears, escaped
    assert DEMO_TITLE not in home and 'href="https://example.com"' not in home
