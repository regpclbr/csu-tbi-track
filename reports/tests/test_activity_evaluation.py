"""One activity's evaluation results (/reports/api/activities/<id>/evaluation/): the same rules as the
reports (reports/metrics.py), only rating questions, never comments or text answers."""

from datetime import date

import pytest

from activities.models import ActivityStatus
from reports import metrics
from reports.metrics import ReportFilters

pytestmark = pytest.mark.django_db


def url(activity) -> str:
    return f"/reports/api/activities/{activity.pk}/evaluation/"


def test_counts_and_rating_averages_for_one_activity(make_activity, register, evaluate, login, admin_staff) -> None:
    activity, other = make_activity("Pitching"), make_activity("Other")
    evaluate(register(activity, "Ana", attended=True), {"Overall rating": 5, "Venue": 4},
             comments="Secret comment", text_answers={"Suggestions": "More coffee"})
    evaluate(register(activity, "Ben", attended=True), {" overall RATING ": 4, "Venue": 2})
    register(activity, "Cara", attended=True)  # attended, not evaluated
    register(activity, "Dan")  # registered only
    evaluate(register(other, "Eve", attended=True), {"Overall rating": 1})  # another activity: never counted

    response = login(admin_staff).get(url(activity))  # any active staff member
    assert response.status_code == 200
    data = response.json()
    assert {k: data[k] for k in ("activity_id", "registered", "attended", "evaluations", "evaluation_response_rate")} == {
        "activity_id": activity.pk, "registered": 4, "attended": 3, "evaluations": 2, "evaluation_response_rate": 0.6667,
    }
    assert data["questions"] == [
        {"question": "Overall rating", "average": 4.5, "answers": 2},  # labels grouped ignoring case and spaces
        {"question": "Venue", "average": 3.0, "answers": 2},
    ]
    assert (data["average_rating"], data["rating_answers"]) == (3.75, 4)
    text = response.content.decode()
    assert "Secret comment" not in text and "More coffee" not in text and "Suggestions" not in text
    assert "Ana" not in text  # no names either


def test_same_numbers_as_the_reports(make_activity, register, evaluate) -> None:
    activity = make_activity("Only one", start=date(2026, 3, 10))
    evaluate(register(activity, "Ana", attended=True), {"Overall rating": 5, "Venue": 3})
    evaluate(register(activity, "Ben", attended=True), {"Overall rating": 2})
    period = metrics.evaluation_results(ReportFilters(date(2026, 3, 1), date(2026, 3, 31)))
    own = metrics.activity_evaluation_results(activity)
    assert own["questions"] == period["questions"]
    assert (own["average_rating"], own["rating_answers"]) == (period["average_rating"], period["rating_answers"])


def test_any_status_and_an_activity_without_evaluations(make_activity, register, evaluate, login, director) -> None:
    ongoing = make_activity(status=ActivityStatus.ONGOING)  # evaluations open on the end date
    evaluate(register(ongoing, "Ana", attended=True), {"Overall rating": 4})
    client = login(director)
    assert client.get(url(ongoing)).json()["questions"] == [{"question": "Overall rating", "average": 4.0, "answers": 1}]
    empty = client.get(url(make_activity())).json()
    assert empty["questions"] == [] and empty["average_rating"] is None and empty["evaluation_response_rate"] is None


def test_needs_a_staff_login(client, make_activity) -> None:
    activity = make_activity()
    assert client.get(url(activity)).status_code == 302
    assert client.get("/reports/api/activities/999999/evaluation/").status_code == 302
