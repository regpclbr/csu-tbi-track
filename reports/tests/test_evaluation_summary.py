"""Evaluation summary of one activity (ASSUMPTIONS 401): responses vs attendees, each rating question's frequency per
rating with percentages (adding up to 100.0 %) and the mean, the text answers WITHOUT names; PDF (fake renderer) and
Excel (formula-safe), made on request for any active staff member. No interpretation scale (owner decision)."""

from datetime import date
from io import BytesIO

import pytest
from django.test import Client
from django.urls import reverse
from openpyxl import load_workbook
from openpyxl.utils import range_boundaries

from reports import metrics, services

pytestmark = pytest.mark.django_db


@pytest.fixture
def evaluated(make_activity, register, evaluate):
    """5 attendees (+1 registered only), 4 evaluations. Overall: 5, 5, 4, 3. Venue: 4, 4, 2 (one did not rate it)."""
    activity = make_activity("Design Thinking Bootcamp", start=date(2026, 3, 10), categories=("Faculty", "MSMEs"))
    people = [register(activity, name, attended=True) for name in ("Ana Cruz", "Ben Reyes", "Cara Lim", "Dan Uy", "Eli Tan")]
    register(activity, "Fe Go")
    evaluate(people[0], {"Overall rating": 5, "Venue rating": 4}, comments="Great speakers",
             text_answers={"Key learnings": "pitching basics"})
    evaluate(people[1], {"Overall rating": 5, "Venue rating": 4}, comments="   ",
             text_answers={"Key learnings": "=HYPERLINK(\"http://x\",\"click\")"})
    evaluate(people[2], {"Overall rating": 4, "Venue rating": 2}, text_answers={"Key learnings": "   "})
    evaluate(people[3], {"Overall rating": 3}, comments="Aircon was cold", text_answers={"Key learnings": "Business model canvas"})
    return activity


def test_numbers(evaluated) -> None:
    data = metrics.activity_evaluation_summary(evaluated)
    assert (data["registered"], data["attended"], data["evaluations"], data["evaluation_response_rate"]) == (6, 5, 4, 0.8)
    overall, venue = data["ratings"]
    assert (overall["question"], overall["answers"], overall["mean"]) == ("Overall rating", 4, 4.25)
    assert [(f["rating"], f["count"], f["share"]) for f in overall["frequency"]] == [
        (5, 2, 0.5), (4, 1, 0.25), (3, 1, 0.25), (2, 0, 0.0), (1, 0, 0.0)]
    assert (venue["answers"], venue["mean"]) == (3, 3.33)
    assert [f["share"] for f in venue["frequency"]] == [0.0, 0.667, 0.0, 0.333, 0.0]  # adds up to 100.0 %


def test_text_answers_have_no_names_are_sorted_and_skip_empty_ones(evaluated) -> None:
    data = metrics.activity_evaluation_summary(evaluated)
    assert data["text_answers"] == [{"question": "Key learnings", "answers": [
        "=HYPERLINK(\"http://x\",\"click\")", "Business model canvas", "pitching basics"]}]  # A-Z, ignoring case
    assert data["comments"] == ["Aircon was cold", "Great speakers"]
    flat = repr(data)
    for private in ("Ana Cruz", "Ben Reyes", "Cara Lim", "Dan Uy", "@example.com"):
        assert private not in flat


def test_no_evaluations_yet(make_activity, register) -> None:
    activity = make_activity("Quiet one")
    register(activity, "A", attended=True)
    data = metrics.activity_evaluation_summary(activity)
    assert (data["evaluations"], data["evaluation_response_rate"], data["ratings"], data["comments"]) == (0, 0.0, [], [])


def test_nobody_attended_gives_a_blank_rate(make_activity) -> None:
    data = metrics.activity_evaluation_summary(make_activity("Empty"))
    assert data["evaluation_response_rate"] is None


def test_a_rating_question_nobody_answered_has_no_mean_and_blank_percentages(make_activity, register, evaluate) -> None:
    from registrations.models import FieldType, FormField

    activity = make_activity("One question unanswered")
    evaluate(register(activity, "A", attended=True), {"Overall rating": 4})
    FormField.objects.create(template=activity.form_templates.get(form_type="evaluation"), field_label="Food rating",
                             field_type=FieldType.RATING, sort_order=9)
    food = metrics.activity_evaluation_summary(activity)["ratings"][1]
    assert (food["question"], food["answers"], food["mean"]) == ("Food rating", 0, None)
    assert [f["share"] for f in food["frequency"]] == [None] * 5


# --- The files ------------------------------------------------------------------------------------------------------


