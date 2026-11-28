"""The participation and approval record tools (ASSUMPTIONS 439) and their routing: numbers from the existing services
and reports.metrics only, never names; what waits for the member who asks. Fake Gemini only."""

import json
from datetime import date, datetime, time

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile
from django.utils import timezone

from activities.models import ActivityParticipantCategory, ActivityStatus, ParticipantCategory
from approvals import assignments
from approvals import services as approval_services
from assistant import routing, services, tools
from assistant.testing import FakeGemini
from registrations.models import (AttendanceDay, EvaluationSubmission, FieldType, FormAnswer, FormField, FormTemplate,
                                  FormType, Registration)

from .conftest import text_pdf

pytestmark = pytest.mark.django_db
NOW = timezone.make_aware(datetime(2026, 11, 16, 10, 0))
say, call = FakeGemini.say, FakeGemini.call
NAMES = ("Ana Cruz", "Ben Reyes", "Carla Santos", "Dino Lim", "Ella Tan")


def _form(activity, form_type: str) -> FormTemplate:
    form, _ = FormTemplate.objects.get_or_create(activity=activity, form_type=form_type,
                                                 defaults={"title": form_type, "created_by": activity.created_by})
    return form


def _register(activity, name, category="Incubatees", *, days=(), source=Registration.Source.ONLINE, cancelled=False):
    registration = Registration.objects.create(
        activity=activity, template=_form(activity, FormType.REGISTRATION),
        participant_category=ParticipantCategory.objects.get(name=category), name=name,
        email=f"{name.split()[0].lower()}@example.com", registration_source=source, attendance_confirmed=bool(days),
        cancelled_at=NOW if cancelled else None)
    for day in days:
        AttendanceDay.objects.create(registration=registration, day=day, checked_in_at=NOW, created_at=NOW,
                                     method=AttendanceDay.Method.QR)
    return registration


@pytest.fixture
def people(activity):
    """Pitching Workshop (Nov 10-11): Ana both days, Ben day 1, Carla cancelled, Dino cancelled but came on day 2,
    Ella a walk-in on day 2 (Faculty)."""
    ActivityParticipantCategory.objects.get_or_create(activity=activity,
                                                      participant_category=ParticipantCategory.objects.get(name="Faculty"))
    d1, d2 = date(2026, 11, 10), date(2026, 11, 11)
    return {
        "ana": _register(activity, "Ana Cruz", days=(d1, d2)),
        "ben": _register(activity, "Ben Reyes", days=(d1,)),
        "carla": _register(activity, "Carla Santos", cancelled=True),
        "dino": _register(activity, "Dino Lim", cancelled=True, days=(d2,)),
        "ella": _register(activity, "Ella Tan", "Faculty", days=(d2,), source=Registration.Source.WALK_IN),
    }


def no_names(result: dict) -> None:
    text = json.dumps(result, default=str)
    assert not any(name in text for name in NAMES) and "@example.com" not in text


def test_attendance_by_day(activity, people) -> None:
    result = tools.get_attendance_by_day({"activity_id": activity.pk})
    assert [(d["day_number"], d["date"], d["attended"], d["by_qr_check_in"]) for d in result["days"]] == [
        (1, "2026-11-10", 2, 2), (2, "2026-11-11", 3, 3)]
    assert result["attended_at_least_one_day"] == 4
    no_names(result)


def test_registration_counts_follow_the_cancelled_rule(activity, people) -> None:
    result = tools.get_registration_counts({"activity_id": activity.pk})
    # Registered: Ana, Ben, Dino (cancelled but came), Ella (walk-in); Carla is cancelled and not counted.
    assert (result["registered"], result["cancelled_not_counted"], result["walk_ins"], result["attended"]) == (4, 1, 1, 4)
    assert (result["will_attend"], result["will_not_attend"]) == (2, 2)  # pre-registrations only, as signified
    assert result["attendance_rate"] == "100.0%" and result["max_slots"] == 30
    no_names(result)


def test_participant_mix(activity, people) -> None:
    result = tools.get_participant_mix({"activity_id": activity.pk})
    groups = {row["group"]: (row["attended"], row["attended_share"]) for row in result["groups"]}
    assert groups["Faculty"] == (1, "25.0%") and groups["Incubatees"] == (3, "75.0%")
    assert result["basis"] == "attended" and result["guideline_checked"] is True
    # ASSUMPTIONS 424 (confirmed 2026-11-28): Incubatees at most 70 %
    assert result["within_guideline"] is False and result["problems"] == ["Incubatees 75% (at most 70%)"]
    no_names(result)


