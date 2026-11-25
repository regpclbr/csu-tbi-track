"""The period Evaluation Summary's numbers (ASSUMPTIONS 445), from reports.metrics only."""

from datetime import date

import pytest

from reports import metrics

from .conftest import YEAR_2026

pytestmark = pytest.mark.django_db


def test_rating_frequency_groups_labels_and_counts_each_rating(make_activity, register, evaluate) -> None:
    first, second = make_activity("A", start=date(2026, 3, 1)), make_activity("B", start=date(2026, 4, 1))
    evaluate(register(first, "Ana", attended=True), {"Overall rating": 5})
    evaluate(register(first, "Ben", attended=True), {"Overall rating": 4})
    evaluate(register(second, "Cy", attended=True), {" overall Rating ": 4})
    [question] = metrics.period_rating_frequency(YEAR_2026)
    assert question["question"] == "Overall rating" and question["answers"] == 3 and question["mean"] == 4.33
    assert [f["count"] for f in question["frequency"]] == [1, 2, 0, 0, 0]
    assert round(sum(f["share"] for f in question["frequency"]), 3) == 1.0


def test_text_answers_have_no_names_and_are_sorted(make_activity, register, evaluate) -> None:
    activity = make_activity("A", start=date(2026, 3, 1))
    evaluate(register(activity, "Zed Santos", attended=True), {"Overall rating": 5}, comments="more snacks",
             text_answers={"What did you learn?": "Pitching"})
    evaluate(register(activity, "Ana Cruz", attended=True), {"Overall rating": 5}, comments="Great venue")
    texts = metrics.period_text_answers(YEAR_2026)
    assert texts == ["Great venue", "more snacks", "Pitching"]
    assert not any("Santos" in text or "Cruz" in text for text in texts)
