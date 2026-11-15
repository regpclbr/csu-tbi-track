"""Display helpers for the AI Assistant page (phase F5). Display only: answering, citations and "where an answer comes
from" are decided in assistant.services; this module turns saved messages into what the templates show.

SECURITY: an answer is text written by the model, so it is untrusted. answer_html() escapes ALL of it first and only
then turns a small, fixed set of Markdown into our own tags (ASSUMPTIONS 376): bold, italics, "#" headings shown as a
bold line, bullet and numbered lists, line breaks, and links for the citation markers [n] that match a saved source.
Everything else (HTML, links, images, tables) stays escaped text: the patterns below only ever wrap escaped text in
<strong>, <em>, <ul>, <ol>, <li>, <br> and our own <a class="cite">, so no attribute or URL from the model can appear.
"""

import re
from datetime import datetime

from django.db.models import Prefetch
from django.urls import reverse
from django.utils.html import escape, format_html
from django.utils.safestring import SafeString, mark_safe

from activities.models import Activity
from reports import metrics

from . import services
from .models import AIChat, AIChatMessage, AIMessageSource

CITATION = re.compile(r"\[(\d+)\]")

# How each "answered from" value is shown (services.answered_from): label and badge tone.
FROM_BADGES = {
    services.DOCUMENTS: ("From approved documents", "info"),
    services.RECORDS: ("From the system's records", "success"),
    services.BOTH: ("From documents and records", "info"),
    services.NOT_FOUND_KIND: ("Not found", "neutral"),
}

# A friendly hint under the service's own message, per AssistantUnavailable.kind.
ERROR_HINTS = {
    "not_configured": "The System Administrator must add the Gemini API key to .env and restart the server.",
    "rejected": "The System Administrator must check the Gemini API key in .env.",
    "model_unavailable": "The System Administrator can run python manage.py check_gemini to see the models this key can use.",
    "rate_limit": "Wait a minute, then send your question again. It was not saved.",
    "timeout": "Try a shorter or more specific question, or choose an activity. It was not saved.",
    "unavailable": "Try again in a few minutes. Your question was not saved.",
}

SOURCES = Prefetch(
    "sources",
    queryset=AIMessageSource.objects.select_related("document__document_type", "document__activity").order_by("message_source_id"),
)


# The Markdown subset (ASSUMPTIONS 376). Applied to ESCAPED text, one line at a time. Inline marks never contain "<"
# or ">", so they never cross a tag we added.
BULLET = re.compile(r"^\s*[*\-•]\s+(.+)$")  # "* item", "- item", "• item"
NUMBERED = re.compile(r"^\s*(\d{1,3})[.)]\s+(.+)$")  # "1. item", "2) item"
HEADING = re.compile(r"^\s{0,3}#{1,3}\s+(.+?)(?:\s+#+)?\s*$")  # "# x", "## x", "### x" -> a bold line
BOLD_ITALIC = re.compile(r"\*\*\*(?=\S)([^<>*]+?)(?<=\S)\*\*\*")
BOLD = re.compile(r"\*\*(?=\S)([^<>]+?)(?<=\S)\*\*")
# Italics only around words: "2 * 3 * 4" and "a*b*c" stay as they are.
ITALIC_STAR = re.compile(r"(?<![\w*])\*(?=[^\s*])([^<>*]+?)(?<=[^\s*])\*(?![\w*])")
# Underscores only at word boundaries: certificate_text_id and file_name_v2.pdf stay as they are.
ITALIC_UNDERSCORE = re.compile(r"(?<![\w_])_(?=[^\s_])([^<>_]+?)(?<=[^\s_])_(?![\w_])")


def _inline(text: str, plain: bool = False) -> str:
    """Bold and italics in one line. plain=True removes the marks instead (the screen-reader text)."""
    text = BOLD_ITALIC.sub(r"\1" if plain else r"<strong><em>\1</em></strong>", text)
    text = BOLD.sub(r"\1" if plain else r"<strong>\1</strong>", text)
    text = ITALIC_STAR.sub(r"\1" if plain else r"<em>\1</em>", text)
    return ITALIC_UNDERSCORE.sub(r"\1" if plain else r"<em>\1</em>", text)


