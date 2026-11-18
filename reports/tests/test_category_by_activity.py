"""Participants by category per activity (ASSUMPTIONS 400): counts and percentages from
reports.metrics.participants_by_category, blank-safe, rounded so each activity adds up to 100.0 %; shown on the
activity page (Overview) and in the Accomplishment and Participation reports (PDF and Excel)."""

import random
from datetime import date

import pytest
from django.db import connection
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from openpyxl import load_workbook
from openpyxl.utils import range_boundaries

from activities.models import ParticipantCategory
from reports import metrics, services
from reports.content import REPORT_TYPES

from .conftest import TEST_NOW

pytestmark = pytest.mark.django_db


@pytest.fixture
def queued(monkeypatch) -> list[int]:
    calls: list[int] = []
    monkeypatch.setattr(services, "queue_build", calls.append)
    return calls


# --- Rounding -------------------------------------------------------------------------------------------------------


@pytest.mark.parametrize("counts, expected", [
    ([1, 1, 1], [0.334, 0.333, 0.333]),  # 33.3 + 33.3 + 33.3 would be 99.9: the first tie gets the missing 0.1
    ([2, 1], [0.667, 0.333]),
    ([1, 2], [0.333, 0.667]),
    ([5, 0], [1.0, 0.0]),
    ([3], [1.0]),
    ([1, 1, 1, 1, 1, 1, 1], [0.143, 0.143, 0.143, 0.143, 0.143, 0.143, 0.142]),
    ([0, 0], [None, None]),  # nothing to divide by: blank, never 0 % or an error
    ([], []),
])
def test_shares(counts, expected) -> None:
    assert metrics.shares(counts) == expected


def test_shares_always_add_up_to_100_percent() -> None:
    generator = random.Random(400)
    for _ in range(500):
        counts = [generator.randint(0, 50) for _ in range(generator.randint(1, 8))]
        if sum(counts):
            assert round(sum(metrics.shares(counts)) * 1000) == 1000, counts


# --- Counting -------------------------------------------------------------------------------------------------------


def test_counts_per_category_with_zero_rows_and_blank_shares(make_activity, register) -> None:
    activity = make_activity("Bootcamp", categories=("Faculty", "Incubatees", "MSMEs"))
    for name in ("A", "B"):
        register(activity, name, category="Faculty", attended=True)
    register(activity, "C", category="Incubatees", attended=True)
    register(activity, "D", category="Incubatees")
    result = metrics.participants_by_category([activity.pk])[activity.pk]
    assert (result["registered"], result["attended"]) == (4, 3)
    rows = {row["category"]: row for row in result["rows"]}
    assert list(rows) == ["Faculty", "Incubatees", "MSMEs"]  # by name; MSMEs listed with zero
    assert (rows["Faculty"]["registered"], rows["Faculty"]["registered_share"]) == (2, 0.5)
    assert (rows["Faculty"]["attended"], rows["Faculty"]["attended_share"]) == (2, 0.667)
    assert (rows["Incubatees"]["attended"], rows["Incubatees"]["attended_share"]) == (1, 0.333)
    assert (rows["MSMEs"]["registered"], rows["MSMEs"]["registered_share"]) == (0, 0.0)


def test_nobody_registered_or_attended_gives_blank_shares(make_activity, register) -> None:
    empty = make_activity("Nobody yet", categories=("Faculty", "MSMEs"))
    registered_only = make_activity("Not marked yet", categories=("Faculty",))
    register(registered_only, "A")
    result = metrics.participants_by_category([empty.pk, registered_only.pk])
    assert [(r["registered_share"], r["attended_share"]) for r in result[empty.pk]["rows"]] == [(None, None), (None, None)]
    assert [(r["registered_share"], r["attended_share"]) for r in result[registered_only.pk]["rows"]] == [(1.0, None)]


def test_a_category_removed_from_the_activity_still_shows_its_registrations(make_activity, register) -> None:
    activity = make_activity(categories=("Faculty", "MSMEs"))
    register(activity, "A", category="MSMEs")
    activity.participant_categories.set(ParticipantCategory.objects.filter(name="Faculty"))
    rows = metrics.participants_by_category([activity.pk])[activity.pk]["rows"]
    assert [(r["category"], r["registered"]) for r in rows] == [("Faculty", 0), ("MSMEs", 1)]


