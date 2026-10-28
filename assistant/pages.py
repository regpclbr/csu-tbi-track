"""Display helpers for the AI Assistant page (phase F5). Display only: answering, citations and "where an answer comes
from" are decided in assistant.services; this module turns saved messages into what the templates show.

SECURITY: an answer is text written by the model, so it is untrusted. answer_html() escapes ALL of it first and only
then adds two things of our own: links for the citation markers [n] that match a saved source, and <br> for line
breaks. No HTML or Markdown from the model is ever rendered.
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
    "not_configured": "An Admin must add the Gemini API key to .env and restart the server.",
    "rejected": "An Admin must check the Gemini API key in .env.",
    "model_unavailable": "An Admin can run python manage.py check_gemini to see the models this key can use.",
    "rate_limit": "Wait a minute, then send your question again. It was not saved.",
    "timeout": "Try a shorter or more specific question, or choose an activity. It was not saved.",
    "unavailable": "Try again in a few minutes. Your question was not saved.",
}

SOURCES = Prefetch(
    "sources",
    queryset=AIMessageSource.objects.select_related("document__document_type", "document__activity").order_by("message_source_id"),
)


def answer_html(content: str, links: dict[int, str]) -> SafeString:
    """The model's text, escaped, with line breaks and with [n] linked to links[n] (the n-th saved source). A marker
    without a saved source stays plain text."""
    escaped = escape(content)  # the model's text: nothing in it is ever treated as HTML

    def link(match: re.Match) -> str:
        number = int(match.group(1))
        if number not in links:
            return match.group(0)
        return format_html('<a href="{}" class="cite" aria-label="Source {}">[{}]</a>', links[number], number, number)

    linked = CITATION.sub(link, escaped)  # escaped text has no "<", so only our own links are added
    return mark_safe(linked.replace("\r\n", "\n").replace("\n", "<br>"))


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
