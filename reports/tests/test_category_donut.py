"""Participants by category on the activity page (ASSUMPTIONS 403): a donut like the dashboard's, whole percentages with
the largest-remainder rule (they add up to 100 %), attended once anybody is marked as attended (else registered, with a
note), categories with 0 in the legend without a slice, numbers in the text and the aria-label. The report section
keeps its 0.1 % table (test_category_by_activity.py)."""

import random
import re

import pytest
from django.urls import reverse

from activities.pages import category_donut
from reports import metrics
from reports.dashboard_page import CATEGORY_COLORS, DONUT_STROKE, donut_segments

pytestmark = pytest.mark.django_db


# --- Whole percentages ---------------------------------------------------------------------------------------------


@pytest.mark.parametrize("counts, expected", [
    ([1, 1, 1], [0.34, 0.33, 0.33]),  # 33 + 33 + 33 would be 99: the first tie gets the missing 1 %
    ([2, 1], [0.67, 0.33]),
    ([92, 100, 63], [0.36, 0.39, 0.25]),
    ([1, 1, 1, 1, 1, 1, 1], [0.15, 0.15, 0.14, 0.14, 0.14, 0.14, 0.14]),
    ([5, 0], [1.0, 0.0]),
    ([0, 0], [None, None]),
])
def test_whole_percent_shares(counts, expected) -> None:
    assert metrics.shares(counts, units=100) == expected


def test_whole_percent_shares_always_add_up_to_100() -> None:
    generator = random.Random(403)
    for _ in range(500):
        counts = [generator.randint(0, 80) for _ in range(generator.randint(1, 9))]
        if sum(counts):
            assert round(sum(metrics.shares(counts, units=100)) * 100) == 100, counts


def test_report_shares_are_still_tenths_of_a_percent() -> None:
    assert metrics.shares([1, 1, 1]) == [0.334, 0.333, 0.333]


# --- The donut data ------------------------------------------------------------------------------------------------


def by_category(rows: list[tuple[str, int, int]]) -> dict:
    """The shape of reports.services.activity_participants_by_category: (category, registered, attended)."""
    return {
        "registered": sum(r[1] for r in rows), "attended": sum(r[2] for r in rows),
        "rows": [{"category": name, "registered": registered, "attended": attended} for name, registered, attended in rows],
    }


def test_attended_basis_with_registered_as_a_second_line() -> None:
    chart = category_donut(by_category([("Faculty", 40, 30), ("Incubatees", 110, 92), ("MSMEs", 120, 100), ("Students", 5, 0)]))
    assert (chart["total"], chart["label"], chart["attended_basis"]) == (222, "attended", True)
    legend = [(row["name"], row["count"], row["percent_text"], row["note"]) for row in chart["legend"]]
    assert legend == [("MSMEs", 100, "45%", "120 registered"), ("Incubatees", 92, "41%", "110 registered"),
                      ("Faculty", 30, "14%", "40 registered"), ("Students", 0, "0%", "5 registered")]
    assert sum(int(row["percent_text"].rstrip("%")) for row in chart["legend"]) == 100
    assert [row["color"] for row in chart["legend"]] == CATEGORY_COLORS[:4]


def test_a_zero_category_is_in_the_legend_but_not_a_slice() -> None:
    chart = category_donut(by_category([("Faculty", 3, 3), ("MSMEs", 2, 0)]))
    assert [row["name"] for row in chart["legend"]] == ["Faculty", "MSMEs"]
    assert len(chart["segments"]) == 1 and chart["segments"][0]["length"] == 100  # one full ring
    assert chart["legend"][1]["percent_text"] == "0%"


def test_before_attendance_the_chart_shows_registrations() -> None:
    chart = category_donut(by_category([("Faculty", 1, 0), ("MSMEs", 2, 0)]))
    assert (chart["total"], chart["label"], chart["attended_basis"]) == (3, "registered", False)
    assert [(row["name"], row["percent_text"], row["note"]) for row in chart["legend"]] == [
        ("MSMEs", "67%", ""), ("Faculty", "33%", "")]


def test_nobody_registered_gives_an_empty_ring_and_dashes() -> None:
    chart = category_donut(by_category([("Faculty", 0, 0), ("MSMEs", 0, 0)]))
    assert (chart["total"], chart["segments"]) == (0, [])
    assert [row["percent_text"] for row in chart["legend"]] == ["–", "–"]


def test_the_aria_label_says_the_numbers() -> None:
    chart = category_donut(by_category([("Incubatees", 110, 92), ("Faculty", 200, 163)]))
    assert chart["aria_label"] == "Participants by category, 255 attended: Faculty 163 (64%), Incubatees 92 (36%)"


def test_shared_segments_match_the_dashboard_rule() -> None:
    assert donut_segments([0, 4])[0] is None
    two = donut_segments([3, 1])
    inset = DONUT_STROKE / 2 + 1.5 / 2
    assert [s["length"] for s in two] == [75 - 2 * inset, 25 - 2 * inset]


# --- The page ------------------------------------------------------------------------------------------------------


def card_of(html: str) -> str:
    start = html.index('id="by-category"')
    return html[start:html.index("</section>", start)]


def test_overview_shows_the_donut_to_any_staff_member(make_activity, register, view_only, login) -> None:
    activity = make_activity("Bootcamp", categories=("Faculty", "Incubatees", "MSMEs"))
    register(activity, "A", category="Faculty", attended=True)
    register(activity, "B", category="Faculty", attended=True)
    register(activity, "C", category="Incubatees", attended=True)
    register(activity, "D", category="Incubatees")
    html = login(view_only).get(reverse("activities:detail", args=[activity.pk])).content.decode()
    card = card_of(html)
    assert "Participants by category" in card and "<svg" in card and 'id="by-category-table"' not in html
    text = re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", card))
    assert "Faculty (67%) 2 registered 2" in text and "Incubatees (33%) 2 registered 1" in text
    assert "MSMEs (0%) 0 registered 0" in text and "3 attended" in text
    assert 'role="img" aria-label="Participants by category, 3 attended: Faculty 2 (67%), Incubatees 1 (33%), MSMEs 0 (0%)"' in card
    assert card.count("stroke-dasharray") == 2  # MSMEs has no slice
    assert "<caption" in card  # the legend is a real text table


def test_overview_before_attendance_says_registered(make_activity, register, director, login) -> None:
    activity = make_activity("Soon", categories=("Faculty",))
    register(activity, "A")
    card = card_of(login(director).get(reverse("activities:detail", args=[activity.pk])).content.decode())
    assert "registered</span>" in card
    assert "Nobody is marked as attended yet; the chart shows registrations." in card


def test_dashboard_legend_keeps_its_numbers() -> None:
    from django.template.loader import render_to_string

    from reports.dashboard_page import _categories

    chart = _categories([{"name": "Faculty", "attended": 3}, {"name": "MSMEs", "attended": 1}])
    text = re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", render_to_string("components/donut.html", {"chart": chart})))
    assert "Faculty (75%) 3" in text and "MSMEs (25%) 1" in text and "4 attended" in text
    assert chart["aria_label"] == "Attendees by category, 4 attended: Faculty 3 (75%), MSMEs 1 (25%)"
