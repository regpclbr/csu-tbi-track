"""Which path answers a question (ASSUMPTIONS 381, 383): decided by our code, tested with the fake Gemini only."""

import logging
from datetime import date, datetime, time

import pytest
from django.utils import timezone

from activities.models import ActivityStatus
from assistant import routing, services, tools
from assistant.models import AIChat
from assistant.testing import FakeGemini

say, call = FakeGemini.say, FakeGemini.call
NOW = timezone.make_aware(datetime(2026, 11, 16, 10, 0))  # Monday, Manila
pytestmark = pytest.mark.django_db

C, O, S, D = ActivityStatus.CONDUCTED, ActivityStatus.ONGOING, ActivityStatus.SCHEDULED, ActivityStatus.DRAFT


@pytest.fixture
def calendar_2026(make_event):
    """The owner's example (November 2026) plus a few others."""
    return {
        "bootcamp": make_event("Pre-incubation / Ideation Bootcamp", date(2026, 11, 3), date(2026, 11, 4), C, "Seminar", (time(8), time(11))),
        "market": make_event("Market Research Training", date(2026, 11, 6), status=C, type_name="Training"),
        "pitching": make_event("ITE 16 Pitching Competition", date(2026, 11, 13), status=O, type_name="Training"),
        "test": make_event("Test", date(2026, 11, 14), status=C, type_name="Mentoring", times=(time(17, 44), time(18))),
        "networking": make_event("Investor Networking", date(2026, 11, 17), status=S, type_name="Networking"),
        "draft": make_event("Draft Workshop Idea", date(2026, 11, 25), status=D),
        "forum": make_event("Technology Forum", date(2026, 12, 2), status=D, type_name="Forum"),
        "business": make_event("Business Model Training", date(2026, 10, 2), date(2026, 10, 3), C, "Training"),
    }


@pytest.fixture
def chat(director) -> AIChat:
    return services.create_chat(director)


ACTIVITY_QUESTIONS = [
    "What are the activities for the month of November?",
    "what are the activities for the month of november?",
    "activities this month",
    "What's happening this week?",
    "events next week",
    "upcoming activities",
    "what's happening now",
    "activities conducted in October",
    "how many trainings this year",
    "List the seminars in November",
    "Which activities are scheduled for December?",
    "Show me the schedule for next month",
    "Are there any workshops tomorrow?",
    "How many activities did we have last month?",
    "what activities are ongoing?",
    "Show the draft activities",
    "number of events in 2026",
]
FILE_QUESTIONS = [
    "can you give me the files for november's activity",
    "give me the files for November's activities",
    "attendance sheet of the Ideation Bootcamp",
    "request letters this month",
    "files of the Pitching Competition",
    "documents uploaded last week",
    "Download the activity design of the Bootcamp",
    "I need a copy of the accomplishment report for the Market Research Training",
    "attachments for activities next week",
    "Send me the invitation letters for October",
    "pdf files of the ITE 16 Pitching Competition",
]
STATUS_QUESTIONS = [
    "Which documents are missing for the Bootcamp?",
    "What files are overdue?",
    "Which documents are waiting for review?",
    "documents returned to the uploader",
    "What is still pending for the Pitching Competition?",
    "Show the checklist of the Market Research Training",
    "Which required documents are due next week?",
    "Which activities have missing documents in November?",
]
CONTENT_QUESTIONS = [
    "What does the activity design of the Bootcamp say about the budget?",
    "Summarize the accomplishment report of the Market Research Training",
    "What is the budget?",
    "What are the objectives of the Ideation Bootcamp according to the documents?",
    "What did the report mention about the participants' feedback?",
]
OTHER_QUESTIONS = [
    "When is the Investor Networking?",
    "Hello",
    "Who is the resource person of the Bootcamp?",
    "and in December?",
]


@pytest.mark.parametrize("question", ACTIVITY_QUESTIONS)
def test_activity_questions_go_to_the_activities_path(calendar_2026, question) -> None:
    assert routing.route(question, NOW).path == routing.ACTIVITIES


@pytest.mark.parametrize("question", FILE_QUESTIONS)
def test_file_questions_go_to_find_files(calendar_2026, question) -> None:
    assert routing.route(question, NOW).path == routing.FIND_FILES


@pytest.mark.parametrize("question", STATUS_QUESTIONS)
def test_missing_overdue_waiting_go_to_the_document_status_tools(calendar_2026, question) -> None:
    assert routing.route(question, NOW).path == routing.DOCUMENT_STATUS


@pytest.mark.parametrize("question", CONTENT_QUESTIONS)
def test_what_documents_say_goes_to_rag(calendar_2026, question) -> None:
    assert routing.route(question, NOW).path == routing.DOCUMENT_CONTENT


@pytest.mark.parametrize("question", OTHER_QUESTIONS)
def test_anything_else_goes_to_gemini_with_all_tools(calendar_2026, question) -> None:
    assert routing.route(question, NOW).path == routing.GEMINI_ALL_TOOLS


def test_what_the_router_reads(calendar_2026) -> None:
    route = routing.route("activities conducted in October", NOW)
    assert route.status == C and route.period.label == "October 1–31, 2026"
    route = routing.route("how many trainings this year", NOW)
    assert route.activity_type.name == "Training" and route.count and route.period.label == "January 1 – December 31, 2026"
    route = routing.route("attendance sheet of the Ideation Bootcamp", NOW)
    assert route.document_type.name == "Attendance Sheet" and route.activity_ids == [calendar_2026["bootcamp"].pk]
    route = routing.route("documents uploaded last week", NOW)
    assert route.uploaded and route.period.label == "November 9–15, 2026"
    route = routing.route("Show the draft activities", NOW)
    assert route.status == D and route.include_drafts


