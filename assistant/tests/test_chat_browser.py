"""The AI Assistant's keyboard and scrolling (ASSUMPTIONS 374-375), checked in a real browser (Playwright + Chromium).

Opt-in, like the modal test:
    $env:BROWSER_TESTS = "1"; pytest -n 0 assistant/tests/test_chat_browser.py; Remove-Item Env:BROWSER_TESTS
Answers come from the FakeGemini of the root conftest (never the real API). Checks (also the compact chat bar, ASSUMPTIONS 441, at 1366 and 390 px): the chat opens at the bottom; Send is
disabled while the box is empty and Enter sends nothing; Shift + Enter adds a new line; Enter sends; a reader who
scrolled up is not pulled down when the answer arrives and sees "Jump to latest", which scrolls to the bottom.
"""

import os

import pytest
from django.conf import settings as django_settings
from django.test import Client
from django.urls import reverse

from assistant.models import AIChat, AIChatMessage
from assistant.testing import FakeGemini

pytestmark = [
    pytest.mark.browser,
    pytest.mark.skipif(os.environ.get("BROWSER_TESTS") != "1", reason="browser test: set BROWSER_TESTS=1"),
]

AT_BOTTOM = "() => { const el = document.getElementById('chat-scroll'); return el.scrollHeight - el.scrollTop - el.clientHeight < 80 }"


@pytest.mark.django_db(transaction=True)
def test_enter_sends_and_the_chat_scrolls_politely(live_server, director, fake_gemini, monkeypatch) -> None:
    from playwright.sync_api import sync_playwright

    chat = AIChat.objects.create(member=director, title="A long chat")
    for number in range(30):  # long enough to scroll
        chat.messages.create(role=AIChatMessage.Role.USER, content=f"Question {number}")
        chat.messages.create(role=AIChatMessage.Role.ASSISTANT, content=f"Answer {number}\nwith a second line.")
    fake_gemini.replies += [FakeGemini.call("documents_needing_attention"), FakeGemini.say("First new answer."),
                            FakeGemini.call("documents_needing_attention"), FakeGemini.say("Second new answer.")]
    client = Client()
    client.force_login(director)
    cookie = client.cookies[django_settings.SESSION_COOKIE_NAME].value
    monkeypatch.setenv("DJANGO_ALLOW_ASYNC_UNSAFE", "true")

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        context = browser.new_context(viewport={"width": 1366, "height": 768})
        context.add_cookies([{"name": django_settings.SESSION_COOKIE_NAME, "value": cookie, "url": live_server.url}])
        page = context.new_page()
        page.goto(live_server.url + reverse("assistant:conversation", args=[chat.pk]))
        page.wait_for_timeout(300)
        assert page.evaluate(AT_BOTTOM), "the chat should open at the latest message"

        box, send = page.locator("#ask-question"), page.locator("#ask-send")
        assert send.is_disabled()
        box.press("Enter")  # empty: nothing is sent
        page.wait_for_timeout(300)
        assert chat.messages.count() == 60

        box.type("Line one")
        box.press("Shift+Enter")
        box.type("line two")
        assert box.input_value() == "Line one\nline two" and send.is_enabled()

        box.press("Enter")  # sends; the answer arrives while we are at the bottom: we follow it
        page.wait_for_selector("#messages >> text=First new answer.")
        page.wait_for_timeout(300)
        assert page.evaluate(AT_BOTTOM) and not page.locator("#jump-latest").is_visible()
        assert box.input_value() == ""

        box.type("Another question")
        box.press("Enter")
        page.evaluate("() => { document.getElementById('chat-scroll').scrollTop = 0 }")  # reading older messages
        page.wait_for_selector("#messages >> text=Second new answer.")
        page.wait_for_timeout(300)
        assert page.evaluate("() => document.getElementById('chat-scroll').scrollTop") < 50, "not pulled down"
        assert page.locator("#jump-latest").is_visible()
        page.locator("#jump-latest").click()
        page.wait_for_function(AT_BOTTOM, timeout=5000)  # smooth scrolling (unless reduced motion) takes a moment
        assert not page.locator("#jump-latest").is_visible()
        browser.close()
    assert chat.messages.count() == 64


