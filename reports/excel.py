"""The Excel version of a report (openpyxl), laid out as a printable report (ASSUMPTIONS 207, 338).

Sheets: "Report info", then one sheet per section in report order, with short names (SHEET_NAMES). Every sheet:
- a compact title block (5 rows + 1 blank): report name, the TBI, the sheet's subtitle, the period, who generated
  it and when (Manila time); merged across the table width (the only merged cells besides the notes);
- each data table is a real Excel table (header #1A5C38 (the primary deep forest green, ASSUMPTIONS 459, 465) with white bold text, banded rows, filter buttons), the
  panes frozen below the first table's header; a totals row (Table.total, from metrics.period_summary) and notes go
  BELOW the table range, never inside it;
- an empty section or table is one row of text instead of an empty table;
- print: A4 landscape, 1 page wide, the table header repeated on every page, footer "Page x of y".

Only layout lives here: every value comes from reports/content.py (metrics). Numbers stay numbers and dates stay
dates, with number formats. Date-times are converted to Manila time and written without tzinfo (Excel has no time
zones; openpyxl refuses aware values).

Formula injection: any text that starts with = + - @ tab or carriage return (for example an activity titled
"=HYPERLINK(...)") is written as TEXT, never as a formula (`safe_cell`).
"""

import math
from datetime import date, datetime
from io import BytesIO

from django.utils import timezone
from openpyxl import Workbook
from openpyxl.cell import Cell as XlCell
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils import get_column_letter
from openpyxl.worksheet.filters import AutoFilter
from openpyxl.worksheet.page import PageMargins
from openpyxl.worksheet.table import Table as XlTable
from openpyxl.worksheet.table import TableStyleInfo

from .content import (
    AI_SUMMARY, DATE, DECIMAL, INT, MONEY, NO_ACTIVITIES, PERCENT, TEXT, Cell, ReportContent, Section, Table,
)

FORMULA_STARTS = ("=", "+", "-", "@", "\t", "\r")
DATETIME = "datetime"  # only used here (Report info: generated at)
NUMBER_FORMATS = {
    INT: "#,##0",
    PERCENT: "0.0%",
    DECIMAL: "0.00",  # ratings
    DATE: "mmm d, yyyy",  # Nov 6, 2026
    DATETIME: "mmm d, yyyy h:mm AM/PM",
    MONEY: '"₱"#,##0.00',  # the symbol quoted, so Excel and WPS both read it
}
NUMBER_KINDS = (INT, PERCENT, DECIMAL, MONEY)

SHEET_NAMES = {
    "summary": "Summary",
    AI_SUMMARY: "AI summary",
    "activities": "Activities",
    "by_type": "By type",
    "by_category": "By category",
    "category_by_activity": "Category per activity",
    "monthly": "Per month",
    "budget": "Budget",  # ASSUMPTIONS 433
    "evaluation": "Evaluation",
    "documents": "Documents",
    "certificates": "Certificates",
}
INFO_SHEET = "Report info"
UNIVERSITY_LINE = "Technology Business Incubator, Caraga State University"
EMPTY_TABLE = "Nothing to show for this period."
FOOTER = "Page &P of &N"

BRAND_NAVY = "1A5C38"  # the app's primary colour, deep forest green since ASSUMPTIONS 465 (the name is kept); white bold text on it 8.0:1
HEADER_FONT = Font(bold=True, color="FFFFFF")
HEADER_FILL = PatternFill("solid", fgColor=BRAND_NAVY)
TABLE_STYLE = "TableStyleMedium2"  # banded rows; the header colour is set on the cells (#1A5C38)
TITLE_FONTS = [
    Font(bold=True, size=14, color="1E3A8A"),
    Font(size=10, color="475569"),
    Font(bold=True, size=12, color="0F172A"),
    Font(size=10, color="475569"),
    Font(size=10, color="475569"),
]
TABLE_TITLE_FONT = Font(bold=True, size=11, color="0F172A")
NOTE_FONT = Font(italic=True, size=10, color="475569")
TOTAL_FONT = Font(bold=True)
TOTAL_BORDER = Border(top=Side(style="thin", color="0F172A"))
TITLE_ROWS = 5  # then one blank row: the first table starts on row 7
FIRST_ROW = TITLE_ROWS + 2
MIN_WIDTH, MAX_WIDTH = 10, 50
LINE_HEIGHT = 15  # points per line of 11 pt text
MAX_ROW_HEIGHT = 409  # Excel's limit


def safe_cell(cell: XlCell, value) -> None:
    """Put `value` in `cell`. Text is always stored as text: one that starts like a formula gets the
    text data type and Excel's quote prefix, so it is shown as typed and never calculated."""
    cell.value = value
    if isinstance(value, str) and value.startswith(FORMULA_STARTS):
        cell.data_type = "s"
        cell.quotePrefix = True


