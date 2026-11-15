"""Activity lists answered by our code (ASSUMPTIONS 381): periods in Manila time, drafts left out unless asked,
at most 20 with "N more", the exact period when empty, safe activity links, and no Gemini (works without the key)."""

import re
from datetime import date, datetime, time, timedelta

import pytest
from django.test import Client
from django.urls import reverse
from django.utils import timezone

from activities.models import ActivityStatus, ActivityType
from assistant import gemini, pages, services
from assistant.gemini import AssistantUnavailable
from assistant.listings import ActivityQuery, activities_answer, safe_text
from assistant.models import AIChat, AIChatMessage
from assistant.periods import parse_period

pytestmark = pytest.mark.django_db

NOW = timezone.make_aware(datetime(2026, 11, 16, 10, 0))  # Monday, Manila
C, O, S, D = ActivityStatus.CONDUCTED, ActivityStatus.ONGOING, ActivityStatus.SCHEDULED, ActivityStatus.DRAFT


@pytest.fixture
def november(make_event):
    return {
        "test": make_event("Test", date(2026, 11, 14), status=C, type_name="Mentoring", times=(time(17, 44), time(18))),
        "bootcamp": make_event("Pre-incubation / Ideation Bootcamp", date(2026, 11, 3), date(2026, 11, 4), C, "Seminar",
                               (time(8), time(11)), venue="CSU Hall"),
        "pitching": make_event("ITE 16 Pitching Competition", date(2026, 11, 13), status=O, type_name="Training"),
        "market": make_event("Market Research Training", date(2026, 11, 6), status=C, type_name="Training", times=None),
        "draft": make_event("Draft Workshop Idea", date(2026, 11, 25), status=D),
        "october": make_event("Business Model Training", date(2026, 10, 2), date(2026, 10, 3), C, "Training"),
        "crossing": make_event("Month-end Bootcamp", date(2026, 10, 30), date(2026, 11, 2), C, "Workshop"),
        "december": make_event("Technology Forum", date(2026, 12, 2), status=S, type_name="Forum"),
    }


@pytest.fixture
def chat(director) -> AIChat:
    return services.create_chat(director)


def answer_for(question: str) -> str:
    from assistant import routing

    route = routing.route(question, NOW)
    assert route.path == routing.ACTIVITIES, question
    return activities_answer(ActivityQuery(route.period, route.status, route.activity_type, route.include_drafts, route.count), NOW).text


def test_the_november_example_lists_every_activity_by_date(november) -> None:
    text = answer_for("What are the activities for the month of November?")
    lines = text.splitlines()
    assert lines[0] == "**Activities in November 1–30, 2026: 5** (drafts not included: 1 draft in this period; ask about drafts to see it)"
    b, m, p, t, x = (november[k] for k in ("bootcamp", "market", "pitching", "test", "crossing"))
    assert lines[2:] == [
        f"1. **Month-end Bootcamp** [activity:{x.pk}] — Oct 30 – Nov 2, 2026 · 8:00 AM – 5:00 PM · Conducted · CSU Hall",
        f"2. **Pre-incubation / Ideation Bootcamp** [activity:{b.pk}] — Nov 3–4, 2026 · 8:00 AM – 11:00 AM · Conducted · CSU Hall",
        f"3. **Market Research Training** [activity:{m.pk}] — Nov 6, 2026 · Time not set · Conducted · CSU Hall",
        f"4. **ITE 16 Pitching Competition** [activity:{p.pk}] — Nov 13, 2026 · 8:00 AM – 5:00 PM · Ongoing · CSU Hall",
        f"5. **Test** [activity:{t.pk}] — Nov 14, 2026 · 5:44 PM – 6:00 PM · Conducted · CSU Hall",
    ]


def test_drafts_only_when_asked(november) -> None:
    text = answer_for("Show the activities in November including drafts")
    assert "Draft Workshop Idea" in text and "drafts not included" not in text and ": 6**" in text
    text = answer_for("Show the draft activities in November")
    assert text.startswith("**Draft activities in November 1–30, 2026: 1**") and "Draft Workshop Idea" in text


def test_no_drafts_note_when_the_period_has_none(november) -> None:
    text = answer_for("activities in October")
    assert text.startswith("**Activities in October 1–31, 2026: 2**\n")  # the crossing activity counts in both months


def test_an_empty_period_names_the_exact_dates(november) -> None:
    assert answer_for("activities in February 2027") == "There are no activities in February 1–28, 2027."
    text = answer_for("what activities are ongoing next week")
    assert text == "There are no Ongoing activities in November 23–29, 2026."


def test_status_and_type_filters_and_counts(november) -> None:
    text = answer_for("activities conducted in October")
    assert text.startswith("**Conducted activities in October 1–31, 2026: 2**")
    text = answer_for("how many trainings this year")
    assert text.startswith("**Training activities in January 1 – December 31, 2026: 3**")
    assert "Business Model Training" in text and "Market Research Training" in text and "ITE 16" in text


