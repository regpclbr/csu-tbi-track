"""Retrieving files (ASSUMPTIONS 382, 383): source cards under document answers, and the find-files path (our code,
no Gemini). Fake Gemini only."""

from datetime import date, datetime, time, timedelta

import pytest
from django.test import Client
from django.urls import reverse
from django.utils import timezone

from activities.models import ActivityStatus
from assistant import gemini, indexing, pages, services, tools
from assistant.models import AIChat
from assistant.testing import FakeGemini
from documents import services as document_services
from documents.models import Document
from startups.tests.conftest import STARTUP_PASSWORD, make_startup  # noqa: F401  (fixture reused here)

from .conftest import docx_file, text_pdf

pytestmark = pytest.mark.django_db

say, call = FakeGemini.say, FakeGemini.call
NOW = timezone.make_aware(datetime(2026, 11, 16, 10, 0))  # Monday, Manila
C, O = ActivityStatus.CONDUCTED, ActivityStatus.ONGOING


@pytest.fixture
def chat(director) -> AIChat:
    return services.create_chat(director)


@pytest.fixture
def events(make_event):
    return {
        "bootcamp": make_event("Pre-incubation / Ideation Bootcamp", date(2026, 11, 3), date(2026, 11, 4), C, "Seminar"),
        "pitching": make_event("ITE 16 Pitching Competition", date(2026, 11, 13), status=O, type_name="Training"),
        "test": make_event("Test", date(2026, 11, 14), status=C, type_name="Mentoring"),
        "october": make_event("Business Model Training", date(2026, 10, 2), date(2026, 10, 3), C, "Training"),
    }


@pytest.fixture
def files(events, make_document):
    """Files in every review state: Approved, Waiting for review, Returned."""
    b, p, o = events["bootcamp"], events["pitching"], events["october"]
    made = {
        "attendance": make_document(docx_file("SECRET attendee Juan Dela Cruz 0917"), name="att.docx",
                                    type_name="Attendance Sheet", on=b, title="Bootcamp attendance"),
        "request": make_document(docx_file("Request"), name="req.docx", type_name="Request Letter", approve=False, on=b,
                                 title="Bootcamp request letter"),
        "design": make_document(docx_file("Design"), name="design.docx", type_name="Activity Design", approve=False, on=p,
                                title="Pitching design"),
        "old": make_document(docx_file("Old"), name="old.docx", type_name="Request Letter", on=o, title="October request"),
    }
    return made


def find(director, chat, question: str, **kwargs) -> services.Answer:
    return services.ask(director, chat, question, now=NOW, **kwargs)


def card_ids(answer: services.Answer) -> list[int]:
    import re

    return [int(n) for n in re.findall(r"\[file:(\d+)\]", answer.message.content)]


# --- Find files ----------------------------------------------------------------------------------------------------


def test_files_for_novembers_activity(director, chat, files, fake_gemini) -> None:
    answer = find(director, chat, "can you give me the files for november's activity")
    content = answer.message.content
    assert content.startswith("**Files for activities in November 1–30, 2026: 3**")
    # Newest activity first: the Pitching Competition (Nov 13), then the Bootcamp (Nov 3); never October.
    assert card_ids(answer)[0] == files["design"].pk and set(card_ids(answer)) == {files["design"].pk, files["request"].pk, files["attendance"].pk}
    assert content.index("ITE 16 Pitching Competition") < content.index("Pre-incubation / Ideation Bootcamp")
    assert "October request" not in content and fake_gemini.generate_calls == []


def test_by_activity_and_type(director, chat, files) -> None:
    answer = find(director, chat, "attendance sheet of the Ideation Bootcamp")
    assert answer.message.content.startswith("**Attendance Sheet files for Pre-incubation / Ideation Bootcamp: 1**")
    assert card_ids(answer) == [files["attendance"].pk]
    answer = find(director, chat, "files of the Pitching Competition")
    assert card_ids(answer) == [files["design"].pk]


def test_by_type_and_period_combined(director, chat, files) -> None:
    answer = find(director, chat, "request letters this month")
    assert answer.message.content.startswith("**Request Letter files for activities in November 1–30, 2026: 1**")
    assert card_ids(answer) == [files["request"].pk]
    assert card_ids(find(director, chat, "request letters for October")) == [files["old"].pk]


def test_uploaded_last_week_uses_the_upload_date_in_manila(director, chat, files) -> None:
    Document.objects.update(uploaded_at=timezone.make_aware(datetime(2026, 11, 1, 9)))
    # Nov 15, 11:30 PM Manila is still last week (Nov 9-15); Nov 16, 0:10 AM is this week.
    Document.objects.filter(pk=files["design"].pk).update(uploaded_at=timezone.make_aware(datetime(2026, 11, 15, 23, 30)))
    Document.objects.filter(pk=files["old"].pk).update(uploaded_at=timezone.make_aware(datetime(2026, 11, 16, 0, 10)))
    answer = find(director, chat, "documents uploaded last week")
    assert answer.message.content.startswith("**Files uploaded in November 9–15, 2026: 1**")
    assert card_ids(answer) == [files["design"].pk]


