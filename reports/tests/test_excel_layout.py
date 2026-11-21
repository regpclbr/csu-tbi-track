"""The Excel layout of the reports (reports/excel.py, ASSUMPTIONS 338): title block, real Excel tables that open
without Excel's "repair" warning, number/date formats, totals from metrics.period_summary, print setup, Report info
and the empty cases. The numbers themselves are tested in test_metrics / test_reports."""

from datetime import UTC, date, datetime, timedelta
from io import BytesIO

import pytest
from openpyxl import Workbook, load_workbook
from openpyxl.utils import get_column_letter
from openpyxl.utils.cell import range_boundaries
from django.utils import timezone

from reports import excel, metrics, services
from reports.content import MONEY, NO_ACTIVITIES, REPORT_TYPES, Column, Table
from reports.metrics import ReportFilters

from .conftest import TEST_NOW
from .test_reports import queued, request  # noqa: F401  (fixture and helper)

pytestmark = pytest.mark.django_db

ACCOMPLISHMENT_SHEETS = ["Report info", "Summary", "Activities", "By type", "By category", "Category per activity",
                         "Per month", "Evaluation"]
PERIOD = ReportFilters(date(2026, 1, 1), date(2026, 12, 31))


@pytest.fixture
def data(make_activity, register, evaluate):
    """Two conducted activities in two months and two types; "Pitching Workshop" is for TWO categories, so the
    by-category "Activities" column adds up to 3 while only 2 distinct activities were conducted."""
    workshop = make_activity("Pitching Workshop", start=date(2026, 3, 10), categories=("Faculty", "MSMEs"))
    seminar = make_activity("Investor Seminar", start=date(2026, 5, 4), type_name="Seminar", categories=("Faculty",))
    ana = register(workshop, "Ana Cruz", attended=True)
    register(workshop, "Ben Santos")
    register(seminar, "Cara Lim", attended=True)
    evaluate(ana, {"Overall rating": 5, "Venue rating": 4})
    return workshop, seminar


def build(director, report_type: str = "accomplishment", start: str = "2026-01-01", end: str = "2026-12-31", **data):
    report = request(director, report_type=report_type, format="excel", date_range_start=start, date_range_end=end, **data)
    assert services.build_report(report.pk, now=TEST_NOW) == "generated"
    report.refresh_from_db()
    return report, load_workbook(report.file_path.path)


def header_row(sheet) -> int:
    return range_boundaries(next(iter(sheet.tables.values())).ref)[1]


def table_rows(sheet, table) -> list[list]:
    min_col, min_row, max_col, max_row = range_boundaries(table.ref)
    return [[sheet.cell(row=r, column=c) for c in range(min_col, max_col + 1)] for r in range(min_row, max_row + 1)]


# --- Sheets and the title block ----------------------------------------------------------------------------


def test_sheet_order_and_short_names(director, queued, data) -> None:
    _report, workbook = build(director)
    assert workbook.sheetnames == ACCOMPLISHMENT_SHEETS


def test_every_sheet_has_the_compact_title_block(director, queued, data) -> None:
    report, workbook = build(director)
    generated = timezone.localtime(report.generated_at)
    hour = generated.hour % 12 or 12
    subtitles = {"Report info": "Report information", "Summary": "Summary", "Activities": "Activities conducted",
                 "By type": "By activity type", "By category": "By participant category", "Per month": "Per month",
                 "Category per activity": "Participants by category per activity",
                 "Evaluation": "Evaluation results"}
    for sheet in workbook.worksheets:
        lines = [sheet.cell(row=row, column=1).value for row in range(1, 7)]
        assert lines == [
            "CSU-TBI Track — Accomplishment Report",
            "Technology Business Incubator, Caraga State University",
            subtitles[sheet.title],
            "Period: 2026 (January 1 – December 31, 2026)",
            f"Generated {generated:%b} {generated.day}, {generated.year} {hour}:{generated:%M} {generated:%p} (Manila time) "
            f"by {director.name}",
            None,  # one blank row
        ], sheet.title
        assert sheet["A1"].font.bold and sheet["A1"].font.size == 14
        if sheet.max_column > 1:  # merged across the table width (the title block may merge)
            assert f"A1:{get_column_letter(sheet.max_column)}1" in {str(r) for r in sheet.merged_cells.ranges}
        if sheet.tables:
            assert int(sheet.freeze_panes[1:]) <= 9  # 5 title rows + 1 blank + (table title) + header: compact


def test_activity_type_filter_is_named_in_the_title_block(director, queued, data) -> None:
    from activities.models import ActivityType

    seminar = ActivityType.objects.get(name="Seminar")
    _report, workbook = build(director, activity_type=seminar.pk)
    assert workbook["Summary"]["A4"].value == "Period: 2026 (January 1 – December 31, 2026) · Activity type: Seminar"


