"""Report PDF tables fit the page (ASSUMPTIONS 385), checked on the HTML given to the fake renderer: landscape A4,
fixed column widths that add up to at most the page content width, Start + End printed as one "Date" column, the time
on one compact line. The Excel file keeps every column. The real layout is checked by the opt-in
test_report_pdf_layout.py (real WeasyPrint)."""

import re
from datetime import date, time

import pytest

from activities.formatting import TIME_NOT_SET, compact_time_range, format_date_span, format_time_range
from reports import pdf
from reports.content import DATE, INT, PERCENT, TEXT, Column, Table
from .conftest import TEST_NOW

pytestmark = pytest.mark.django_db


@pytest.mark.parametrize(
    "start, end, expected",
    [
        (date(2026, 3, 5), date(2026, 3, 5), "Mar 5, 2026"),
        (date(2026, 3, 2), date(2026, 3, 3), "Mar 2–3, 2026"),
        (date(2026, 3, 30), date(2026, 4, 2), "Mar 30 – Apr 2, 2026"),
        (date(2026, 12, 30), date(2027, 1, 2), "Dec 30, 2026 – Jan 2, 2027"),
    ],
)
def test_date_span(start, end, expected) -> None:
    assert format_date_span(start, end) == expected


def test_compact_time_range() -> None:
    assert compact_time_range(format_time_range(time(8), time(17))) == "8:00 AM–5:00 PM"
    assert compact_time_range(format_time_range(None, None)) == TIME_NOT_SET


def activities_table() -> Table:
    columns = [
        Column("Title", key="title"), Column("Type"), Column("Categories"), Column("Start", DATE, key="start"),
        Column("End", DATE, key="end"), Column("Time", key="time"), Column("Venue", key="venue"),
        Column("Registered", INT), Column("Attended", INT), Column("Attendance rate", PERCENT),
        Column("Evaluations", INT), Column("Average rating", "decimal"),
    ]
    row = ["Pitching Workshop", "Training", "Faculty", date(2026, 3, 2), date(2026, 3, 3),
           format_time_range(time(8), time(17)), "CSU Hall", 10, 8, 0.8, 6, 4.25]
    return Table("Activities", columns, [row])


def test_start_and_end_are_one_date_column_and_the_time_is_compact() -> None:
    table = pdf.pdf_table(activities_table())
    labels = [label for label, _, _ in table["headers"]]
    assert labels == ["Title", "Type", "Categories", "Date", "Time", "Venue", "Registered", "Attended",
                      "Attendance rate", "Evaluations", "Average rating"]
    cells = [text for text, _ in table["rows"][0]]
    assert cells[3] == "Mar 2–3, 2026" and cells[4] == "8:00 AM–5:00 PM"
    assert cells[6:] == ["10", "8", "80.0%", "6", "4.25"]
    assert [css for _, css in table["rows"][0]][6:] == ["num"] * 5  # numbers right-aligned
    assert table["wide"] is True


@pytest.mark.parametrize("extra_types", [0, 5, 20])
def test_column_widths_never_exceed_the_page(extra_types) -> None:
    """However many columns (document types are master data), the table is at most the page content width."""
    table = activities_table()
    table.columns += [Column(f"Document type {n}") for n in range(extra_types)]
    table.rows[0] += ["Approved"] * extra_types
    printed = pdf.pdf_columns(table.columns)
    assert sum(column.width_mm for column in printed) <= pdf.PAGE_CONTENT_WIDTH_MM + 0.01
    widths = {column.label: column.width_mm for column in printed}
    if not extra_types:
        assert widths["Title"] > widths["Venue"] > widths["Type"]  # the long texts get the widest columns
        assert widths["Time"] == pdf.TIME_WIDTH_MM


def test_short_tables_stay_narrow() -> None:
    table = pdf.pdf_table(Table("Key figures", [Column("Measure", TEXT), Column("Value", INT)], [["Registered", 5]]))
    assert float(table["width_mm"]) == pdf.TEXT_MAX_WIDTH_MM + pdf.NUMBER_WIDTH_MM
    assert sum(float(width) for _, _, width in table["headers"]) == pytest.approx(100, abs=0.05)


def test_report_html_is_landscape_with_fixed_tables(director, make_activity, fake_pdf_renderer, monkeypatch) -> None:
    from reports import services

    monkeypatch.setattr(services, "queue_build", lambda report_id: None)
    make_activity("Very long title " * 12, start=date(2026, 3, 2), end=date(2026, 3, 3))
    report = services.request_report(director, {
        "report_type": "accomplishment", "format": "pdf", "date_range_start": "2026-01-01",
        "date_range_end": "2026-12-31", "included_sections": ["activities", "documents"],
    }, now=TEST_NOW)
    services.build_report(report.pk, now=TEST_NOW)
    html = fake_pdf_renderer.last
    assert "size: A4 landscape" in html and "table-layout: fixed" in html
    assert "display: table-header-group" in html and "break-inside: avoid" in html
    for table in re.findall(r"<table class=\"data[^\"]*\" style=\"width: ([\d.]+)mm\">", html):
        assert float(table) <= pdf.PAGE_CONTENT_WIDTH_MM + 0.01
    assert html.count("<colgroup>") == 3  # activities, documents overview, documents per activity
    assert ">Date</th>" in html and ">Start</th>" not in html
    assert "Mar 2–3, 2026" in html
