"""The PDF version of a report. Rendered through certificates.pdf.get_renderer() (the one WeasyPrint
injection point; tests use FakeRenderer, and WeasyPrint may only fetch data: URIs).

Charts are simple horizontal bar charts drawn as inline SVG: one series, the value written at the
end of each bar, and the same numbers in the table next to them.

Tables (ASSUMPTIONS 385): landscape A4, `table-layout: fixed` with the column widths computed here (`pdf_table`), so
nothing can run off the page. Layout only: the values and numbers are the report content's, unchanged; the PDF only
prints Start + End as one "Date" column and the time in its compact form (the Excel file keeps every column).
"""

from collections.abc import Callable
from dataclasses import dataclass
from datetime import date, datetime
from html import escape

from django.template.loader import render_to_string
from django.utils import timezone
from django.utils.safestring import mark_safe

from activities.formatting import compact_time_range, format_date_span
from certificates.pdf import get_renderer

from .content import DATE, DECIMAL, INT, NO_ACTIVITIES, PERCENT, Cell, Chart, Column, ReportContent, Table, long_date

EMPTY = "—"

# Chart look (reference palette: series slot 1, text inks, recessive baseline).
BAR_COLOR = "#2A4494"  # the primary navy (ASSUMPTIONS 413): dark enough to print as a solid dark grey in black and white
TEXT_PRIMARY = "#0b0b0b"
TEXT_SECONDARY = "#52514e"
BASELINE = "#c9c8c3"
LABEL_WIDTH, BAR_AREA, VALUE_WIDTH, ROW_HEIGHT, BAR_HEIGHT = 190, 380, 60, 22, 14
MAX_LABEL_CHARS = 30

# Table layout. The page is landscape A4 with 14 mm side margins (@page in reports/report_pdf.html: keep both in step).
PAGE_CONTENT_WIDTH_MM = 297 - 2 * 14
NUMBER_WIDTH_MM = 21  # fits a one-word header such as "Evaluations" at the table's header size
DATE_WIDTH_MM = 25  # "Sep 30, 2026"
DATE_SPAN_WIDTH_MM = 29  # "Mar 26–27, 2026" on one line (spans over two months wrap after the dash)
TIME_WIDTH_MM = 31  # "10:00 AM–12:00 PM" on one line, also in DejaVu Sans (wider than Arial)
TEXT_WEIGHTS = {"title": 3.0, "venue": 2.0}  # share of the remaining width; other text columns 1.5
DEFAULT_TEXT_WEIGHT = 1.5
TEXT_MIN_WIDTH_MM, TEXT_MAX_WIDTH_MM = 18, 110  # short tables (Measure / Value) stay narrow instead of page-wide
WIDE_TABLE_COLUMNS = 6  # more columns than this: the smaller table font (never below 8 pt)
NUMBER_KINDS = (INT, PERCENT, DECIMAL)


def format_value(value, kind: str) -> str:
    """How a value is printed: 1,234 / 85.0% / 4.25 / Oct 5, 2026; "—" for nothing."""
    if isinstance(value, Cell):
        value, kind = value.value, value.kind
    if value is None or value == "":
        return EMPTY
    if isinstance(value, datetime):
        return timezone.localtime(value).strftime("%b %d, %Y %I:%M %p")
    if isinstance(value, date):
        return long_date(value)
    if kind == PERCENT:
        return f"{value * 100:.1f}%"
    if kind == DECIMAL:
        return f"{value:.2f}"
    if kind == INT:
        return f"{value:,}"
    return str(value)


def bar_chart_svg(chart: Chart) -> str:
    """A single-series horizontal bar chart. Labels are escaped (they can be typed by staff)."""
    items = chart.items
    if not items:
        return ""
    largest = chart.max_value or max((value for _, value in items), default=0) or 1
    width = LABEL_WIDTH + BAR_AREA + VALUE_WIDTH
    height = ROW_HEIGHT * len(items) + 8
    parts = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" viewBox="0 0 {width} {height}" '
        f'role="img" aria-label="{escape(chart.title)}">'
    ]
    for index, (label, value) in enumerate(items):
        y = 4 + index * ROW_HEIGHT
        short = label if len(label) <= MAX_LABEL_CHARS else label[: MAX_LABEL_CHARS - 1] + "…"
        bar = max(0.0, min(1.0, (value or 0) / largest)) * BAR_AREA
        text_y = y + BAR_HEIGHT - 3
        parts.append(
            f'<text x="{LABEL_WIDTH - 8}" y="{text_y}" text-anchor="end" font-size="10" fill="{TEXT_SECONDARY}">{escape(short)}</text>'
        )
        if bar > 0:
            parts.append(f'<rect x="{LABEL_WIDTH}" y="{y}" width="{bar:.1f}" height="{BAR_HEIGHT}" rx="2" fill="{BAR_COLOR}"/>')
        parts.append(
            f'<text x="{LABEL_WIDTH + bar + 6:.1f}" y="{text_y}" font-size="10" fill="{TEXT_PRIMARY}">'
            f"{escape(format_value(value, chart.kind))}</text>"
        )
    parts.append(f'<line x1="{LABEL_WIDTH}" y1="2" x2="{LABEL_WIDTH}" y2="{height - 2}" stroke="{BASELINE}" stroke-width="1"/>')
    parts.append("</svg>")
    return "".join(parts)


@dataclass
class PdfColumn:
    """One printed column: its header, CSS class (num / date / time / text), how a row's cell is printed, its width."""

    label: str
    css: str
    cell: Callable[[list], str]
    width_mm: float = 0.0
    weight: float = 0.0  # text columns share the width left after the fixed ones