def excel_datetime(value: datetime) -> datetime:
    """Manila wall-clock time without tzinfo (Excel has no time zones; openpyxl refuses aware datetimes)."""
    return timezone.localtime(value).replace(tzinfo=None) if timezone.is_aware(value) else value


def manila_text(value: datetime) -> str:
    """"Nov 6, 2026 3:05 PM" in Manila time (the title block)."""
    local = timezone.localtime(value) if timezone.is_aware(value) else value
    hour = local.hour % 12 or 12
    return f"{local:%b} {local.day}, {local.year} {hour}:{local:%M} {local:%p}"


def _kind_of(value, column_kind: str) -> tuple[object, str]:
    if isinstance(value, Cell):
        return value.value, value.kind
    return value, column_kind


def _alignment(kind: str, value) -> Alignment:
    if kind == DATE or isinstance(value, (date, datetime)):
        return Alignment(horizontal="center", vertical="top")
    if kind in NUMBER_KINDS and not isinstance(value, str):
        return Alignment(horizontal="right", vertical="top")
    return Alignment(horizontal="left", vertical="top", wrap_text=True)


def write_value(cell: XlCell, value, kind: str) -> None:
    """One table value: text as text, numbers and dates with their number format and alignment."""
    value, kind = _kind_of(value, kind)
    if isinstance(value, datetime):
        value, kind = excel_datetime(value), DATETIME
    elif isinstance(value, date) and kind == TEXT:
        kind = DATE
    safe_cell(cell, value)
    if kind in NUMBER_FORMATS and value is not None and not isinstance(value, str):
        cell.number_format = NUMBER_FORMATS[kind]
    cell.alignment = _alignment(kind, value)


def _display_length(value, kind: str) -> int:
    """About how many characters a value needs (for the column width)."""
    value, kind = _kind_of(value, kind)
    if value is None:
        return 0
    if isinstance(value, datetime):
        return 20
    if isinstance(value, date):
        return 12
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        if kind == PERCENT:
            return 7
        return len(f"{value:,.2f}") if kind in (DECIMAL, MONEY) else len(f"{value:,}")
    return max(len(line) for line in str(value).splitlines() or [""])


def header_labels(table: Table) -> list[str]:
    """The header cells: never empty, never repeated within one table (otherwise Excel "repairs" the file)."""
    labels, seen = [], set()
    for number, column in enumerate(table.columns, start=1):
        label = str(column.label or "").strip() or f"Column {number}"
        candidate, copy = label, 2
        while candidate.lower() in seen:
            candidate, copy = f"{label} ({copy})", copy + 1
        seen.add(candidate.lower())
        labels.append(candidate)
    return labels