def test_this_week_next_week_upcoming_and_now(november, make_event, clock) -> None:
    assert "Test" in answer_for("activities last week") and "November 9–15, 2026" in answer_for("activities last week")
    assert answer_for("events next week") == ("There are no activities in November 23–29, 2026. (drafts not included: "
                                              "1 draft in this period; ask about drafts to see it)")
    upcoming = answer_for("upcoming activities")
    assert upcoming.startswith("**Upcoming activities (from November 16, 2026): 1** (drafts not included")
    assert "Technology Forum" in upcoming and "Test" not in upcoming
    live = make_event("Live Mentoring", date(2026, 11, 16), status=O, type_name="Mentoring", times=(time(9), time(12)))
    now_text = answer_for("what's happening now")
    assert now_text.startswith("**Activities happening now: 1**") and f"[activity:{live.pk}]" in now_text


def test_more_than_twenty_shows_the_first_twenty_and_how_many_more(make_event) -> None:
    for day in range(25):
        make_event(f"Daily Session {day + 1:02d}", date(2026, 9, 1) + timedelta(days=day))
    text = answer_for("activities in September")
    lines = text.splitlines()
    assert lines[0] == "**Activities in September 1–30, 2026: 25**"
    assert sum(bool(re.match(r"\d+\. ", line)) for line in lines) == 20
    assert "Daily Session 20" in text and "Daily Session 21" not in text
    assert lines[-1].startswith("Showing the first 20 of 25. 5 more are in the Activities list [activities-list:2026-09-01:2026-09-30:0:0]")


def test_titles_never_create_markers_or_list_lines() -> None:
    assert safe_text("Evil [activity:1] [1] [file:9]\nline") == "Evil ［activity:1] ［1] ［file:9] line"


# --- ask() and the page ---------------------------------------------------------------------------------------------


def test_activity_questions_work_without_the_gemini_key(director, chat, november, monkeypatch, settings) -> None:
    monkeypatch.setattr(gemini, "_override", None)
    settings.GEMINI_API_KEY = ""
    assert not gemini.is_configured()
    answer = services.ask(director, chat, "What are the activities for the month of November?", now=NOW)
    assert "Pre-incubation / Ideation Bootcamp" in answer.message.content
    with pytest.raises(AssistantUnavailable) as error:
        services.ask(director, chat, "What is the budget?", now=NOW)  # other questions: the current message
    assert error.value.kind == "not_configured"


def test_the_page_says_the_ai_is_off_without_the_key(director, monkeypatch, settings) -> None:
    client = Client()
    client.force_login(director)
    assert "The AI is turned off" not in client.get(reverse("assistant:chat")).content.decode()
    monkeypatch.setattr(gemini, "_override", None)
    settings.GEMINI_API_KEY = ""
    html = client.get(reverse("assistant:chat")).content.decode()
    assert "The AI is turned off. You can still ask about activities and find files" in html


def test_the_answer_links_each_activity_page_and_escapes_titles(director, chat, make_event) -> None:
    evil = make_event('<script>alert(1)</script> **Pitch**', date(2026, 11, 3))
    services.ask(director, chat, "activities in November", now=NOW)
    view = pages.conversation(chat)[-1]
    html = str(view["html"])
    url = reverse("activities:detail", args=[evil.pk])
    assert f'<a href="{url}" class="cite" aria-label="Open the activity page: &lt;script&gt;alert(1)&lt;/script&gt; **Pitch**">(open)</a>' in html
    assert "<script>" not in html and "&lt;script&gt;" in html
    assert "[activity:" not in html and "[activity:" not in view["text"]


def test_activity_markers_link_only_existing_activities() -> None:
    lookup = pages.Lookup()
    html = str(pages.answer_html("See [activity:999] and [activities-list:2026-13-99:-:0:0] and [file:5].", {}, lookup))
    assert "href" not in html and "[activity" not in html and "[file" not in html


def test_the_activities_list_marker_builds_a_safe_internal_link() -> None:
    html = str(pages.answer_html("More [activities-list:2026-09-01:2026-09-30:2:3]", {}, pages.Lookup()))
    assert f'href="{reverse("activities:list")}?date_from=2026-09-01&amp;date_to=2026-09-30&amp;status=2&amp;activity_type=3&amp;sort=start_date"' in html


def test_startups_never_reach_the_assistant(client) -> None:
    assert client.get(reverse("assistant:chat")).status_code == 302
    assert client.post(reverse("assistant:ask"), {"question": "activities this month"}).status_code == 302
    assert AIChatMessage.objects.count() == 0


def test_activity_answers_send_nothing_to_gemini(director, chat, november, fake_gemini) -> None:
    services.ask(director, chat, "activities this month", now=NOW)
    services.ask(director, chat, "how many trainings this year", now=NOW)
    assert fake_gemini.generate_calls == [] and fake_gemini.embed_calls == []


def test_a_new_activity_type_is_matched_from_the_table(make_event) -> None:
    ActivityType.objects.create(name="Hackathon")
    make_event("Code Sprint", date(2026, 11, 20), status=S, type_name="Hackathon")
    assert answer_for("how many hackathons this month").startswith("**Hackathon activities in November 1–30, 2026: 1**")


def test_period_label_matches_the_parser() -> None:
    assert parse_period("this month", NOW).label == "November 1–30, 2026"
