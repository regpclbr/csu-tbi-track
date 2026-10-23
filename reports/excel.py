"""The Excel version of a report (openpyxl): a "Filters" sheet (report type, the range in words and as
dates, activity type, who, when), then one sheet per section. Numbers are real numbers with number
formats, dates are real dates, and the header row of each sheet is frozen.

Formula injection: any text that starts with = + - @ tab or carriage return (for example an activity
titled "=HYPERLINK(...)") is written as TEXT, never as a formula (`safe_cell`).
"""

from datetime import date, datetime
from io import BytesIO

from django.utils import timezone
from openpyxl import Workbook
from openpyxl.cell import Cell as XlCell
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter

from .content import AI_SUMMARY, DATE, DECIMAL, INT, NO_ACTIVITIES, PERCENT, Cell, ReportContent, Section, Table

FORMULA_STARTS = ("=", "+", "-", "@", "\t", "\r")
NUMBER_FORMATS = {INT: "#,##0", PERCENT: "0.0%", DECIMAL: "0.00", DATE: "yyyy-mm-dd"}
HEADER_FONT = Font(bold=True, color="FFFFFF")
HEADER_FILL = PatternFill("solid", fgColor="1F3A5F")
TITLE_FONT = Font(bold=True, size=12)
MAX_COLUMN_WIDTH = 60


def safe_cell(cell: XlCell, value) -> None:
    """Put `value` in `cell`. Text is always stored as text: one that starts like a formula gets the
    text data type and Excel's quote prefix, so it is shown as typed and never calculated."""
    cell.value = value
    if isinstance(value, str) and value.startswith(FORMULA_STARTS):
        cell.data_type = "s"
        cell.quotePrefix = True


def _write(cell: XlCell, value, kind: str) -> None:
    if isinstance(value, Cell):
        value, kind = value.value, value.kind
    if isinstance(value, datetime):
        value = timezone.localtime(value).replace(tzinfo=None)  # Excel has no time zones: Manila time
    safe_cell(cell, value)
    if kind in NUMBER_FORMATS and value is not None and not isinstance(value, str):
        cell.number_format = NUMBER_FORMATS[kind]


def _sheet_name(label: str, used: set[str]) -> str:
    name = "".join(ch for ch in label if ch not in '[]:*?/\\')[:31]
    candidate, number = name, 2
    while candidate in used:
        suffix = f" {number}"
        candidate, number = name[: 31 - len(suffix)] + suffix, number + 1
    used.add(candidate)
    return candidate


def _write_table(sheet, table: Table, row: int, with_title: bool) -> int:
    """Write one table starting at `row`; returns the next free row."""
    if with_title:
        safe_cell(sheet.cell(row=row, column=1), table.title)
        sheet.cell(row=row, column=1).font = TITLE_FONT
        row += 1
    for index, column in enumerate(table.columns, start=1):
        cell = sheet.cell(row=row, column=index)
        safe_cell(cell, column.label)
        cell.font, cell.fill = HEADER_FONT, HEADER_FILL
        cell.alignment = Alignment(wrap_text=True, vertical="top")
    row += 1
    for values in table.rows:
        for index, (value, column) in enumerate(zip(values, table.columns), start=1):
            _write(sheet.cell(row=row, column=index), value, column.kind)
        row += 1
    if table.note:
        safe_cell(sheet.cell(row=row, column=1), table.note)
        sheet.cell(row=row, column=1).font = Font(italic=True)
        row += 1
    return row + 1


def _fit_columns(sheet) -> None:
    widths: dict[int, int] = {}
    for row in sheet.iter_rows():
        for cell in row:
            if cell.value is None:
                continue
            length = 12 if isinstance(cell.value, (date, int, float)) else len(str(cell.value))
            widths[cell.column] = max(widths.get(cell.column, 8), min(length + 2, MAX_COLUMN_WIDTH))
    for column, width in widths.items():
        sheet.column_dimensions[get_column_letter(column)].width = width


def _section_sheet(sheet, section: Section) -> None:
    if section.key == AI_SUMMARY:
        headers = ["AI-generated summary"]
        safe_cell(sheet.cell(row=1, column=1), headers[0])
        sheet.cell(row=1, column=1).font, sheet.cell(row=1, column=1).fill = HEADER_FONT, HEADER_FILL
        safe_cell(sheet.cell(row=2, column=1), section.note)
        sheet.cell(row=2, column=1).font = Font(italic=True)
        safe_cell(sheet.cell(row=3, column=1), section.text)
        sheet.cell(row=3, column=1).alignment = Alignment(wrap_text=True, vertical="top")
        sheet.column_dimensions["A"].width = 100
        sheet.freeze_panes = "A2"
        return
    row = 1
    for number, table in enumerate(section.tables):
        # The first table's header is row 1 (frozen); later tables get a title row above them.
        row = _write_table(sheet, table, row, with_title=number > 0)
    if section.note:
        safe_cell(sheet.cell(row=row, column=1), section.note)
        sheet.cell(row=row, column=1).font = Font(italic=True)
    sheet.freeze_panes = "A2"
    _fit_columns(sheet)


def build_workbook(content: ReportContent) -> bytes:
    workbook = Workbook()
    info = workbook.active
    info.title = "Filters"
    rows = [
        ("Report", content.report_type_label),
        ("Period", content.period_in_words),
        ("Period start", content.filters.start),
        ("Period end", content.filters.end),
        ("Activity type", content.activity_type_name),
        ("Generated by", content.generated_by),
        ("Generated at (Manila time)", content.generated_at),
        ("Counting rule", "Only Conducted activities whose end date is in the period are counted."),
        ("Sections", ", ".join(section.label for section in content.sections)),
    ]
    if not content.has_activities:
        rows.append(("Result", NO_ACTIVITIES))
    for column, label in enumerate(("Item", "Value"), start=1):
        cell = info.cell(row=1, column=column)
        safe_cell(cell, label)
        cell.font, cell.fill = HEADER_FONT, HEADER_FILL
    for index, (label, value) in enumerate(rows, start=2):
        safe_cell(info.cell(row=index, column=1), label)
        kind = DATE if isinstance(value, date) and not isinstance(value, datetime) else ""
        _write(info.cell(row=index, column=2), value, kind)
        if isinstance(value, datetime):
            info.cell(row=index, column=2).number_format = "yyyy-mm-dd hh:mm"
    info.freeze_panes = "A2"
    _fit_columns(info)

    used = {"Filters"}
    for section in content.sections:
        _section_sheet(workbook.create_sheet(_sheet_name(section.label, used)), section)

    buffer = BytesIO()
    workbook.save(buffer)
    return buffer.getvalue()