# --- Excel tables: style, and nothing that makes Excel "repair" the file ------------------------------------


@pytest.mark.parametrize("report_type", list(REPORT_TYPES))
def test_tables_open_without_repair(director, queued, data, add_document, report_type) -> None:
    add_document(data[0], "Activity Design")
    _report, workbook = build(director, report_type)
    names = []
    for sheet in workbook.worksheets:
        merged = [range_boundaries(str(r)) for r in sheet.merged_cells.ranges]
        for table in sheet.tables.values():
            name = table.displayName
            names.append(name)
            assert " " not in name and name[0].isalpha()
            min_col, min_row, max_col, max_row = range_boundaries(table.ref)
            assert max_row > min_row  # a header and at least one row
            header = [sheet.cell(row=min_row, column=c).value for c in range(min_col, max_col + 1)]
            assert all(isinstance(h, str) and h.strip() for h in header), (sheet.title, header)
            assert len({h.lower() for h in header}) == len(header), (sheet.title, header)
            assert table.autoFilter.ref == table.ref  # filter buttons
            # Nothing merged inside a table, and the row just below the table is a total, a note or empty.
            for m_col, m_row, m_max_col, m_max_row in merged:
                assert m_max_row < min_row or m_row > max_row or m_max_col < min_col or m_col > max_col
    assert len(names) == len(set(names))  # unique in the workbook


def test_header_style_banding_and_frozen_header(director, queued, data) -> None:
    _report, workbook = build(director)
    for name in ACCOMPLISHMENT_SHEETS[1:]:
        sheet = workbook[name]
        [table] = sheet.tables.values()
        assert table.tableStyleInfo.showRowStripes
        row = header_row(sheet)
        for cell in table_rows(sheet, table)[0]:
            assert cell.fill.fgColor.rgb.endswith("2A4494") and cell.font.bold and cell.font.color.rgb.endswith("FFFFFF")
        assert sheet.freeze_panes == f"A{row + 1}" == "A8"


def test_header_labels_are_never_empty_or_repeated() -> None:
    table = Table("t", [Column(""), Column("Name"), Column(" name "), Column("Name")], [])
    assert excel.header_labels(table) == ["Column 1", "Name", "name (2)", "Name (3)"]


# --- Formats and alignment ------------------------------------------------------------------------------------


def test_number_date_and_text_formats(director, queued, data) -> None:
    _report, workbook = build(director)
    sheet = workbook["Activities"]
    header = [cell.value for cell in sheet[7]]
    row = {name: cell for name, cell in zip(header, sheet[8])}  # the first data row
    assert row["Start"].number_format == "mmm d, yyyy" and row["Start"].alignment.horizontal == "center"
    assert row["Registered"].number_format == "#,##0" and row["Registered"].alignment.horizontal == "right"
    assert row["Attendance rate"].number_format == "0.0%" and row["Attendance rate"].alignment.horizontal == "right"
    assert row["Title"].alignment.horizontal == "left" and row["Title"].alignment.wrap_text
    widths = [sheet.column_dimensions[letter].width for letter in "ABCDEFGHIJK"]
    assert all(excel.MIN_WIDTH <= width <= 60 for width in widths)
    evaluation = workbook["Evaluation"]
    assert evaluation.cell(row=8, column=2).number_format == "0.00"  # ratings with 2 decimals
    summary = {r[0].value: r[1] for r in workbook["Summary"].iter_rows(min_row=8) if r[0].value}
    assert summary["Attendance rate (attended / registered)"].number_format == "0.0%"
    assert summary["Average rating (1-5, all rating questions)"].number_format == "0.00"


def test_money_format_quotes_the_peso_sign() -> None:
    assert excel.NUMBER_FORMATS[MONEY] == '"₱"#,##0.00'
    workbook = Workbook()
    excel.write_value(workbook.active["A1"], 30000, MONEY)
    buffer = BytesIO()
    workbook.save(buffer)
    cell = load_workbook(BytesIO(buffer.getvalue())).active["A1"]
    assert cell.value == 30000 and cell.number_format == '"₱"#,##0.00' and cell.alignment.horizontal == "right"


def test_datetimes_are_written_in_manila_time_without_tzinfo(director, queued, data) -> None:
    assert excel.excel_datetime(datetime(2026, 10, 23, 1, 30, tzinfo=UTC)) == datetime(2026, 10, 23, 9, 30)
    workbook = Workbook()
    excel.write_value(workbook.active["A1"], datetime(2026, 11, 6, 7, 5, tzinfo=UTC), "text")  # aware: no error
    assert workbook.active["A1"].value == datetime(2026, 11, 6, 15, 5)
    assert workbook.active["A1"].number_format == "mmm d, yyyy h:mm AM/PM"
    report, book = build(director)
    info = {row[0].value: row[1] for row in book["Report info"].iter_rows(min_row=7)}
    expected = timezone.localtime(report.generated_at).replace(tzinfo=None)
    assert info["Generated at (Manila time)"].value.tzinfo is None
    assert abs(info["Generated at (Manila time)"].value - expected) < timedelta(seconds=1)