def _blocks(escaped: str) -> str:
    """Escaped text -> lines joined with <br>, runs of list lines as <ul>/<ol>, headings as a bold line. Blank lines
    next to a list are dropped (the list has its own spacing); other blank lines stay (a paragraph gap)."""
    blocks: list[list] = []  # ["text", html] or ["ul" | "ol", [items], first number]
    for line in escaped.replace("\r\n", "\n").replace("\r", "\n").split("\n"):
        bullet, numbered = BULLET.match(line), NUMBERED.match(line)
        if bullet or numbered:
            kind = "ul" if bullet else "ol"
            item = bullet.group(1) if bullet else numbered.group(2)
            while blocks and blocks[-1] == ["text", ""]:
                blocks.pop()
            if blocks and blocks[-1][0] == kind:
                blocks[-1][1].append(item)
            else:
                blocks.append([kind, [item], 1 if bullet else int(numbered.group(1))])
            continue
        if not line.strip() and blocks and blocks[-1][0] != "text":
            continue
        heading = HEADING.match(line)
        blocks.append(["text", f"<strong>{_inline(heading.group(1))}</strong>" if heading else _inline(line)])

    html, previous = [], None
    for block in blocks:
        if block[0] == "text":
            if previous == "text":
                html.append("<br>")
            html.append(block[1])
        else:
            items = "".join(f"<li>{_inline(item)}</li>" for item in block[1])
            start = f' start="{block[2]}"' if block[0] == "ol" and block[2] != 1 else ""
            html.append(f"<{block[0]}{start}>{items}</{block[0]}>")
        previous = block[0]
    return "".join(html)


def answer_html(content: str, links: dict[int, str]) -> SafeString:
    """The model's text, escaped, with the small Markdown subset (_blocks) and with [n] linked to links[n] (the n-th
    saved source). A marker without a saved source stays plain text."""
    escaped = escape(content)  # the model's text: nothing in it is ever treated as HTML

    def link(match: re.Match) -> str:
        number = int(match.group(1))
        if number not in links:
            return match.group(0)
        return format_html('<a href="{}" class="cite" aria-label="Source {}">[{}]</a>', links[number], number, number)

    # The escaped text has no "<"; _blocks adds only <strong>/<em>/<ul>/<ol>/<li>/<br>, then only our own links.
    return mark_safe(CITATION.sub(link, _blocks(escaped)))


def answer_text(content: str) -> str:
    """The answer as plain text for the live region (screen readers): the Markdown marks removed, no HTML (the
    template escapes it)."""
    lines = []
    for line in content.splitlines():
        heading = HEADING.match(line)
        line = BULLET.sub(r"\1", heading.group(1) if heading else line)
        lines.append(_inline(line, plain=True))
    return "\n".join(lines)


def source_chip(number: int, source: AIMessageSource) -> dict:
    document = source.document
    return {
        "number": number,
        "title": document.title,
        "type": document.document_type.name,
        "activity": document.activity.title,
        "url": reverse("documents:detail", args=[document.pk]),
    }


def message_view(message: AIChatMessage) -> dict:
    """One saved message as the chat shows it. Sources must be prefetched (SOURCES)."""
    if message.role != AIChatMessage.Role.ASSISTANT:
        return {"message": message, "is_answer": False}
    chips = [source_chip(number, source) for number, source in enumerate(message.sources.all(), start=1)]
    kind = services.answered_from(message)
    label, tone = FROM_BADGES[kind]
    return {
        "message": message,
        "is_answer": True,
        "html": answer_html(message.content, {chip["number"]: chip["url"] for chip in chips}),
        "text": answer_text(message.content),
        "sources": chips,
        "badge": {"label": label, "tone": tone, "kind": kind},
    }


def conversation(chat: AIChat) -> list[dict]:
    messages = chat.messages.order_by("created_at", "message_id").prefetch_related(SOURCES)
    return [message_view(message) for message in messages]


def exchange(answer: services.Answer) -> list[dict]:
    """The question and the answer that services.ask() just saved."""
    messages = (
        AIChatMessage.objects.filter(chat=answer.message.chat_id, message_id__lte=answer.message.pk)
        .order_by("-message_id").prefetch_related(SOURCES)[:2]
    )
    return [message_view(message) for message in reversed(messages)]


def activity_choices() -> list[Activity]:
    """The activity filter: every activity, the newest first."""
    return list(Activity.objects.order_by("-start_date", "title").only("pk", "title", "start_date"))


def suggestions(now: datetime | None = None) -> list[str]:
    """Three questions for an empty chat. The first names the next Scheduled activity (reports.metrics, S2)."""
    upcoming = metrics.upcoming_activities(metrics.default_filters(now), now)["next"]
    first = (f"Which documents are missing for {upcoming[0]['title']}?" if upcoming
             else "Which required documents are missing for our activities?")
    return [first, "Which documents are overdue right now?", "Which documents are waiting for review?"]
