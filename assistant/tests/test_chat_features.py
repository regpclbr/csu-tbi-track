"""AI Assistant chat improvements (2026-11-15, ASSUMPTIONS 374-379): Enter to send, auto-scroll with "Jump to latest",
a small SAFE Markdown subset in answers, deleting and renaming chats, titles generated in the background, the "My chats"
heading and the answer instruction. Every Gemini call goes to the FakeGemini of the root conftest (never the real API);
the keyboard and scrolling behaviour itself is checked in a real browser by test_chat_browser.py (opt-in)."""

from html.parser import HTMLParser

import pytest
from django.http import Http404
from django.test import Client
from django.urls import reverse

from assistant import gemini, pages, services, tasks, tools
from assistant.gemini import AssistantUnavailable
from assistant.models import AIChat, AIChatMessage, AIMessageSource
from assistant.testing import FakeGemini
from documents.models import Document

from .conftest import docx_file

pytestmark = pytest.mark.django_db

say, call = FakeGemini.say, FakeGemini.call
HTMX = {"HTTP_HX_REQUEST": "true"}
LINKS = {1: "/documents/5/", 2: "/documents/6/"}


def html_of(text: str) -> str:
    return str(pages.answer_html(text, LINKS))


# --- 3. Safe formatting: escape first, then only the small subset ---------------------------------------------------