def test_evaluation_results_are_numbers_only(activity, people) -> None:
    form = _form(activity, FormType.EVALUATION)
    field = FormField.objects.create(template=form, field_label="Overall rating", field_type=FieldType.RATING, sort_order=0)
    for registration, value in ((people["ana"], 5), (people["ben"], 4)):
        submission = EvaluationSubmission.objects.create(template=form, registration=registration, comments="Ana loved it",
                                                         certificate_name=registration.name, certificate_email=registration.email)
        FormAnswer.objects.create(field=field, answer_source=FormAnswer.AnswerSource.EVALUATION,
                                  evaluation_submission=submission, answer_value=str(value))
    result = tools.get_evaluation_results({"activity_id": activity.pk})
    assert (result["evaluations"], result["attended"], result["response_rate"]) == (2, 4, "50.0%")
    [question] = result["rating_questions"]
    assert question == {"question": "Overall rating", "answers": 2, "mean": "4.50", "counts": {"5": 1, "4": 1, "3": 0, "2": 0, "1": 0}}
    assert "loved" not in json.dumps(result)  # never the written comments
    no_names(result)


def test_pending_approvals_are_the_askers_own(activity, director, head) -> None:
    submission = approval_services.submit(head, SimpleUploadedFile("l.pdf", text_pdf("Letter")), "Venue request", "", activity)
    assignments.assign_work(director, "activity", head, activity=activity, due_date=date(2030, 1, 1))
    mine = tools.get_pending_approvals({}, director)
    assert [item["title"] for item in mine["waiting_for_your_approval"]] == ["Venue request"] and mine["waiting_total"] == 1
    theirs = tools.get_pending_approvals({}, head)
    assert theirs["waiting_for_your_approval"] == []  # never your own submission
    assert [(item["work"], item["title"]) for item in theirs["assigned_to_you"]] == [("Work on the activity", activity.title)]
    assert submission.pk


def test_unknown_activity_goes_back_to_the_model() -> None:
    with pytest.raises(tools.ToolError):
        tools.get_attendance_by_day({"activity_id": 999999})


# --- Routing ------------------------------------------------------------------------------------------------------

PARTICIPATION_QUESTIONS = [
    "How many attended the Pitching Workshop on day 2?",
    "What were the evaluation results of the Pitching Workshop?",
    "Did the Pitching Workshop meet the participant mix guideline?",
    "How many walk-ins did the Pitching Workshop have?",
    "How many registered for the Pitching Workshop?",
    "how many cancelled the pitching workshop",
    "What is the response rate of the Pitching Workshop evaluation?",
    "How many walk-ins did we have?",
]


@pytest.mark.parametrize("question", PARTICIPATION_QUESTIONS)
def test_participation_questions(question, activity) -> None:
    decided = routing.route(question, NOW)
    assert decided.path == routing.PARTICIPATION


@pytest.mark.parametrize("question, path", [
    ("What does the evaluation form say about the venue?", routing.DOCUMENT_CONTENT),  # "say": the files
    ("Download the attendance sheet of the Pitching Workshop", routing.FIND_FILES),  # a document type
    ("How many attended in November?", routing.GEMINI_ALL_TOOLS),  # a period: the period tools
    ("How many activities were attended by Faculty this month?", routing.ACTIVITIES),
    ("Which submissions are waiting for my approval?", routing.DOCUMENT_STATUS),
])
def test_not_participation(question, path, activity) -> None:
    assert routing.route(question, NOW).path == path


def test_the_participation_path_offers_only_those_tools(director, activity, people) -> None:
    chat = services.create_chat(director)
    fake = services.get_gateway()
    fake.replies += [call(tools.REGISTRATIONS_TOOL, activity_id=activity.pk), say("4 registered, 1 cancelled.")]
    answer = services.ask(director, chat, "How many registered for the Pitching Workshop?", now=NOW)
    offered = {spec.name for spec in fake.generate_calls[0]["tools"]}
    assert offered == {"find_activities", tools.ATTENDANCE_TOOL, tools.REGISTRATIONS_TOOL, tools.MIX_TOOL, tools.EVALUATION_TOOL}
    assert answer.answered_from == services.RECORDS and answer.message.content.endswith(services.RECORDS_NOTE)
    assert fake.tool_results()[0][1]["registered"] == 4


def test_pending_approvals_tool_gets_the_member(director, head, activity) -> None:
    approval_services.submit(head, SimpleUploadedFile("l.pdf", text_pdf("Letter")), "Venue request", "", activity)
    chat = services.create_chat(director)
    fake = services.get_gateway()
    fake.replies += [call(tools.APPROVALS_TOOL), say("One submission waits.")]
    services.ask(director, chat, "Which submissions are waiting for my approval?", now=NOW)
    assert fake.tool_results()[0][1]["waiting_total"] == 1