class _SheetWriter:
    """Writes one sheet top to bottom and remembers what the layout needs (widths, first header, merged notes)."""

    def __init__(self, workbook_state: dict, sheet, content: ReportContent, subtitle: str):
        self.state, self.sheet, self.content, self.subtitle = workbook_state, sheet, content, subtitle
        self.row = FIRST_ROW
        self.columns = 1  # how many columns the content uses (the title block and notes are merged across them)
        self.widths: dict[int, int] = {}
        self.first_header_row: int | None = None
        self.notes: list[tuple[int, str]] = []

    # --- content ---

    def _measure(self, column: int, length: int) -> None:
        self.widths[column] = max(self.widths.get(column, MIN_WIDTH), min(length + 2, MAX_WIDTH))

    def text_row(self, text: str, font: Font = NOTE_FONT) -> None:
        """A line of text (a note, an empty-section message); merged across the content width later."""
        safe_cell(self.sheet.cell(row=self.row, column=1), text)
        self.sheet.cell(row=self.row, column=1).font = font
        self.notes.append((self.row, text))
        self.row += 1

    def table(self, table: Table, title: str | None) -> None:
        """A table title (optional), the Excel table, its totals row and note; then one blank row."""
        sheet = self.sheet
        self.columns = max(self.columns, len(table.columns))
        if title:
            safe_cell(sheet.cell(row=self.row, column=1), title)
            sheet.cell(row=self.row, column=1).font = TABLE_TITLE_FONT
            self.row += 1
        if not table.rows:
            self.text_row(EMPTY_TABLE)
        else:
            header_row = self.row
            for number, (label, column) in enumerate(zip(header_labels(table), table.columns), start=1):
                cell = sheet.cell(row=header_row, column=number)
                safe_cell(cell, label)
                cell.data_type = "s"  # a header is always a string
                cell.font, cell.fill = HEADER_FONT, HEADER_FILL
                horizontal = "center" if column.kind == DATE else "right" if column.kind in NUMBER_KINDS else "left"
                cell.alignment = Alignment(horizontal=horizontal, vertical="center", wrap_text=True)
                self._measure(number, min(len(label), 24))  # long headers wrap instead of widening the column
            for values in table.rows:
                self.row += 1
                for number, (value, column) in enumerate(zip(values, table.columns), start=1):
                    write_value(sheet.cell(row=self.row, column=number), value, column.kind)
                    self._measure(number, _display_length(value, column.kind))
            self._add_excel_table(header_row, self.row, len(table.columns))
            self.row += 1
            if table.total:
                for number, (value, column) in enumerate(zip(table.total, table.columns), start=1):
                    cell = sheet.cell(row=self.row, column=number)
                    write_value(cell, value, column.kind)
                    cell.font, cell.border = TOTAL_FONT, TOTAL_BORDER
                    self._measure(number, _display_length(value, column.kind))
                self.row += 1
        if table.note:
            self.text_row(table.note)
        self.row += 1

    def _add_excel_table(self, header_row: int, last_row: int, columns: int) -> None:
        self.state["tables"] += 1
        ref = f"A{header_row}:{get_column_letter(columns)}{last_row}"
        # Unique in the workbook, no spaces, never looks like a cell reference.
        xl_table = XlTable(displayName=f"Table{self.state['tables']}_{self.state['key']}", ref=ref)
        xl_table.tableStyleInfo = TableStyleInfo(name=TABLE_STYLE, showRowStripes=True, showColumnStripes=False)
        xl_table.autoFilter = AutoFilter(ref=ref)
        self.sheet.add_table(xl_table)
        if self.first_header_row is None:
            self.first_header_row = header_row

    # --- finishing ---

    def finish(self) -> None:
        sheet = self.sheet
        for column, width in self.widths.items():
            sheet.column_dimensions[get_column_letter(column)].width = width
        self._title_block()
        content_width = sum(sheet.column_dimensions[get_column_letter(c)].width or MIN_WIDTH
                            for c in range(1, self.columns + 1))
        for row, text in self.notes:
            self._merge(row)
            sheet.cell(row=row, column=1).alignment = Alignment(horizontal="left", vertical="top", wrap_text=True)
            lines = math.ceil(len(text) * 1.1 / max(content_width, 1)) if len(text) * 1.1 > content_width else 1
            sheet.row_dimensions[row].height = min(MAX_ROW_HEIGHT, LINE_HEIGHT * lines)
        if self.first_header_row:
            sheet.freeze_panes = f"A{self.first_header_row + 1}"
            sheet.print_title_rows = f"{self.first_header_row}:{self.first_header_row}"
        print_setup(sheet)

    def _merge(self, row: int) -> None:
        if self.columns > 1:
            self.sheet.merge_cells(start_row=row, start_column=1, end_row=row, end_column=self.columns)

    def _title_block(self) -> None:
        """Five lines, each one row high; the columns are widened so no line is cut off."""
        lines = title_lines(self.content, self.subtitle)
        for row, (text, font) in enumerate(zip(lines, TITLE_FONTS), start=1):
            cell = self.sheet.cell(row=row, column=1)
            safe_cell(cell, text)
            cell.font = font
            cell.alignment = Alignment(horizontal="left", vertical="center")
            self._merge(row)
        self.sheet.row_dimensions[1].height = 22
        # Size 14 text is about 1.3 times as wide as the 11 pt characters Excel measures widths in.
        needed = max(len(lines[0]) * 1.3, *(len(line) * 1.0 for line in lines[1:])) + 2
        letters = [get_column_letter(c) for c in range(1, self.columns + 1)]
        current = sum(self.sheet.column_dimensions[letter].width or MIN_WIDTH for letter in letters)
        if current < needed:
            first = self.sheet.column_dimensions[letters[0]]
            extra = needed - current
            grow_first = min(extra, max(0, 60 - (first.width or MIN_WIDTH)))
            first.width = (first.width or MIN_WIDTH) + grow_first
            if extra > grow_first:
                last = self.sheet.column_dimensions[letters[-1]]
                last.width = (last.width or MIN_WIDTH) + extra - grow_first


def title_lines(content: ReportContent, subtitle: str) -> list[str]:
    if content.meta_lines is not None:  # one activity (ASSUMPTIONS 401-402): "Activity: … · Dates: …"
        period = " · ".join(f"{label}: {value}" for label, value in content.meta_lines)
    else:
        period = f"Period: {content.period_text}"
        if content.filters.activity_type_id:
            period += f" · Activity type: {content.activity_type_name}"
    return [
        f"CSU-TBI Track — {content.report_type_label}",
        UNIVERSITY_LINE,
        subtitle,
        period,
        f"Generated {manila_text(content.generated_at)} (Manila time) by {content.generated_by}",
    ]