class TagCollector(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.tags: list[tuple[str, dict]] = []

    def handle_starttag(self, tag, attrs) -> None:
        self.tags.append((tag, dict(attrs)))


ALLOWED_TAGS = {"strong", "em", "ul", "ol", "li", "br", "a"}


def assert_only_our_tags(html: str) -> None:
    """Every tag is from the subset; the only attributes are our citation link's and an ordered list's number."""
    parser = TagCollector()
    parser.feed(html)
    for tag, attrs in parser.tags:
        assert tag in ALLOWED_TAGS, (tag, html)
        if tag == "a":
            assert attrs.get("class") == "cite" and attrs.get("href") in LINKS.values(), html
            assert set(attrs) == {"href", "class", "aria-label"}, html
        elif tag == "ol":
            assert set(attrs) <= {"start"} and all(value.isdigit() for value in attrs.values()), html
        else:
            assert attrs == {}, html


ATTACKS = [
    '<script>alert("x")</script>',
    '<img src=x onerror="alert(1)">',
    "<iframe src=//evil.example></iframe>",
    "[click me](javascript:alert(1))",
    "[1](javascript:alert(1))",  # looks like a citation: only [1] becomes OUR link, the rest stays text
    "**[bold link](javascript:alert(1))**",
    '- <a href="javascript:alert(1)">x</a>',
    "1. [x](JaVaScRiPt:alert(1))",
    "![image](https://evil.example/x.png)",
    "**<b onclick=alert(1)>x</b>**",
    "## <script>alert(1)</script>",
    "_<svg onload=alert(1)>_",
    "*<style>body{display:none}</style>*",
    "<a href='https://evil.example'>[2]</a>",
    "javascript:alert(1)",
    "| a | b |\n|---|---|\n| 1 | 2 |",
]


@pytest.mark.parametrize("attack", ATTACKS)
def test_html_links_and_scripts_in_an_answer_stay_escaped_text(attack: str) -> None:
    html = html_of(attack)
    assert_only_our_tags(html)
    for raw in ("<script", "<img", "<iframe", "<svg", "<style", "<b ", "<a href=\"javascript"):
        assert raw not in html.lower()  # "onerror=" may remain as harmless TEXT: assert_only_our_tags checks attributes
    assert 'href="javascript' not in html.lower() and "href='https" not in html


def test_attacks_stay_text_on_the_page(director, login, fake_gemini) -> None:
    fake_gemini.replies += [call("documents_needing_attention"), say("\n".join(ATTACKS))]
    client = login(director)
    client.post(reverse("assistant:ask"), {"question": "Overdue?"}, **HTMX)
    html = client.get(reverse("assistant:conversation", args=[AIChat.objects.get().pk])).content.decode()
    assert "<script>alert" not in html and "<img src=x" not in html and 'href="javascript' not in html.lower()
    assert "&lt;script&gt;alert(&quot;x&quot;)&lt;/script&gt;" in html


def test_bold_italics_lists_and_citations() -> None:
    text = "The **total** is *big* and _final_ [1].\n\n* one\n* two **[2]**\n\nThen:\n1. first\n2. second\nEnd"
    assert html_of(text) == (
        'The <strong>total</strong> is <em>big</em> and <em>final</em> '
        '<a href="/documents/5/" class="cite" aria-label="Source 1">[1]</a>.'
        '<ul><li>one</li><li>two <strong><a href="/documents/6/" class="cite" aria-label="Source 2">[2]</a></strong></li></ul>'
        "Then:<ol><li>first</li><li>second</li></ol>End"
    )


def test_dash_and_bullet_lists_and_a_numbered_list_that_starts_later() -> None:
    assert html_of("- a\n• b") == "<ul><li>a</li><li>b</li></ul>"
    assert html_of("3. c\n4) d") == '<ol start="3"><li>c</li><li>d</li></ol>'


def test_headings_become_a_bold_line_without_the_marks() -> None:
    assert html_of("# Budget\n## Venue ##\n### Dates\nText") == (
        "<strong>Budget</strong><br><strong>Venue</strong><br><strong>Dates</strong><br>Text")
    assert html_of("#### Four\n#hashtag") == "#### Four<br>#hashtag"  # only #, ## and ###, with a space


def test_line_breaks_and_paragraph_gaps_stay() -> None:
    assert html_of("A\nB\n\nC") == "A<br>B<br><br>C"


@pytest.mark.parametrize("text", [
    "certificate_text_id",
    "file_name_v2.pdf",
    "Open certificate_text_id and file_name_v2.pdf now",
    "__init__ and snake_case_name",
    "a_b_ c",
    "2 * 3 * 4",
    "a*b*c",
    "**not closed",
    "* not a list item*",
])
def test_underscores_inside_names_and_lone_stars_stay_unchanged(text: str) -> None:
    html = html_of(text)
    assert "<em>" not in html and "<strong>" not in html
    if not text.startswith("* "):
        assert html == text


def test_underscore_italics_only_at_word_boundaries() -> None:
    assert html_of("_note_ and (_this_) but not my_var_name") == "<em>note</em> and (<em>this</em>) but not my_var_name"


def test_bold_italic_together() -> None:
    assert html_of("***both***") == "<strong><em>both</em></strong>"


def test_the_live_region_text_has_no_marks() -> None:
    assert pages.answer_text("## Budget\n- **total** is _final_ [1]\n1. *one*") == "Budget\ntotal is final [1]\n1. one"


def test_the_answer_instruction_asks_for_simple_formatting(director, fake_gemini) -> None:
    """Short paragraphs and simple lists, no tables or headings; the citation rule is unchanged."""
    fake_gemini.replies += [call("documents_needing_attention"), say("Nothing is overdue.")]
    services.ask(director, services.create_chat(director), "Overdue?")
    instruction = fake_gemini.generate_calls[0]["system_instruction"]
    assert "Write short paragraphs" in instruction and "Never use tables and never use headings." in instruction
    assert ("Cite every factual statement taken from an excerpt with its number in square brackets, for example [1] or "
            "[2][3]. Never cite anything else, and never invent excerpt numbers.") in instruction


# --- Fixtures for the database tests ---------------------------------------------------------------------------------


@pytest.fixture
def login():
    def log_in(member) -> Client:
        client = Client()
        client.force_login(member)
        return client

    return log_in


def chat_with_answer(member, question: str = "What is the budget of the Pitching Workshop?", answer: str = "It is 30,000 pesos.") -> AIChat:
    """A chat as services.ask leaves it after the first answer (first-question title)."""
    chat = AIChat.objects.create(member=member, title=services.title_from_question(question))
    chat.messages.create(role=AIChatMessage.Role.USER, content=question)
    chat.messages.create(role=AIChatMessage.Role.ASSISTANT, content=answer)
    return chat


# --- 5. Titles generated in the background ---------------------------------------------------------------------------


def test_the_first_answer_queues_a_title_after_commit_and_later_answers_do_not(
        director, fake_gemini, monkeypatch, django_capture_on_commit_callbacks) -> None:
    queued = []
    monkeypatch.setattr(services, "queue_chat_title", queued.append)
    chat = services.create_chat(director)
    fake_gemini.replies += [call("documents_needing_attention"), say("Nothing is overdue."),
                            call("documents_needing_attention"), say("Still nothing.")]
    with django_capture_on_commit_callbacks(execute=True):
        first = services.ask(director, chat, "Which documents are overdue right now?")
    assert first.title_queued is True and queued == [chat.pk]
    chat.refresh_from_db()
    assert chat.title == "Which documents are overdue right now?"  # the first question until the title is ready
    with django_capture_on_commit_callbacks(execute=True):
        second = services.ask(director, chat, "And now?")
    assert second.title_queued is False and queued == [chat.pk]


def test_queue_chat_title_queues_the_django_q_task(monkeypatch) -> None:
    import django_q.tasks

    calls = []
    monkeypatch.setattr(django_q.tasks, "async_task", lambda *args, **kwargs: calls.append((args, kwargs)))
    services.queue_chat_title(7)
    assert calls == [((services.TITLE_TASK, 7), {"task_name": "Chat title 7"})]


def test_a_generated_title_replaces_the_first_question(director, fake_gemini) -> None:
    chat = chat_with_answer(director)
    AIChat.objects.filter(pk=chat.pk).update(updated_at=chat.created_at)
    before = AIChat.objects.get(pk=chat.pk).updated_at
    fake_gemini.replies.append(say('"Pitching Workshop budget."'))
    assert services.generate_chat_title(chat.pk) == services.TITLE_GENERATED
    chat.refresh_from_db()
    assert chat.title == "Pitching Workshop budget" and chat.updated_at == before  # keeps its place in My chats
    sent = fake_gemini.generate_calls[0]
    assert sent["system_instruction"] == services.TITLE_INSTRUCTION and sent["tools"] == []
    assert "What is the budget of the Pitching Workshop?" in sent["turns"][0].text and "30,000 pesos" in sent["turns"][0].text


def test_the_task_runs_the_service(director, fake_gemini) -> None:
    chat = chat_with_answer(director)
    fake_gemini.replies.append(say("Workshop budget"))
    assert tasks.run_generate_chat_title(chat.pk) == services.TITLE_GENERATED
    assert AIChat.objects.get(pk=chat.pk).title == "Workshop budget"


@pytest.mark.parametrize(("reply", "title"), [
    ('"Budget Review"', "Budget Review"),
    ("Title: Workshop budget?", "Workshop budget"),
    ("**Missing documents**\nA second line", "Missing documents"),
    ("one two three four five six seven eight nine", "one two three four five six"),
    ("“Overdue documents for November.”", "Overdue documents for November"),
    ("Don't forget the budget!", "Don't forget the budget"),
    ("# Venue and dates.", "Venue and dates"),
    ("'Single quoted'", "Single quoted"),
    ("   ", ""),
    ('"..."', ""),
])
def test_clean_title(reply: str, title: str) -> None:
    assert services.clean_title(reply) == title


def test_an_empty_generated_title_keeps_the_first_question(director, fake_gemini) -> None:
    chat = chat_with_answer(director)
    fake_gemini.replies.append(say('""'))
    assert services.generate_chat_title(chat.pk) == services.TITLE_FAILED
    assert AIChat.objects.get(pk=chat.pk).title == "What is the budget of the Pitching Workshop?"


@pytest.mark.parametrize("kind", ["rate_limit", "unavailable", "timeout", "rejected"])
def test_gemini_failing_keeps_the_first_question(director, fake_gemini, kind) -> None:
    chat = chat_with_answer(director)

    def fail(**_kwargs):
        raise AssistantUnavailable("Gemini failed.", kind)

    fake_gemini.replies.append(fail)
    assert services.generate_chat_title(chat.pk) == services.TITLE_FAILED
    assert AIChat.objects.get(pk=chat.pk).title == "What is the budget of the Pitching Workshop?"


def test_no_api_key_keeps_the_first_question(director, monkeypatch, settings) -> None:
    monkeypatch.setattr(gemini, "_override", None)  # no fake: get_gateway() checks the key (and never calls Google)
    settings.GEMINI_API_KEY = ""
    chat = chat_with_answer(director)
    assert services.generate_chat_title(chat.pk) == services.TITLE_FAILED
    assert AIChat.objects.get(pk=chat.pk).title == "What is the budget of the Pitching Workshop?"


def test_a_renamed_chat_is_never_overwritten(director, fake_gemini) -> None:
    chat = chat_with_answer(director)
    services.rename_chat(director, chat.pk, "My budget notes")
    assert services.generate_chat_title(chat.pk) == services.TITLE_KEPT
    assert fake_gemini.generate_calls == []  # Gemini is not even asked
    assert AIChat.objects.get(pk=chat.pk).title == "My budget notes"


def test_a_rename_while_gemini_answers_wins(director, fake_gemini) -> None:
    chat = chat_with_answer(director)

    def rename_meanwhile(**_kwargs):
        services.rename_chat(director, chat.pk, "Typed by hand")
        return say("Generated title")

    fake_gemini.replies.append(rename_meanwhile)
    assert services.generate_chat_title(chat.pk) == services.TITLE_KEPT
    assert AIChat.objects.get(pk=chat.pk).title == "Typed by hand"


def test_a_deleted_chat_is_skipped(director, fake_gemini) -> None:
    chat = chat_with_answer(director)
    services.delete_chat(director, chat.pk)
    assert services.generate_chat_title(chat.pk) == services.TITLE_SKIPPED
    assert fake_gemini.generate_calls == []


def test_the_task_never_raises(monkeypatch) -> None:
    def crash(chat_id):
        raise RuntimeError("boom")

    monkeypatch.setattr(services, "generate_chat_title", crash)
    assert tasks.run_generate_chat_title(1) == services.TITLE_FAILED


def test_the_first_answer_on_the_page_schedules_one_list_refresh(director, login, fake_gemini) -> None:
    client = login(director)
    fake_gemini.replies += [call("documents_needing_attention"), say("Nothing is overdue.")]
    html = client.post(reverse("assistant:ask"), {"question": "Overdue?"}, **HTMX).content.decode()
    chat = AIChat.objects.get()
    refresh = f'<div id="title-refresh" hx-swap-oob="true" hx-get="{reverse("assistant:chat_list")}?chat={chat.pk}"'
    assert refresh in html and 'hx-trigger="load delay:6s" hx-target="#chat-list" hx-swap="outerHTML"' in html
    fake_gemini.replies += [call("documents_needing_attention"), say("Still nothing.")]
    html = client.post(reverse("assistant:ask"), {"question": "Again?", "chat": chat.pk}, **HTMX).content.decode()
    assert 'id="title-refresh"' not in html


def test_the_list_refresh_shows_the_new_title_and_only_my_chats(director, make_member, login) -> None:
    chat = chat_with_answer(director)
    AIChat.objects.filter(pk=chat.pk).update(title="Workshop budget")
    theirs = chat_with_answer(make_member("Admin Staff"), "Someone else's question")
    client = login(director)
    html = client.get(reverse("assistant:chat_list"), {"chat": chat.pk}, **HTMX).content.decode()
    assert '<div id="chat-list"' in html and "Workshop budget" in html and 'aria-current="page"' in html
    assert '<h2 id="chat-title" hx-swap-oob="true" class="truncate font-semibold text-ink">Workshop budget</h2>' in html
    html = client.get(reverse("assistant:chat_list"), {"chat": theirs.pk}, **HTMX).content.decode()
    assert "Someone else" not in html and 'id="chat-title"' not in html
    assert Client().get(reverse("assistant:chat_list")).status_code == 302


# --- 5. Renaming by hand ----------------------------------------------------------------------------------------------


def test_rename_cleans_spaces_and_keeps_the_place_in_the_list(director) -> None:
    chat = chat_with_answer(director)
    before = AIChat.objects.get(pk=chat.pk).updated_at
    renamed = services.rename_chat(director, chat.pk, "  Budget   notes \n")
    assert renamed.title == "Budget notes" and AIChat.objects.get(pk=chat.pk).updated_at == before


@pytest.mark.parametrize(("title", "error"), [("   ", "Type a title."), ("x" * 81, "at most 80 characters")])
def test_rename_refuses_an_empty_or_long_title(director, title, error) -> None:
    from django.core.exceptions import ValidationError

    chat = chat_with_answer(director)
    with pytest.raises(ValidationError, match=error):
        services.rename_chat(director, chat.pk, title)
    assert AIChat.objects.get(pk=chat.pk).title == chat.title


def test_rename_page_action(director, login) -> None:
    chat, other = chat_with_answer(director), chat_with_answer(director, "Second question")
    client = login(director)
    response = client.post(reverse("assistant:rename", args=[other.pk]), {"title": "Renamed", "current": chat.pk})
    assert response.status_code == 302 and response["Location"] == reverse("assistant:conversation", args=[chat.pk])
    assert AIChat.objects.get(pk=other.pk).title == "Renamed"
    assert "Chat renamed." in client.get(response["Location"]).content.decode()
    response = client.post(reverse("assistant:rename", args=[other.pk]), {"title": " ", "current": ""})
    assert response["Location"] == reverse("assistant:chat")
    assert "Type a title." in client.get(response["Location"]).content.decode()
    assert AIChat.objects.get(pk=other.pk).title == "Renamed"


# --- 4. Deleting a chat -----------------------------------------------------------------------------------------------


@pytest.fixture
def cited_chat(director, make_document, fake_gemini):
    """A chat whose answer cites an approved document (so it has an AI_MESSAGE_SOURCE)."""
    from assistant import indexing

    fake_gemini.add_topics("budget")
    document = make_document(docx_file("The budget is 30,000 pesos."), title="Design v2")
    assert indexing.index_document(document.pk)["status"] == "indexed"
    fake_gemini.replies += [call(tools.SEARCH_TOOL, query="budget"), say("The budget is 30,000 pesos [1].")]
    chat = services.create_chat(director)
    services.ask(director, chat, "What is the budget?")
    assert AIMessageSource.objects.filter(message__chat=chat).count() == 1
    return chat, document


def test_delete_removes_the_chat_its_messages_and_sources_but_not_the_document(director, cited_chat) -> None:
    chat, document = cited_chat
    services.delete_chat(director, chat.pk)
    assert not AIChat.objects.filter(pk=chat.pk).exists()
    assert not AIChatMessage.objects.filter(chat_id=chat.pk).exists() and not AIMessageSource.objects.exists()
    assert Document.objects.filter(pk=document.pk).exists()


def test_someone_elses_chat_cannot_be_deleted_or_renamed(director, make_member, login) -> None:
    theirs = chat_with_answer(make_member("Admin Staff"))
    with pytest.raises(Http404):
        services.delete_chat(director, theirs.pk)
    with pytest.raises(Http404):
        services.rename_chat(director, theirs.pk, "Mine now")
    client = login(director)
    assert client.post(reverse("assistant:delete", args=[theirs.pk])).status_code == 404
    assert client.post(reverse("assistant:rename", args=[theirs.pk]), {"title": "Mine now"}).status_code == 404
    theirs.refresh_from_db()
    assert theirs.title == "What is the budget of the Pitching Workshop?" and theirs.messages.count() == 2


def test_deleting_the_open_chat_goes_to_a_new_chat(director, login) -> None:
    chat = chat_with_answer(director)
    client = login(director)
    response = client.post(reverse("assistant:delete", args=[chat.pk]), {"current": chat.pk})
    assert response.status_code == 302 and response["Location"] == reverse("assistant:chat")
    assert not AIChat.objects.exists()
    html = client.get(response["Location"]).content.decode()
    assert "Chat deleted." in html and "No chats yet" in html


def test_deleting_another_chat_stays_on_the_open_one(director, login) -> None:
    open_chat, other = chat_with_answer(director), chat_with_answer(director, "Other question")
    response = login(director).post(reverse("assistant:delete", args=[other.pk]), {"current": open_chat.pk})
    assert response["Location"] == reverse("assistant:conversation", args=[open_chat.pk])
    assert list(AIChat.objects.values_list("pk", flat=True)) == [open_chat.pk]


def test_delete_and_rename_need_post_and_a_login(director, login) -> None:
    chat = chat_with_answer(director)
    client = login(director)
    assert client.get(reverse("assistant:delete", args=[chat.pk])).status_code == 405
    assert client.get(reverse("assistant:rename", args=[chat.pk])).status_code == 405
    assert Client().post(reverse("assistant:delete", args=[chat.pk])).status_code == 302
    assert AIChat.objects.filter(pk=chat.pk).exists()


# --- The page: keyboard, scrolling, menu, dialogs, heading ------------------------------------------------------------


def test_enter_sends_and_the_help_text_says_so(director, login) -> None:
    html = login(director).get(reverse("assistant:chat")).content.decode()
    assert "Enter to send, Shift + Enter for a new line. At most 2000 characters." in html
    assert "Ctrl + Enter" not in html and "keydown.enter.ctrl" not in html
    assert '@keydown.enter="enterKey($event)"' in html and "if (event.shiftKey || event.isComposing" in html
    assert "if (!this.busy &amp;&amp; this.hasText) event.target.form.requestSubmit()" in html or \
           "if (!this.busy && this.hasText) event.target.form.requestSubmit()" in html
    assert ':disabled="busy || !hasText"' in html and 'hx-sync="this:drop"' in html


def test_the_chat_follows_new_messages_and_offers_jump_to_latest(director, login) -> None:
    html = login(director).get(reverse("assistant:chat")).content.decode()
    assert '@scroll.passive="onScroll()"' in html and "contentAdded()" in html
    assert "el.scrollHeight - el.scrollTop - el.clientHeight &lt; 80" in html or "el.clientHeight < 80" in html
    jump = html[html.index('id="jump-latest"'):]
    assert 'x-show="showJump"' in jump[:200] and "Jump to latest</button>" in " ".join(jump[:900].split())
    assert "prefers-reduced-motion: reduce" in html  # smooth scrolling only when motion is allowed


def test_my_chats_heading_is_never_cut_off(director, login) -> None:
    html = login(director).get(reverse("assistant:chat")).content.decode()
    assert '<h2 id="my-chats-title" class="card-title min-w-0 flex-1 text-base">My chats</h2>' in html


def test_each_chat_has_a_menu_and_the_page_has_the_dialogs(director, login) -> None:
    chat = chat_with_answer(director)
    html = login(director).get(reverse("assistant:conversation", args=[chat.pk])).content.decode()
    assert f'data-rename-url="{reverse("assistant:rename", args=[chat.pk])}"' in html
    assert f'data-delete-url="{reverse("assistant:delete", args=[chat.pk])}"' in html
    assert 'aria-label="Options for the chat What is the budget of the Pitching Workshop?" aria-haspopup="menu"' in html
    assert "Rename</button>" in html and "Delete</button>" in html
    assert "Delete this chat? This can&#x27;t be undone." in html or "Delete this chat? This can't be undone." in html
    assert '<dialog id="delete-chat"' in html and '<dialog id="rename-chat"' in html
    assert f'<input type="hidden" name="current" value="{chat.pk}">' in html
    assert 'maxlength="80"' in html


# --- The page fits the window (ASSUMPTIONS 380; checked in Chromium by test_chat_browser.py) ---------------------------


def test_only_the_assistant_page_fits_the_window(director, login) -> None:
    client = login(director)
    html = client.get(reverse("assistant:chat")).content.decode()
    panel = html[html.index('<div id="content-panel"'):].split(">", 1)[0]
    assert "fit-screen:overflow-hidden" in panel and "short-screen:overflow-y-auto" in panel and "lg:overflow-y-auto" not in panel
    assert 'class="px-4 pt-2 pb-8 sm:px-6 xl:px-8 fit-screen:flex fit-screen:min-h-0 fit-screen:flex-1' in html  # spacing scale (411)
    assert "fit-screen:flex-1" in html and "lg:h-[calc(100vh" not in html
    # Scroll boxes holding sr-only text must be positioned, or that text stretches the page (CLAUDE.md).
    assert 'id="chat-scroll" x-ref="scroll" class="relative ' in html and 'id="chat-list" class="relative ' in html
    other = client.get(reverse("home")).content.decode()
    other_panel = other[other.index('<div id="content-panel"'):].split(">", 1)[0]
    assert "lg:overflow-y-auto" in other_panel and "fit-screen" not in other_panel
    assert '<main id="main" class="px-4 pt-2 pb-8 sm:px-6 xl:px-8"' in other
