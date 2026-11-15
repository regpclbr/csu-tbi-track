"""Phase F5: the AI Assistant page (/assistant/). The answering rules are tested with the services; these tests check
that the page asks through services.ask, shows answers ESCAPED with only our citation links, shows the sources and the
"answered from" badge, keeps chats private, and shows the service's refusals without saving anything.
Every Gemini call here goes to the FakeGemini of the root conftest (never the real API)."""

from datetime import datetime

import pytest
from django.test import Client
from django.urls import reverse

from assistant import gemini, indexing, pages, services, tools
from assistant.gemini import AssistantUnavailable
from assistant.models import AIChat, AIChatMessage
from assistant.testing import FakeGemini

from .conftest import docx_file

pytestmark = pytest.mark.django_db

HTMX = {"HTTP_HX_REQUEST": "true"}
say, call = FakeGemini.say, FakeGemini.call
EVIL = 'Hi <script>alert("x")</script> <img src=x onerror="alert(1)"> **bold** [1]'


@pytest.fixture
def login():
    def log_in(member) -> Client:
        client = Client()
        client.force_login(member)
        return client

    return log_in


@pytest.fixture
def budget(make_document, fake_gemini):
    """One approved, indexed document about the budget."""
    fake_gemini.add_topics("budget")
    document = make_document(docx_file("The budget is 30,000 pesos."), title="Design v2")
    assert indexing.index_document(document.pk)["status"] == "indexed"
    return document


def cited(fake_gemini, text: str = "The budget is 30,000 pesos [1].") -> None:
    fake_gemini.replies += [call(tools.SEARCH_TOOL, query="budget"), say(text)]


def failing(kind: str, message: str):
    def reply(**_kwargs):
        raise AssistantUnavailable(message, kind)

    return reply


def ask(client, question: str = "What is the budget?", htmx: bool = True, **data):
    return client.post(reverse("assistant:ask"), {"question": question, **data}, **(HTMX if htmx else {}))


# --- The page ----------------------------------------------------------------------------------------------------


def test_empty_chat_shows_three_suggestions_naming_the_next_activity(director, login, activity, clock) -> None:
    clock(datetime(2026, 10, 28, 9, 0))
    html = login(director).get(reverse("assistant:chat")).content.decode()
    assert "is coming soon" not in html and "No chats yet" in html
    assert f"Which documents are missing for {activity.title}?" in html
    assert "Which documents are overdue right now?" in html and "Which documents are waiting for review?" in html
    assert 'id="ask-form"' in html and 'hx-sync="this:drop"' in html and "Stop waiting" in html
    assert f'<option value="{activity.pk}">{activity.title}</option>' in html  # the activity filter


def test_page_needs_a_staff_login(client) -> None:
    assert client.get(reverse("assistant:chat")).status_code == 302
    assert client.post(reverse("assistant:ask"), {"question": "x"}).status_code == 302


def test_a_question_starts_a_chat_and_shows_the_cited_answer(director, login, budget, fake_gemini) -> None:
    cited(fake_gemini)
    response = ask(login(director))
    chat = AIChat.objects.get()
    html = response.content.decode()
    assert response.status_code == 200 and response["HX-Push-Url"] == reverse("assistant:conversation", args=[chat.pk])
    detail = reverse("documents:detail", args=[budget.pk])
    answer = AIChatMessage.objects.get(chat=chat, role="assistant")
    card = f"m{answer.pk}-d{budget.pk}"
    # The marker links to the source card (ASSUMPTIONS 382); the card opens and downloads the document.
    assert f'<a href="#{card}" class="cite" aria-label="Source 1">[1]</a>' in html and f'id="{card}"' in html
    assert f'href="{detail}"' in html and "Design v2" in html and "Activity Design" in html and "Pitching Workshop" in html
    assert f'href="{reverse("documents:download", args=[budget.pk])}"' in html
    assert "From approved documents" in html
    assert "What is the budget?" in html  # the question bubble
    assert 'id="chat-list" ' in html and 'hx-swap-oob="true"' in html and f'value="{chat.pk}"' in html
    assert 'id="answer-live" hx-swap-oob="innerHTML">The assistant answered: The budget is 30,000 pesos [1].' in html
    assert chat.title == "What is the budget?"