def test_query_count_does_not_grow_with_the_number_of_activities(make_activity, register) -> None:
    def count(ids):
        with CaptureQueriesContext(connection) as queries:
            metrics.participants_by_category(ids)
        return len(queries)

    first = make_activity(categories=("Faculty",))
    register(first, "A")
    few = count([first.pk])
    others = [make_activity(categories=("Faculty", "MSMEs")) for _ in range(5)]
    for activity in others:
        register(activity, "B", category="MSMEs")
    assert count([first.pk] + [a.pk for a in others]) == few == 3


# --- The activity page ----------------------------------------------------------------------------------------------


def test_overview_shows_the_table_to_any_staff_member(make_activity, register, view_only, login) -> None:
    activity = make_activity("Bootcamp", categories=("Faculty", "Incubatees"))
    register(activity, "A", category="Faculty", attended=True)
    register(activity, "B", category="Faculty")
    register(activity, "C", category="Incubatees")
    html = login(view_only).get(reverse("activities:detail", args=[activity.pk])).content.decode()
    table = html[html.index('id="by-category-table"'):html.index("</table>", html.index('id="by-category-table"'))]
    assert "Participants by category" in html
    assert "66.7%" in table and "33.3%" in table and "100.0%" in table
    assert "Faculty" in table and "Incubatees" in table


def test_overview_before_attendance_shows_dashes(make_activity, register, director, login) -> None:
    activity = make_activity("Soon", categories=("Faculty",))
    register(activity, "A")
    html = login(director).get(reverse("activities:detail", args=[activity.pk])).content.decode()
    assert "nobody is marked as attended yet" in html


# --- The reports ----------------------------------------------------------------------------------------------------


def test_main_reports_include_the_section_by_default() -> None:
    assert "category_by_activity" in REPORT_TYPES["accomplishment"].default_sections
    assert "category_by_activity" in REPORT_TYPES["participation"].default_sections
    assert "category_by_activity" not in REPORT_TYPES["evaluation"].default_sections


@pytest.fixture
def period_data(make_activity, register):
    first = make_activity("=HYPERLINK(\"http://x\")", start=date(2026, 3, 10), categories=("Faculty", "MSMEs"))
    register(first, "A", category="Faculty", attended=True)
    register(first, "B", category="Faculty", attended=True)
    register(first, "C", category="MSMEs")
    second = make_activity("Pitch Day", start=date(2026, 5, 4), categories=("Incubatees",))
    register(second, "D", category="Incubatees")
    return first, second


def build(director, fmt: str, report_type: str = "participation"):
    report = services.request_report(director, {"report_type": report_type, "format": fmt, "date_range_start": "2026-01-01",
                                                "date_range_end": "2026-12-31"}, now=TEST_NOW)
    services.build_report(report.pk, now=TEST_NOW)
    report.refresh_from_db()
    return report


def test_excel_sheet_is_a_real_table_with_percent_numbers(director, queued, period_data) -> None:
    workbook = load_workbook(build(director, "excel").file_path.path)
    sheet = workbook["Category per activity"]
    table = next(iter(sheet.tables.values()))
    min_col, min_row, max_col, max_row = range_boundaries(table.ref)
    header = [sheet.cell(row=min_row, column=c).value for c in range(min_col, max_col + 1)]
    assert header == ["Activity", "Start", "End", "Participant category", "Registered", "% of registered", "Attended",
                      "% of attended"]
    rows = [[sheet.cell(row=r, column=c) for c in range(min_col, max_col + 1)] for r in range(min_row + 1, max_row + 1)]
    values = [[cell.value for cell in row] for row in rows]
    assert [row[3:] for row in values] == [
        ["Faculty", 2, 0.667, 2, 1.0], ["MSMEs", 1, 0.333, 0, 0.0], ["Incubatees", 1, 1.0, 0, None],
    ]
    assert rows[0][5].number_format == "0.0%" and rows[0][4].number_format == "#,##0"
    # A formula-like activity title stays text (Excel safety rule).
    assert rows[0][0].data_type == "s" and rows[0][0].value.startswith("=HYPERLINK")
    assert "Percentages are of the activity's registered" in " ".join(
        str(sheet.cell(row=r, column=1).value or "") for r in range(max_row + 1, sheet.max_row + 1))


def test_pdf_shows_the_section_with_percentages(director, queued, period_data, fake_pdf_renderer) -> None:
    build(director, "pdf", report_type="accomplishment")
    text = fake_pdf_renderer.last_text()
    assert "Participants by category per activity" in text
    assert "66.7%" in text and "33.3%" in text and "100.0%" in text
    assert "Pitch Day" in text and "add up to 100%" in text