def _date_span(start, end) -> str:
    if isinstance(start, date) and isinstance(end, date):
        return format_date_span(start, end)
    return format_value(start or end, DATE)


def pdf_columns(columns: list[Column]) -> list[PdfColumn]:
    """The printed columns: Start + End as one "Date" column (keys "start" / "end"), "time" compact, fixed widths for
    numbers, dates and times, a weight for text."""
    keys = [column.key for column in columns]
    merge_dates = "start" in keys and "end" in keys
    printed = []
    for index, column in enumerate(columns):
        if merge_dates and column.key == "end":
            continue
        if merge_dates and column.key == "start":
            start, end = index, keys.index("end")
            printed.append(PdfColumn("Date", "date", lambda row, s=start, e=end: _date_span(row[s], row[e]),
                                     width_mm=DATE_SPAN_WIDTH_MM))
        elif column.key == "time":
            printed.append(PdfColumn(column.label, "time",
                                     lambda row, i=index, k=column.kind: compact_time_range(format_value(row[i], k)),
                                     width_mm=TIME_WIDTH_MM))
        elif column.kind in NUMBER_KINDS:
            printed.append(PdfColumn(column.label, "num", lambda row, i=index, k=column.kind: format_value(row[i], k),
                                     width_mm=NUMBER_WIDTH_MM))
        elif column.kind == DATE:
            printed.append(PdfColumn(column.label, "date", lambda row, i=index, k=column.kind: format_value(row[i], k),
                                     width_mm=DATE_WIDTH_MM))
        else:
            printed.append(PdfColumn(column.label, "text", lambda row, i=index, k=column.kind: format_value(row[i], k),
                                     weight=TEXT_WEIGHTS.get(column.key, DEFAULT_TEXT_WEIGHT)))
    _fit_widths(printed)
    return printed


def _fit_widths(columns: list[PdfColumn]) -> None:
    """Text columns share the width the fixed columns leave (by weight, between the min and max); if the table is
    still wider than the page (many columns), every column shrinks by the same factor."""
    fixed = sum(column.width_mm for column in columns if not column.weight)
    weights = sum(column.weight for column in columns) or 1
    free = PAGE_CONTENT_WIDTH_MM - fixed
    for column in columns:
        if column.weight:
            column.width_mm = max(TEXT_MIN_WIDTH_MM, min(TEXT_MAX_WIDTH_MM, free * column.weight / weights))
    total = sum(column.width_mm for column in columns)
    if total > PAGE_CONTENT_WIDTH_MM:
        for column in columns:
            column.width_mm *= PAGE_CONTENT_WIDTH_MM / total


def pdf_table(table: Table) -> dict:
    """A table ready for the template: headers, cells (text, css) and the widths (table in mm, columns in %)."""
    columns = pdf_columns(table.columns)
    total = sum(column.width_mm for column in columns)
    return {
        "title": table.title,
        "note": table.note,
        "wide": len(columns) > WIDE_TABLE_COLUMNS,
        "width_mm": f"{total:.1f}",
        "headers": [(column.label, column.css, f"{column.width_mm / total * 100:.2f}") for column in columns],
        "rows": [[(column.cell(row), column.css) for column in columns] for row in table.rows],
    }


def report_html(content: ReportContent) -> str:
    sections = []
    for section in content.sections:
        sections.append({
            "key": section.key,
            "label": section.label,
            "tables": [pdf_table(table) for table in section.tables],
            "chart_title": section.chart.title if section.chart else "",
            "chart": mark_safe(bar_chart_svg(section.chart)) if section.chart else "",  # built from escaped text only
            "text": section.text,
            "note": section.note,
        })
    return render_to_string("reports/report_pdf.html", {
        "content": content,
        "start_iso": content.filters.start.isoformat(),
        "end_iso": content.filters.end.isoformat(),
        "no_activities": NO_ACTIVITIES,
        "generated_at": format_value(content.generated_at, "text"),
        "today": long_date(content.today),
        "sections": sections,
    })


def render_report_pdf(content: ReportContent) -> bytes:
    return get_renderer().render(report_html(content))


def individual_evaluation_html(activity, data: dict, *, generated_by: str, generated_at: datetime) -> str:
    """ONE participant's evaluation (ASSUMPTIONS 402, 404) on portrait A4, the same parts as the registration dialog:
    the participant, the average rating, one row per rating question (dots + "4/5") and the text answers in boxes.
    `data` = registrations.services.evaluation_answers (never the email or the contact number)."""
    return render_to_string("reports/individual_evaluation_pdf.html", {
        "activity": activity,
        "dates": format_date_span(activity.start_date, activity.end_date),
        "data": data,
        "registered_as": data["name"] if data["name"].strip() != data["certificate_name"].strip() else "",
        "submitted": format_value(data["submitted_at"], "text"),
        "average": "–" if data["average"] is None else f"{data['average']:.2f}",
        "ratings": [{**row, "dots": [index < (row["value"] or 0) for index in range(row["maximum"])]}
                    for row in data["ratings"]],
        "generated_by": generated_by,
        "generated_at": format_value(generated_at, "text"),
    })


def render_individual_evaluation_pdf(activity, data: dict, *, generated_by: str, generated_at: datetime) -> bytes:
    return get_renderer().render(individual_evaluation_html(activity, data, generated_by=generated_by,
                                                            generated_at=generated_at))