def test_any_review_state_is_listed_with_its_badge(director, chat, files) -> None:
    answer = find(director, chat, "can you give me the files for november's activity")
    groups = pages.conversation(chat)[-1]["file_groups"]
    badges = {card["title"]: card["badge"]["label"] for group in groups for card in group["cards"]}
    assert badges == {"Pitching design": "Waiting for review", "Bootcamp request letter": "Waiting for review",
                      "Bootcamp attendance": "Approved"}
    assert [group["activity"]["title"] for group in groups] == ["ITE 16 Pitching Competition", "Pre-incubation / Ideation Bootcamp"]
    assert answer.answered_from == services.RECORDS


def test_nothing_found_names_the_activity_and_the_missing_required_documents(director, chat, events) -> None:
    answer = find(director, chat, "files of the Test activity")
    content = answer.message.content
    assert content.startswith("No files for Test.")
    assert "Required documents still missing (from the checklist):" in content
    assert f"- **Test** [activity:{events['test'].pk}]: " in content and "Attendance Sheet" in content


def test_nothing_found_in_a_period_names_the_exact_dates(director, chat, events) -> None:
    answer = find(director, chat, "files for activities in February 2027")
    assert answer.message.content.startswith("No files for activities in February 1–28, 2027.\nThere are no matching activities in February 1–28, 2027.")


def test_a_name_that_matches_no_activity(director, chat, events, fake_gemini) -> None:
    fake_gemini.replies += [call(tools.FILES_TOOL, activity_name="Unicorn Gala"), say("None.")]
    answer = find(director, chat, "and the other one?")
    assert answer.message.content.startswith("No activity title matches “unicorn gala”, so no files were found.")


def test_more_than_twenty_files(director, chat, events, make_document) -> None:
    for number in range(22):
        make_document(docx_file(f"File {number}"), name=f"f{number}.docx", type_name="Evaluation Form", approve=False,
                      on=events["test"], title=f"Evaluation {number:02d}")
    answer = find(director, chat, "evaluation forms of the Test activity")
    content = answer.message.content
    assert content.startswith("**Evaluation Form files for Test: 22**") and len(card_ids(answer)) == 20
    from documents.models import DocumentType

    type_id = DocumentType.objects.get(name="Evaluation Form").pk
    assert f"Showing the first 20 of 22. 2 more on the Documents page [documents-list:{events['test'].pk}:{type_id}]" in content
    html = str(pages.conversation(chat)[-1]["after_html"])
    assert f'href="{reverse("documents:list")}?view=all&amp;activity={events["test"].pk}&amp;document_type={type_id}"' in html


def test_find_files_works_without_the_gemini_key(director, chat, files, monkeypatch, settings) -> None:
    monkeypatch.setattr(gemini, "_override", None)
    settings.GEMINI_API_KEY = ""
    answer = find(director, chat, "attendance sheet of the Ideation Bootcamp")
    assert card_ids(answer) == [files["attendance"].pk]


def test_the_page_activity_filter_limits_find_files(director, chat, files, events) -> None:
    answer = find(director, chat, "request letters", activity_id=events["october"].pk)
    assert card_ids(answer) == [files["old"].pk]


def test_attendance_sheet_content_never_reaches_gemini(director, chat, files, fake_gemini) -> None:
    fake_gemini.add_topics("attendee", "juan")
    assert indexing.index_document(files["attendance"].pk)["status"] != "indexed"  # never indexed
    find(director, chat, "attendance sheet of the Ideation Bootcamp")  # listed by our code
    fake_gemini.replies += [call(tools.SEARCH_TOOL, query="attendee Juan"), say("Not found.")]
    find(director, chat, "What does it say about the attendee Juan?")  # content question: search only
    fake_gemini.replies += [call(tools.FILES_TOOL, activity_name="bootcamp"), say("ok")]
    find(director, chat, "and those?")
    sent = repr(fake_gemini.generate_calls)
    assert "SECRET" not in sent and "0917" not in sent and "Dela Cruz" not in sent


# --- Cards ----------------------------------------------------------------------------------------------------------