def test_the_next_question_goes_to_the_same_chat(director, login, budget, fake_gemini) -> None:
    client = login(director)
    cited(fake_gemini)
    ask(client)
    chat = AIChat.objects.get()
    cited(fake_gemini, "Still 30,000 pesos [1].")
    response = ask(client, "And again?", chat=chat.pk)
    assert "HX-Push-Url" not in response and 'id="chat-list"' not in response.content.decode()
    assert AIChat.objects.count() == 1 and chat.messages.count() == 4


def test_without_javascript_a_question_redirects_to_its_chat(director, login, budget, fake_gemini) -> None:
    cited(fake_gemini)
    client = login(director)
    response = ask(client, htmx=False)
    chat = AIChat.objects.get()
    assert response.status_code == 302 and response["Location"] == reverse("assistant:conversation", args=[chat.pk])
    html = client.get(response["Location"]).content.decode()
    assert "The budget is 30,000 pesos" in html and 'aria-current="page"' in html and "Which documents are overdue" not in html
    assert 'id="django-messages"' not in html  # the chat never leaks into the toasts (Django's "messages")


# --- SECURITY: the answer is untrusted text -------------------------------------------------------------------------


def test_html_in_an_answer_is_shown_as_text(director, login, budget, fake_gemini) -> None:
    cited(fake_gemini, EVIL)
    client = login(director)
    answer_html = ask(client).content.decode()
    chat = AIChat.objects.get()
    saved = AIChatMessage.objects.get(role=AIChatMessage.Role.ASSISTANT)
    assert saved.content == EVIL  # the service kept the citation, so the page must make it safe
    page_html = client.get(reverse("assistant:conversation", args=[chat.pk])).content.decode()
    for html in (answer_html, page_html):
        assert "<script>alert" not in html and "<img src=x" not in html
        assert "&lt;script&gt;alert(&quot;x&quot;)&lt;/script&gt;" in html
        assert "&lt;img src=x onerror=&quot;alert(1)&quot;&gt;" in html
        assert "<strong>bold</strong>" in html and "**bold**" not in html  # only the small Markdown subset (ASSUMPTIONS 376)
        assert 'class="cite" aria-label="Source 1">[1]</a>' in html  # only our own link


def test_answer_html_links_only_saved_sources_and_keeps_line_breaks() -> None:
    html = pages.answer_html("A [1]\nB [2] <b>x</b> [9]", {1: "/documents/5/", 2: "/documents/6/"})
    assert html == ('A <a href="/documents/5/" class="cite" aria-label="Source 1">[1]</a><br>'
                    'B <a href="/documents/6/" class="cite" aria-label="Source 2">[2]</a> &lt;b&gt;x&lt;/b&gt; [9]')


def test_the_question_is_escaped_too(director, login, fake_gemini) -> None:
    fake_gemini.replies.append(call("documents_needing_attention"))
    fake_gemini.replies.append(say("Nothing is overdue."))
    html = ask(login(director), "<b>bold?</b>").content.decode()
    assert "&lt;b&gt;bold?&lt;/b&gt;" in html and "<b>bold?</b>" not in html
    assert "From the system&#x27;s records" in html  # the records badge


# --- Refusals: nothing is saved -----------------------------------------------------------------------------------


@pytest.mark.parametrize(("kind", "message", "title"), [
    ("not_configured", gemini.MISSING_KEY, "The AI assistant is not set up"),
    ("rate_limit", gemini.RATE_LIMITED, "The AI service is busy"),
    ("timeout", gemini.TOO_SLOW.format(seconds=90), "The assistant took too long"),
])
def test_ai_unavailable_shows_a_card_and_saves_nothing(director, login, fake_gemini, kind, message, title) -> None:
    fake_gemini.replies.append(failing(kind, message))
    response = ask(login(director))
    html = response.content.decode()
    assert response.status_code == 200 and response["HX-Retarget"] == "#ask-error"
    assert title in html and message in html and pages.ERROR_HINTS[kind] in html
    assert not AIChat.objects.exists() and not AIChatMessage.objects.exists()  # the new, empty chat was removed