def test_pdf(evaluated, director, fake_pdf_renderer) -> None:
    file = services.evaluation_summary_file(director, evaluated, "pdf")
    assert file.name == f"evaluation-summary-activity-{evaluated.pk}.pdf" and file.content_type == "application/pdf"
    text = fake_pdf_renderer.last_text()
    assert "Evaluation Summary - Design Thinking Bootcamp" in text
    assert "4 of 5 attendees submitted the evaluation." in text and "80.0%" in text
    assert "4.25" in text and "3.33" in text and "66.7%" in text and "33.3%" in text
    assert "Great speakers" in text and "Business model canvas" in text
    assert "Interpretation" not in text  # mean only until the TBI gives its scale
    for private in ("Ana Cruz", "Ben Reyes", "Cara Lim", "Dan Uy", "@example.com"):
        assert private not in text
    assert "Only Conducted activities whose end date" not in text  # the activity's own header lines instead


def test_excel(evaluated, director) -> None:
    file = services.evaluation_summary_file(director, evaluated, "excel")
    assert file.name.endswith(".xlsx")
    workbook = load_workbook(BytesIO(file.data))
    assert workbook.sheetnames == ["Report info", "Responses", "Rating questions", "Text answers"]
    info = {row[0].value: row[1].value for row in workbook["Report info"].iter_rows(min_row=7)}
    assert info["Activity"] == "Design Thinking Bootcamp" and "Counting rule" not in info
    ratings = workbook["Rating questions"]
    tables = {}
    for table in ratings.tables.values():
        min_col, min_row, max_col, max_row = range_boundaries(table.ref)
        title = ratings.cell(row=min_row - 1, column=1).value
        tables[title] = [[ratings.cell(row=r, column=c) for c in range(min_col, max_col + 1)] for r in range(min_row, max_row + 1)]
    assert [c.value for c in tables["Overview"][0]] == ["Rating question", "Answers", "Mean (1-5)"]
    overall = tables["Overall rating"]
    assert [[c.value for c in row] for row in overall[1:]] == [["5", 2, 0.5], ["4", 1, 0.25], ["3", 1, 0.25], ["2", 0, 0], ["1", 0, 0]]
    assert overall[1][2].number_format == "0.0%"
    texts = workbook["Text answers"]
    cells = [cell for row in texts.iter_rows() for cell in row if isinstance(cell.value, str) and cell.value.startswith("=HYPERLINK")]
    assert cells and all(cell.data_type == "s" for cell in cells)  # shown as typed, never a formula
    for sheet in workbook.worksheets:
        for row in sheet.iter_rows():
            for cell in row:
                assert "Ana Cruz" not in str(cell.value or "") and "@example.com" not in str(cell.value or "")


def test_no_evaluations_says_so(make_activity, register, director, fake_pdf_renderer) -> None:
    activity = make_activity("Quiet one")
    register(activity, "A", attended=True)
    services.evaluation_summary_file(director, activity, "pdf")
    assert "No evaluations submitted yet." in fake_pdf_renderer.last_text()
    workbook = load_workbook(BytesIO(services.evaluation_summary_file(director, activity, "excel").data))
    assert workbook.sheetnames == ["Report info", "Responses", "Rating questions"]


# --- Who may download, and the button ------------------------------------------------------------------------------


def url(activity, file_format="pdf") -> str:
    return reverse("reports:evaluation_summary", args=[activity.pk, file_format])


@pytest.mark.parametrize("role", ["view_only", "System Administrator", "Director"])
def test_any_active_staff_member_downloads_it(evaluated, role, view_only, director, make_member, login, fake_pdf_renderer) -> None:
    member = {"view_only": view_only, "Director": director}.get(role) or make_member(role)
    response = login(member).get(url(evaluated))
    assert response.status_code == 200
    assert response["Content-Disposition"] == f'attachment; filename="evaluation-summary-activity-{evaluated.pk}.pdf"'
    assert response["Cache-Control"] == "private, no-store" and response["X-Content-Type-Options"] == "nosniff"
    excel = login(member).get(url(evaluated, "excel"))
    assert excel.status_code == 200 and excel["Content-Type"].startswith("application/vnd.openxmlformats")


def test_never_public(evaluated) -> None:
    response = Client().get(url(evaluated))
    assert response.status_code == 302 and reverse("accounts:login") in response["Location"]


def test_unknown_format_or_activity_is_404(evaluated, director, login) -> None:
    assert login(director).get(url(evaluated, "docx")).status_code == 404
    assert login(director).get(reverse("reports:evaluation_summary", args=[999999, "pdf"])).status_code == 404


def test_the_evaluation_tab_has_both_buttons(evaluated, view_only, login) -> None:
    html = login(view_only).get(reverse("activities:detail", args=[evaluated.pk]), {"tab": "evaluation"}).content.decode()
    assert f'href="{url(evaluated)}"' in html and f'href="{url(evaluated, "excel")}"' in html
    assert "Evaluation summary (PDF)" in html and "Evaluation summary (Excel)" in html