# --- Totals rows ------------------------------------------------------------------------------------------------


@pytest.mark.parametrize(("sheet_name", "label", "with_rate"), [
    ("By type", "Total", True), ("By category", "Total (distinct activities)", True), ("Per month", "Total", False),
])
def test_totals_come_from_the_period_summary(director, queued, data, sheet_name, label, with_rate) -> None:
    _report, workbook = build(director)
    sheet = workbook[sheet_name]
    [table] = sheet.tables.values()
    total_row = range_boundaries(table.ref)[3] + 1  # directly below the table, outside its range
    cells = [sheet.cell(row=total_row, column=c) for c in range(1, 6 if with_rate else 5)]
    summary = metrics.period_summary(PERIOD, TEST_NOW)
    expected = [label, summary["activities_conducted"], summary["registered"], summary["attended"]]
    expected += [summary["attendance_rate"]] if with_rate else []
    assert [cell.value for cell in cells] == expected
    assert all(cell.font.bold and cell.border.top.style == "thin" for cell in cells)
    assert cells[1].number_format == "#,##0"


def test_category_total_counts_distinct_activities(director, queued, data) -> None:
    _report, workbook = build(director)
    sheet = workbook["By category"]
    [table] = sheet.tables.values()
    rows = table_rows(sheet, table)[1:]
    assert sum(row[1].value for row in rows) == 3  # Pitching Workshop counts under Faculty AND MSMEs
    assert sheet.cell(row=range_boundaries(table.ref)[3] + 1, column=2).value == 2  # distinct activities
    note_row = range_boundaries(table.ref)[3] + 2
    assert sheet.cell(row=note_row, column=1).value == metrics.CATEGORY_NOTE  # merged note below the total


# --- Print setup ------------------------------------------------------------------------------------------------


def test_print_setup_on_every_sheet(director, queued, data) -> None:
    _report, workbook = build(director)
    for sheet in workbook.worksheets:
        setup = sheet.page_setup
        assert (setup.orientation, setup.paperSize, setup.fitToWidth, setup.fitToHeight) == ("landscape", 9, 1, 0), sheet.title
        assert sheet.sheet_properties.pageSetUpPr.fitToPage
        assert sheet.oddFooter.center.text == "Page &P of &N"
        if sheet.tables:
            row = header_row(sheet)
            assert sheet.print_title_rows == f"${row}:${row}"


# --- Report info ------------------------------------------------------------------------------------------------


def test_report_info_sheet(director, queued, data) -> None:
    _report, workbook = build(director, start="2026-01-01", end="2026-06-30")
    sheet = workbook["Report info"]
    assert not sheet.tables and sheet.max_column == 2
    rows = {row[0].value: row for row in sheet.iter_rows(min_row=7)}
    assert rows["Period"][1].value == "January to June 2026 (January 1 – June 30, 2026)"
    start = rows["Period start"][1]
    assert start.value.date() == date(2026, 1, 1) and start.number_format == "mmm d, yyyy"
    assert all(row[0].font.bold and row[1].alignment.horizontal == "left" for row in rows.values())
    assert "Result" not in rows


# --- Empty cases -----------------------------------------------------------------------------------------------


def test_empty_period_shows_one_row_per_section(director, queued, data) -> None:
    _report, workbook = build(director, start="2027-01-01", end="2027-06-30")
    assert workbook.sheetnames == ACCOMPLISHMENT_SHEETS
    for name in ACCOMPLISHMENT_SHEETS[1:]:
        sheet = workbook[name]
        assert sheet["A7"].value == NO_ACTIVITIES and sheet.max_row == 7 and not sheet.tables, name
        assert sheet.page_setup.orientation == "landscape"
    info = {row[0].value: row[1].value for row in workbook["Report info"].iter_rows(min_row=7)}
    assert info["Result"] == NO_ACTIVITIES


def test_empty_table_shows_one_row_instead_of_a_table(director, queued, data) -> None:
    _report, workbook = build(director, "participation")  # nobody in the data is a startup
    sheet = workbook["Startups"]
    assert not sheet.tables and sheet["A7"].value == excel.EMPTY_TABLE


def test_two_tables_on_one_sheet(director, queued, data) -> None:
    _report, workbook = build(director, "document_compliance")
    sheet = workbook["Documents"]
    tables = list(sheet.tables.values())
    assert len(tables) == 2
    assert sheet["A7"].value == "Overview" and sheet["A7"].font.bold  # a table title above each table
    assert sheet.freeze_panes == "A9"  # below the first table's header (row 8)
    first_end = range_boundaries(tables[0].ref)[3]
    assert range_boundaries(tables[1].ref)[1] > first_end + 1  # never touching