def test_without_javascript_the_unavailable_card_is_a_503_page(director, login, fake_gemini) -> None:
    fake_gemini.replies.append(failing("unavailable", gemini.UNAVAILABLE))
    response = ask(login(director), "Keep my words", htmx=False)
    html = response.content.decode()
    assert response.status_code == 503 and gemini.UNAVAILABLE in html and "Keep my words</textarea>" in html


def test_a_failed_question_in_an_existing_chat_keeps_the_chat(director, login, budget, fake_gemini) -> None:
    client = login(director)
    cited(fake_gemini)
    ask(client)
    chat = AIChat.objects.get()
    fake_gemini.replies.append(failing("rate_limit", gemini.RATE_LIMITED))
    ask(client, "Second?", chat=chat.pk)
    assert AIChat.objects.filter(pk=chat.pk).exists() and chat.messages.count() == 2


def test_an_empty_question_is_refused_under_the_box(director, login) -> None:
    response = ask(login(director), "   ", htmx=False)
    assert response.status_code == 400 and 'id="ask-question-error"' in response.content.decode()
    assert "Type a question." in response.content.decode() and not AIChat.objects.exists()


def test_an_unknown_activity_is_refused(director, login) -> None:
    html = ask(login(director), activity="999999").content.decode()
    assert "Unknown activity." in html and not AIChat.objects.exists()


# --- Privacy ------------------------------------------------------------------------------------------------------


def test_another_members_chat_is_404(director, make_member, login) -> None:
    other = make_member("Admin Staff")
    chat = services.create_chat(other)
    client = login(director)
    assert client.get(reverse("assistant:conversation", args=[chat.pk])).status_code == 404
    assert ask(client, chat=chat.pk).status_code == 404
    assert AIChat.objects.filter(pk=chat.pk).exists()


def test_my_chats_list_only_mine(director, make_member, login) -> None:
    other = make_member("Admin Staff")
    services.create_chat(other).messages.create(role="user", content="x")
    AIChat.objects.filter(member=other).update(title="Someone else's chat")
    mine = services.create_chat(director)
    AIChat.objects.filter(pk=mine.pk).update(title="My own chat")
    html = login(director).get(reverse("assistant:chat")).content.decode()
    assert "My own chat" in html and "Someone else" not in html


def test_my_chats_panel_can_be_hidden_with_accessible_toggles(director, login) -> None:
    """ASSUMPTIONS 326: real buttons with aria-controls/aria-expanded, a New chat link when closed, and the choice is
    remembered only in this browser (localStorage inside try/catch, default open)."""
    html = login(director).get(reverse("assistant:chat")).content.decode()
    assert '<nav id="my-chats-panel"' in html and 'x-show="chatsOpen"' in html
    for button_id, label in (("chats-hide", 'aria-label="Hide my chats"'), ("chats-show", "</svg> Chats</button>")):
        button = html[html.index(f'<button type="button" id="{button_id}"'):]
        button = button[:button.index("</button>") + len("</button>")]
        assert 'aria-controls="my-chats-panel"' in button and 'aria-expanded="true"' in button
        assert ':aria-expanded="chatsOpen.toString()"' in button and label in " ".join(button.split())
    assert 'aria-label="New chat"' in html  # New chat stays reachable when the panel is closed
    assert "try { this.chatsOpen = localStorage.getItem('tbi.assistant.chats')" in html
    assert "lg:grid-cols-[15rem_minmax(0,1fr)]" in html and "'lg:col-span-2': !chatsOpen" in html


def test_discard_if_empty_never_removes_a_chat_with_messages_or_someone_elses(director, make_member) -> None:
    other = make_member("Admin Staff")
    empty, theirs = services.create_chat(director), services.create_chat(other)
    used = services.create_chat(director)
    used.messages.create(role="user", content="Hello")
    assert services.discard_if_empty(director, used) is False and services.discard_if_empty(director, theirs) is False
    assert services.discard_if_empty(director, empty) is True
    assert set(AIChat.objects.values_list("pk", flat=True)) == {used.pk, theirs.pk}
