"""The PDF version of a report. Rendered through certificates.pdf.get_renderer() (the one WeasyPrint
injection point; tests use FakeRenderer, and WeasyPrint may only fetch data: URIs).

Charts are simple horizontal bar charts drawn as inline SVG: one series, the value written at the
end of each bar, and the same numbers in the table next to them.
"""

from datetime import date, datetime
from html import escape

from django.template.loader import render_to_string
from django.utils import timezone
from django.utils.safestring import mark_safe

from certificates.pdf import get_renderer

from .content import DATE, DECIMAL, INT, NO_ACTIVITIES, PERCENT, Cell, Chart, ReportContent, long_date

EMPTY = "—"

# Chart look (reference palette: series slot 1, text inks, recessive baseline).
BAR_COLOR = "#2a78d6"
TEXT_PRIMARY = "#0b0b0b"
TEXT_SECONDARY = "#52514e"
BASELINE = "#c9c8c3"
LABEL_WIDTH, BAR_AREA, VALUE_WIDTH, ROW_HEIGHT, BAR_HEIGHT = 190, 380, 60, 22, 14
MAX_LABEL_CHARS = 30


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


def _is_number(value, kind: str) -> bool:
    if isinstance(value, Cell):
        kind = value.kind
    return kind in (INT, PERCENT, DECIMAL, DATE)


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


def report_html(content: ReportContent) -> str:
    sections = []
    for section in content.sections:
        tables = [
            {
                "title": table.title,
                "note": table.note,
                "headers": [(column.label, column.kind != "text") for column in table.columns],
                "rows": [
                    [(format_value(value, column.kind), _is_number(value, column.kind)) for value, column in zip(row, table.columns)]
                    for row in table.rows
                ],
            }
            for table in section.tables
        ]
        sections.append({
            "key": section.key,
            "label": section.label,
            "tables": tables,
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