def test_types_come_from_their_tables(calendar_2026) -> None:
    from activities.models import ActivityType
    from documents.models import DocumentType

    ActivityType.objects.create(name="Hackathon")
    DocumentType.objects.create(name="Liquidation Report", is_required=False)
    assert routing.route("how many hackathons this year", NOW).activity_type.name == "Hackathon"
    assert routing.route("liquidation reports for November", NOW).document_type.name == "Liquidation Report"


# --- ask(): which path ran, which tools were offered ---------------------------------------------------------------


def test_the_november_question_is_answered_by_our_code_without_gemini(director, chat, calendar_2026, fake_gemini) -> None:
    answer = services.ask(director, chat, "What are the activities for the month of November?", now=NOW)
    assert fake_gemini.generate_calls == []  # no Gemini at all
    assert answer.answered_from == services.RECORDS
    assert "November 1–30, 2026" in answer.message.content


def test_the_bug_report_follow_up_still_lists_november(director, chat, calendar_2026, fake_gemini) -> None:
    """Chat 11: a file question first, then the November question in the same chat (ASSUMPTIONS 381)."""
    first = services.ask(director, chat, "can you give me the files for november's activity", now=NOW)
    assert first.answered_from == services.RECORDS and "No files for activities in November 1–30, 2026." in first.message.content
    answer = services.ask(director, chat, "What are the activities for the month of November?", now=NOW)
    for title in ("Pre-incubation / Ideation Bootcamp", "Market Research Training", "ITE 16 Pitching Competition", "Test"):
        assert title in answer.message.content
    assert fake_gemini.generate_calls == []


def test_document_status_questions_get_only_the_existing_record_tools(director, chat, calendar_2026, fake_gemini) -> None:
    fake_gemini.replies.append(say("Nothing is missing."))
    services.ask(director, chat, "Which documents are missing for the Bootcamp?", now=NOW)
    offered = {spec.name for spec in fake_gemini.generate_calls[0]["tools"]}
    assert offered == {"find_activities", "get_document_checklist", "list_activity_documents", "documents_needing_attention"}


def test_content_questions_get_the_document_search(director, chat, calendar_2026, fake_gemini) -> None:
    fake_gemini.replies.append(say("I could not find it."))
    services.ask(director, chat, "What is the budget?", now=NOW)
    assert {spec.name for spec in fake_gemini.generate_calls[0]["tools"]} == {"find_activities", tools.SEARCH_TOOL}


def test_other_questions_get_every_tool(director, chat, calendar_2026, fake_gemini) -> None:
    fake_gemini.replies.append(say("Hello!"))
    services.ask(director, chat, "Hello", now=NOW)
    assert {spec.name for spec in fake_gemini.generate_calls[0]["tools"]} == {spec.name for spec in tools.TOOL_SPECS}
    assert {tools.ACTIVITIES_TOOL, tools.FILES_TOOL} <= {spec.name for spec in tools.TOOL_SPECS}


def test_gemini_passes_words_and_our_code_writes_the_answer(director, chat, calendar_2026, fake_gemini) -> None:
    """A follow-up the router does not catch: Gemini calls the activities tool with the user's words; the dates and
    the reply are ours (Gemini's own text is not used)."""
    fake_gemini.replies += [call(tools.ACTIVITIES_TOOL, period_text="October"), say("There were 7 activities, I think.")]
    answer = services.ask(director, chat, "and in October?", now=NOW)
    content = answer.message.content
    assert "October 1–31, 2026: 1" in content and "Business Model Training" in content
    assert "I think" not in content and answer.answered_from == services.RECORDS


def test_gemini_find_files_tool_gets_no_file_content(director, chat, calendar_2026, fake_gemini, make_document) -> None:
    from .conftest import docx_file

    make_document(docx_file("SECRET attendee Juan Dela Cruz 0917"), name="att.docx", type_name="Attendance Sheet",
                  approve=False, on=calendar_2026["bootcamp"], title="Bootcamp attendance")
    fake_gemini.replies += [call(tools.FILES_TOOL, activity_name="Ideation Bootcamp"), say("Here.")]
    answer = services.ask(director, chat, "the thing for the bootcamp please", now=NOW)
    assert "Bootcamp attendance" in answer.message.content and "[file:" in answer.message.content
    sent = repr(fake_gemini.generate_calls)
    assert "SECRET" not in sent and "Juan" not in sent and "0917" not in sent


def test_a_bad_period_goes_back_to_gemini_as_an_error(director, chat, calendar_2026, fake_gemini) -> None:
    fake_gemini.replies += [call(tools.ACTIVITIES_TOOL, period_text="sometime"), say("Which period do you mean?")]
    answer = services.ask(director, chat, "and then?", now=NOW)
    assert "error" in fake_gemini.tool_results()[0][1]
    assert answer.message.content.startswith("Which period do you mean?")


def test_the_log_has_ids_path_and_tools_but_never_text(director, chat, calendar_2026, fake_gemini, caplog) -> None:
    caplog.set_level(logging.INFO, logger="assistant")
    answer = services.ask(director, chat, "What are the activities for the month of November?", now=NOW)
    line = next(r.getMessage() for r in caplog.records if r.name == "assistant" and "answered" in r.getMessage())
    assert f"chat {chat.pk}, message {answer.message.pk}: path activities, tools none" in line
    assert "November" not in line and "Bootcamp" not in line

    caplog.clear()
    fake_gemini.replies += [call(tools.ACTIVITIES_TOOL, period_text="October"), say("x")]
    services.ask(director, chat, "and in October?", now=NOW)
    line = next(r.getMessage() for r in caplog.records if r.name == "assistant" and "answered" in r.getMessage())
    assert "path gemini_all_tools, tools list_activities_by_period" in line and "October" not in line