def print_setup(sheet) -> None:
    """A4 landscape, fit to one page wide (as many pages tall as needed), footer "Page x of y"."""
    sheet.page_setup.orientation = "landscape"
    sheet.page_setup.paperSize = sheet.PAPERSIZE_A4
    sheet.page_setup.fitToWidth = 1
    sheet.page_setup.fitToHeight = 0
    sheet.sheet_properties.pageSetUpPr.fitToPage = True
    sheet.page_margins = PageMargins(left=0.5, right=0.5, top=0.6, bottom=0.7, header=0.3, footer=0.3)
    sheet.oddFooter.center.text = FOOTER
    sheet.oddFooter.center.size = 9


def _sheet_name(section: Section, used: set[str]) -> str:
    name = "".join(ch for ch in SHEET_NAMES.get(section.key, section.label) if ch not in '[]:*?/\\')[:31]
    candidate, number = name, 2
    while candidate in used:
        suffix = f" {number}"
        candidate, number = name[: 31 - len(suffix)] + suffix, number + 1
    used.add(candidate)
    return candidate


def _section_sheet(writer: _SheetWriter, section: Section) -> None:
    if section.key == AI_SUMMARY:
        writer.columns = 1
        writer.widths[1] = 100
        if section.note:
            writer.text_row(section.note)
        for paragraph in (p.strip() for p in section.text.split("\n")):
            if paragraph:
                cell = writer.sheet.cell(row=writer.row, column=1)
                safe_cell(cell, paragraph)
                cell.alignment = Alignment(horizontal="left", vertical="top", wrap_text=True)
                lines = math.ceil(len(paragraph) / 95) or 1
                writer.sheet.row_dimensions[writer.row].height = min(MAX_ROW_HEIGHT, LINE_HEIGHT * lines)
                writer.row += 1
        return
    if not section.tables:  # e.g. "No conducted activities in this period."
        writer.widths[1] = max(writer.widths.get(1, MIN_WIDTH), 60)
        writer.text_row(section.note or NO_ACTIVITIES, font=Font(italic=True, size=11, color="0F172A"))
        return
    with_titles = len(section.tables) > 1  # one table: the sheet's subtitle already names it
    for table in section.tables:
        writer.table(table, table.title if with_titles else None)
    if section.note:
        writer.text_row(section.note)


def _info_sheet(writer: _SheetWriter, content: ReportContent) -> None:
    """"Report info": two columns, bold labels, values left-aligned, dates in the report's format."""
    if content.meta_lines is not None:  # one activity (ASSUMPTIONS 401-402)
        rows = [
            ("Report", content.report_type_label),
            *content.meta_lines,
            ("Generated by", content.generated_by),
            ("Generated at (Manila time)", content.generated_at),
            ("Sections", ", ".join(section.label for section in content.sections)),
        ]
        _info_rows(writer, rows)
        return
    rows = [
        ("Report", content.report_type_label),
        ("Period", content.period_text),
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
    _info_rows(writer, rows)


def _info_rows(writer: _SheetWriter, rows: list[tuple[str, object]]) -> None:
    writer.columns = 2
    for label, value in rows:
        label_cell = writer.sheet.cell(row=writer.row, column=1)
        safe_cell(label_cell, label)
        label_cell.font = Font(bold=True)
        label_cell.alignment = Alignment(horizontal="left", vertical="top")
        cell = writer.sheet.cell(row=writer.row, column=2)
        write_value(cell, value, TEXT)
        cell.alignment = Alignment(horizontal="left", vertical="top", wrap_text=True)
        writer._measure(1, len(label))
        writer.widths[2] = max(writer.widths.get(2, MIN_WIDTH), min(_display_length(value, TEXT) + 2, 80))
        writer.row += 1


def build_workbook(content: ReportContent) -> bytes:
    workbook = Workbook()
    state = {"tables": 0, "key": "info"}
    info = workbook.active
    info.title = INFO_SHEET
    writer = _SheetWriter(state, info, content, "Report information")
    _info_sheet(writer, content)
    writer.finish()

    used = {INFO_SHEET}
    for section in content.sections:
        state["key"] = section.key
        writer = _SheetWriter(state, workbook.create_sheet(_sheet_name(section, used)), content, section.label)
        _section_sheet(writer, section)
        writer.finish()

    buffer = BytesIO()
    workbook.save(buffer)
    return buffer.getvalue()