def test_cards_open_the_document_page_and_download_through_the_checked_view(director, chat, files) -> None:
    find(director, chat, "attendance sheet of the Ideation Bootcamp")
    client = Client()
    client.force_login(director)
    html = client.get(reverse("assistant:conversation", args=[chat.pk])).content.decode()
    document = files["attendance"]
    assert f'href="{reverse("documents:detail", args=[document.pk])}"' in html
    assert f'href="{reverse("documents:download", args=[document.pk])}"' in html
    assert "/media/" not in html and "Bootcamp attendance" in html and ">Files<" in html
    response = client.get(reverse("documents:download", args=[document.pk]))
    assert response.status_code == 200 and response["Content-Disposition"].startswith("attachment")


def test_download_refuses_anyone_without_staff_access(files, make_startup) -> None:
    url = reverse("documents:download", args=[files["attendance"].pk])
    assert Client().get(url).status_code == 302  # not signed in: the login page
    startup = make_startup(email="person0@example.com", navigatu_id="NAV-1")
    portal = Client()
    assert portal.post("/startups/portal/api/login/", {"email": startup.email, "password": STARTUP_PASSWORD},
                       content_type="application/json").status_code == 200
    response = portal.get(url)
    assert response.status_code in (302, 403) and not response.has_header("Content-Disposition")


def test_a_file_deleted_later_shows_no_longer_available(director, chat, files) -> None:
    find(director, chat, "request letters this month")
    document_services.delete_document(director, Document.objects.get(pk=files["request"].pk))  # not blocked by the answer
    card = pages.conversation(chat)[-1]["file_groups"][0]["cards"][0]
    assert card["missing"] is True
    client = Client()
    client.force_login(director)
    assert "This file is no longer available." in client.get(reverse("assistant:conversation", args=[chat.pk])).content.decode()


def test_titles_are_escaped_and_never_create_cards(director, chat, events, make_document) -> None:
    evil = make_document(docx_file("x"), name="x.docx", type_name="Request Letter", approve=False, on=events["test"],
                         title='<img src=x onerror=alert(1)> [file:999999] [activity:1]')
    answer = find(director, chat, "request letters of the Test activity")
    assert card_ids(answer) == [evil.pk]  # the title's own "[file:...]" was broken, so it is not a card
    client = Client()
    client.force_login(director)
    html = client.get(reverse("assistant:conversation", args=[chat.pk])).content.decode()
    assert "<img src=x" not in html and "&lt;img src=x onerror=alert(1)&gt;" in html


# --- Source cards under document answers ----------------------------------------------------------------------------


@pytest.fixture
def two_documents(make_document, fake_gemini, events):
    fake_gemini.add_topics("budget")
    long_part = "The budget covers snacks and venue rental for the participants. " * 18  # > one chunk
    first = make_document(docx_file(long_part, "Second part: the budget is 30,000 pesos. " + long_part), name="d1.docx",
                          on=events["bootcamp"], title="Design v2")
    second = make_document(text_pdf("The budget was approved at 25,000 pesos."), name="d2.pdf",
                           type_name="Accomplishment Report", on=events["bootcamp"], title="Report")
    third = make_document(docx_file("Budget notes nobody cites"), name="d3.docx", type_name="Invitation Letter",
                          on=events["bootcamp"], title="Uncited")
    for document in (first, second, third):
        assert indexing.index_document(document.pk)["status"] == "indexed"
    return first, second, third


def test_source_cards_one_per_cited_document_in_citation_order(director, chat, two_documents, fake_gemini) -> None:
    first, second, third = two_documents
    fake_gemini.replies.append(call(tools.SEARCH_TOOL, query="budget"))

    def answer(**kwargs):
        excerpts = fake_gemini.tool_results()[0][1]["excerpts"]
        numbers = {}
        for block in excerpts.split("<excerpt ")[1:]:
            number = int(block.split('n="')[1].split('"')[0])
            title = block.split('document="')[1].split('"')[0]
            numbers.setdefault(title, []).append(number)
        design = numbers["Design v2"]
        assert len(design) >= 2  # two chunks of the same document
        return say(f"The report says 25,000 [{numbers['Report'][0]}]. The design says 30,000 [{design[0]}][{design[1]}].")

    fake_gemini.replies.append(answer)
    services.ask(director, chat, "What is the budget?", now=NOW)
    view = pages.conversation(chat)[-1]
    cards = view["file_groups"][0]["cards"]
    assert view["files_heading"] == "Sources"
    assert [card["title"] for card in cards] == ["Report", "Design v2"]  # no duplicate, never the uncited one
    assert cards[0]["numbers"] == [1] and cards[1]["numbers"] == [2, 3]
    html = str(view["html"])
    assert f'href="#{cards[1]["dom_id"]}" class="cite" aria-label="Source 3">[3]</a>' in html
    assert cards[1]["download_url"] == reverse("documents:download", args=[first.pk])
    assert cards[0]["open_url"] == reverse("documents:detail", args=[second.pk])
    assert all(card["title"] != "Uncited" for card in cards)