FIT = """() => {
  const panel = document.getElementById('content-panel'), scroll = document.getElementById('chat-scroll');
  const chats = document.getElementById('my-chats-panel').getBoundingClientRect();
  const card = document.querySelector('section[aria-labelledby=chat-title]').getBoundingClientRect();
  const send = document.getElementById('ask-send').getBoundingClientRect();
  const box = document.getElementById('ask-question').getBoundingClientRect();
  const header = document.querySelector('#content-panel > header').getBoundingClientRect();
  const panelBox = panel.getBoundingClientRect();
  return {
    panelScrolls: panel.scrollHeight > panel.clientHeight + 1,
    windowScrolls: document.documentElement.scrollHeight > innerHeight + 1,
    messagesScroll: scroll.scrollHeight > scroll.clientHeight + 1,
    cardTop: card.top, headerBottom: header.bottom,
    cardGap: panelBox.bottom - card.bottom, chatsGap: panelBox.bottom - chats.bottom,
    sendVisible: send.top >= 0 && send.bottom <= innerHeight, boxVisible: box.top >= header.bottom && box.bottom <= innerHeight,
  };
}"""


@pytest.mark.django_db(transaction=True)
def test_the_page_fits_the_window_and_only_the_messages_scroll(live_server, director, monkeypatch) -> None:
    """ASSUMPTIONS 380: from 1024 x 600 px the panel never scrolls; both cards reach the panel's bottom; the messages and
    the chat list scroll inside; the message box and Send stay visible. Below 600 px high the panel scrolls instead.
    Other pages (the dashboard) keep their normal panel scrolling."""
    from playwright.sync_api import sync_playwright

    chat = AIChat.objects.create(member=director, title="A long chat")
    for number in range(40):
        chat.messages.create(role=AIChatMessage.Role.USER, content=f"Question {number}")
        chat.messages.create(role=AIChatMessage.Role.ASSISTANT, content=f"Answer {number}\nwith a second line.")
        AIChat.objects.create(member=director, title=f"Older chat {number}")  # a chat list long enough to scroll
    client = Client()
    client.force_login(director)
    cookie = client.cookies[django_settings.SESSION_COOKIE_NAME].value
    monkeypatch.setenv("DJANGO_ALLOW_ASYNC_UNSAFE", "true")
    url = live_server.url + reverse("assistant:conversation", args=[chat.pk])

    problems = []
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        for width, height in [(1280, 720), (1366, 768), (1440, 900), (1920, 1080)]:
            context = browser.new_context(viewport={"width": width, "height": height})
            context.add_cookies([{"name": django_settings.SESSION_COOKIE_NAME, "value": cookie, "url": live_server.url}])
            page = context.new_page()
            page.goto(url)
            page.wait_for_timeout(300)
            page.mouse.move(width // 2, height // 2)
            page.mouse.wheel(0, 3000)  # scrolling never moves the cards under the header
            page.wait_for_timeout(300)
            fit = page.evaluate(FIT)
            chat_list = page.evaluate("() => { const el = document.getElementById('chat-list'); return el.scrollHeight > el.clientHeight + 1 }")
            print(f"\n{width}x{height}: {fit} chat list scrolls: {chat_list}")
            if fit["panelScrolls"] or fit["windowScrolls"]:
                problems.append(f"{width}x{height}: the page scrolls")
            if not fit["messagesScroll"] or not chat_list:
                problems.append(f"{width}x{height}: the messages or the chat list do not scroll inside their card")
            if fit["cardTop"] < fit["headerBottom"] - 1:
                problems.append(f"{width}x{height}: the chat card is under the header")
            if not 0 <= fit["cardGap"] <= 32 or abs(fit["cardGap"] - fit["chatsGap"]) > 1:
                problems.append(f"{width}x{height}: the cards do not reach the bottom (gaps {fit['cardGap']}, {fit['chatsGap']})")
            if not (fit["sendVisible"] and fit["boxVisible"]):
                problems.append(f"{width}x{height}: the message box or Send is not visible")
            context.close()

        context = browser.new_context(viewport={"width": 1366, "height": 560})  # short window: normal page scrolling
        context.add_cookies([{"name": django_settings.SESSION_COOKIE_NAME, "value": cookie, "url": live_server.url}])
        page = context.new_page()
        page.goto(url)
        page.wait_for_timeout(300)
        fit = page.evaluate(FIT)
        if not fit["panelScrolls"] or not fit["messagesScroll"]:
            problems.append(f"1366x560: expected the panel AND the messages to scroll: {fit}")
        page.goto(live_server.url + reverse("home"))
        overflow = page.evaluate("() => getComputedStyle(document.getElementById('content-panel')).overflowY")
        if overflow != "auto":
            problems.append(f"the dashboard panel should keep scrolling (overflow-y {overflow})")
        context.close()
        browser.close()
    assert not problems, "\n".join(problems)




BOX = """() => {
  const box = document.getElementById('ask-question'), send = document.getElementById('ask-send');
  const scroll = document.getElementById('chat-scroll');
  const card = document.querySelector('section[aria-labelledby=chat-title]');
  const b = box.getBoundingClientRect(), s = send.getBoundingClientRect();
  const panel = document.getElementById('content-panel');
  const lineHeight = parseFloat(getComputedStyle(box).lineHeight);
  const sideways = document.documentElement.scrollWidth > innerWidth + 1 || panel.scrollWidth > panel.clientWidth + 1;
  return {
    height: b.height, lineHeight, scrollsInside: box.scrollHeight > box.clientHeight + 1,
    conversationShare: scroll.getBoundingClientRect().height / card.getBoundingClientRect().height,
    sendRight: s.left >= b.right - 1 && s.top < b.bottom && s.bottom > b.top,
    sendVisible: s.top >= 0 && s.bottom <= innerHeight && s.right <= innerWidth, sendSize: Math.min(s.width, s.height),
    boxVisible: b.top >= 0 && b.bottom <= innerHeight, sideways,
    pageScrolls: panel.scrollHeight > panel.clientHeight + 1 || document.documentElement.scrollHeight > innerHeight + 1,
    filterInHeader: !!document.querySelector('section[aria-labelledby=chat-title] header #ask-activity'),
  };
}"""


@pytest.mark.django_db(transaction=True)
def test_the_compact_chat_bar_at_1366_and_390(live_server, director, fake_gemini, monkeypatch) -> None:
    """ASSUMPTIONS 441 (corrects 439): the conversation gets most of the chat panel (at least about 70% of its height);
    the typing bar is [box][Send]: the box starts ONE line tall and stops growing at about 5 lines, then scrolls inside;
    Send stays on the right (44 px); the activity filter is in the header; nothing scrolls sideways and the page does
    not scroll; Ctrl + Enter sends; the counter shows near the limit. On a phone (390 px, touch) Enter is a new line
    and My chats starts closed."""
    from playwright.sync_api import sync_playwright

    chat = AIChat.objects.create(member=director, title="A chat")
    for number in range(20):
        chat.messages.create(role=AIChatMessage.Role.USER, content=f"Question {number}")
        chat.messages.create(role=AIChatMessage.Role.ASSISTANT, content=f"Answer {number}")
    fake_gemini.replies += [FakeGemini.call("documents_needing_attention"), FakeGemini.say("Sent with Ctrl Enter.")]
    client = Client()
    client.force_login(director)
    cookie = client.cookies[django_settings.SESSION_COOKIE_NAME].value
    monkeypatch.setenv("DJANGO_ALLOW_ASYNC_UNSAFE", "true")
    url = live_server.url + reverse("assistant:conversation", args=[chat.pk])
    long_text = "\n".join(f"Line {n} of a long question about the activity." for n in range(40))

    problems = []
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        for width, height, phone in [(1366, 768, False), (390, 844, True)]:
            options = {"viewport": {"width": width, "height": height}}
            if phone:
                options.update(is_mobile=True, has_touch=True, device_scale_factor=2)
            context = browser.new_context(**options)
            context.add_cookies([{"name": django_settings.SESSION_COOKIE_NAME, "value": cookie, "url": live_server.url}])
            page = context.new_page()
            page.goto(url)
            page.wait_for_timeout(400)
            label = f"{width}x{height}"
            start = page.evaluate(BOX)
            print(f"\n{label} start: {start}")
            if start["height"] > 1.5 * start["lineHeight"] + 24:
                problems.append(f"{label}: the box should start one line tall ({start['height']} px)")
            if start["conversationShare"] < 0.68:
                problems.append(f"{label}: the conversation should fill most of the panel ({start['conversationShare']:.2f})")
            if not start["sendRight"] or start["sendSize"] < 44:
                problems.append(f"{label}: Send should be on the right of the box, 44 px ({start})")
            if not start["filterInHeader"]:
                problems.append(f"{label}: the activity filter should be in the chat header")
            if start["sideways"] or start["pageScrolls"] or not (start["sendVisible"] and start["boxVisible"]):
                problems.append(f"{label}: something is hidden or the page scrolls: {start}")
            if phone and page.locator("#my-chats-panel").is_visible():
                problems.append(f"{label}: My chats should start closed on a phone")

            box = page.locator("#ask-question")
            box.fill("one\ntwo\nthree")
            page.wait_for_timeout(150)
            three = page.evaluate(BOX)
            box.fill(long_text)
            page.wait_for_timeout(200)
            grown = page.evaluate(BOX)
            print(f"{label} grown: {grown}")
            five_lines = 5 * grown["lineHeight"] + 24
            if not (three["height"] > start["height"] + grown["lineHeight"] and three["height"] < grown["height"]):
                problems.append(f"{label}: the box should grow with the text ({start['height']} -> {three['height']})")
            if not (grown["height"] <= five_lines + 2 and grown["height"] >= five_lines - grown["lineHeight"]):
                problems.append(f"{label}: the box should stop growing at about 5 lines ({grown['height']} px)")
            if not grown["scrollsInside"]:
                problems.append(f"{label}: a long text should scroll inside the box")
            if not grown["sendRight"] or grown["sideways"] or grown["pageScrolls"] or not grown["sendVisible"]:
                problems.append(f"{label}: with a long text something moved, is hidden or the page scrolls: {grown}")
            if grown["conversationShare"] < 0.55:
                problems.append(f"{label}: even at 5 lines the conversation keeps most of the panel ({grown['conversationShare']:.2f})")

            box.fill("x" * 1850)
            page.wait_for_timeout(100)
            if not page.locator("#ask-question-count").is_visible() or "1850 / 2000" not in page.locator("#ask-question-count").inner_text():
                problems.append(f"{label}: the counter should show near the limit")
            box.fill("x" * 100)
            page.wait_for_timeout(100)
            if page.locator("#ask-question-count").is_visible():
                problems.append(f"{label}: the counter should be hidden far from the limit")

            box.fill("First line")
            if phone:
                before = chat.messages.count()  # the 1366 run already sent one question
                box.press("Enter")  # a new line on a phone, never a send
                page.wait_for_timeout(300)
                if box.input_value() != "First line\n" or chat.messages.count() != before:
                    problems.append(f"{label}: Enter should add a new line on a phone")
            else:
                box.press("Control+Enter")
                page.wait_for_selector("#messages >> text=Sent with Ctrl Enter.")
                if box.input_value() != "":
                    problems.append(f"{label}: Ctrl + Enter should send and clear the box")
            context.close()
        browser.close()
    assert not problems, "\n".join(problems)
