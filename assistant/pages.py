"""Display helpers for the AI Assistant page (phase F5). Display only: answering, citations and "where an answer comes
from" are decided in assistant.services; this module turns saved messages into what the templates show.

SECURITY: an answer is text written by the model, so it is untrusted. answer_html() escapes ALL of it first and only
then turns a small, fixed set of Markdown into our own tags (ASSUMPTIONS 376): bold, italics, "#" headings shown as a
bold line, bullet and numbered lists, line breaks, and links for the citation markers [n] that match a saved source.
Everything else (HTML, links, images, tables) stays escaped text: the patterns below only ever wrap escaped text in
<strong>, <em>, <ul>, <ol>, <li>, <br> and our own <a class="cite">, so no attribute or URL from the model can appear.
"""

import re
from dataclasses import dataclass, field
from datetime import date, datetime
from urllib.parse import urlencode

from django.db.models import Prefetch
from django.urls import reverse
from django.utils import timezone
from django.utils.html import escape, format_html
from django.utils.safestring import SafeString, mark_safe

from activities.models import Activity
from config.ui_tags import date_span
from documents import pages as document_pages
from documents import services as document_services
from documents.models import Document
from reports import metrics

from . import indexing, services, tools
from .models import AIChat, AIChatMessage, AIMessageSource

CITATION = re.compile(r"\[(\d+)\]")

# How each "answered from" value is shown (services.answered_from): label and badge tone.
FROM_BADGES = {
    services.DOCUMENTS: ("From the files", "info"),
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
    queryset=AIMessageSource.objects.select_related(
        "document__document_type", "document__activity", "document__status", "generated_report",
        "submission__activity", "submission__document_type",
    ).order_by("message_source_id"),
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


# --- Markers written by our code (assistant.listings, ASSUMPTIONS 381-383) -----------------------------------------
# Only digits and dates are read from them; every URL is built here with reverse(), and only for rows that exist.
ACTIVITY_MARKER = re.compile(r"\[activity:(\d{1,18})\]")
FILE_MARKER = re.compile(r"\[file:(\d{1,18})\]")
ACTIVITIES_LIST_MARKER = re.compile(r"\[activities-list:(\d{4}-\d{2}-\d{2}|-):(\d{4}-\d{2}-\d{2}|-):(\d{1,9}):(\d{1,9})\]")
DOCUMENTS_LIST_MARKER = re.compile(r"\[documents-list:(\d{1,18}):(\d{1,9})\]")
ANY_MARKER = re.compile("|".join(p.pattern for p in (ACTIVITY_MARKER, FILE_MARKER, ACTIVITIES_LIST_MARKER, DOCUMENTS_LIST_MARKER)))


@dataclass
class Lookup:
    """The activities and documents the markers of some messages name (two queries for a whole conversation)."""

    activities: dict[int, Activity] = field(default_factory=dict)
    documents: dict[int, Document] = field(default_factory=dict)


def lookup_for(messages) -> Lookup:
    activity_ids, document_ids = set(), set()
    for message in messages:
        if message.role == AIChatMessage.Role.ASSISTANT:
            activity_ids.update(int(n) for n in ACTIVITY_MARKER.findall(message.content))
            document_ids.update(int(n) for n in FILE_MARKER.findall(message.content))
    activities = Activity.objects.filter(pk__in=activity_ids).only("pk", "title", "start_date", "end_date") if activity_ids else []
    # The Documents page's rows only (activity documents; never certificate evidence), whatever the review state.
    documents = document_services.documents_for_listing().filter(pk__in=document_ids) if document_ids else []
    return Lookup({a.pk: a for a in activities}, {d.pk: d for d in documents})


def _activities_list_url(match: re.Match) -> str | None:
    params = {}
    for key, value in (("date_from", match.group(1)), ("date_to", match.group(2))):
        if value != "-":
            try:
                params[key] = date.fromisoformat(value).isoformat()
            except ValueError:
                return None
    if int(match.group(3)):
        params["status"] = int(match.group(3))
    if int(match.group(4)):
        params["activity_type"] = int(match.group(4))
    params["sort"] = "start_date"
    return f"{reverse('activities:list')}?{urlencode(params)}"


def _documents_list_url(match: re.Match) -> str:
    params = {"view": "all"}
    if int(match.group(1)):
        params["activity"] = int(match.group(1))
    if int(match.group(2)):
        params["document_type"] = int(match.group(2))
    return f"{reverse('documents:list')}?{urlencode(params)}"


def answer_html(content: str, links: dict[int, str], lookup: Lookup | None = None) -> SafeString:
    """The model's text, escaped, with the small Markdown subset (_blocks) and with [n] linked to links[n] (the n-th
    saved source). A marker without a saved source stays plain text. Our own markers (listings) become links only
    for rows in `lookup`; an activity that no longer exists says so in plain words (ACTIVITY_GONE). [file:n] never becomes a link here (cards show files)."""
    lookup = lookup or Lookup()
    escaped = escape(content)  # the model's text: nothing in it is ever treated as HTML

    def link(match: re.Match) -> str:
        number = int(match.group(1))
        if number not in links:
            return match.group(0)
        return format_html('<a href="{}" class="cite" aria-label="Source {}">[{}]</a>', links[number], number, number)

    def activity_link(match: re.Match) -> str:
        activity = lookup.activities.get(int(match.group(1)))
        if activity is None:  # deleted since the answer (ASSUMPTIONS 389): plain words, never a link
            return format_html('<span class="text-muted">{}</span>', ACTIVITY_GONE)
        return format_html('<a href="{}" class="cite" aria-label="Open the activity page: {}">(open)</a>',
                           reverse("activities:detail", args=[activity.pk]), activity.title)

    def activities_list_link(match: re.Match) -> str:
        url = _activities_list_url(match)
        return format_html('<a href="{}" class="cite">Open the Activities list</a>', url) if url else ""

    def documents_list_link(match: re.Match) -> str:
        return format_html('<a href="{}" class="cite">Open the Documents page</a>', _documents_list_url(match))

    # The escaped text has no "<"; _blocks adds only <strong>/<em>/<ul>/<ol>/<li>/<br>, then only our own links.
    html = CITATION.sub(link, _blocks(escaped))
    html = ACTIVITY_MARKER.sub(activity_link, html)
    html = ACTIVITIES_LIST_MARKER.sub(activities_list_link, html)
    html = DOCUMENTS_LIST_MARKER.sub(documents_list_link, html)
    html = FILE_MARKER.sub("", html)
    return mark_safe(html)


def answer_text(content: str) -> str:
    """The answer as plain text for the live region (screen readers): the Markdown marks and our markers removed, no
    HTML (the template escapes it)."""
    lines = []
    for line in ANY_MARKER.sub("", content).splitlines():
        heading = HEADING.match(line)
        line = BULLET.sub(r"\1", heading.group(1) if heading else line)
        lines.append(_inline(line, plain=True))
    return "\n".join(lines)


def file_card(message: AIChatMessage, document: Document | None, document_id: int, numbers: list[int] | None = None) -> dict:
    """One file card (ASSUMPTIONS 382, 383), built from the database only. Open = the document page; Download = the
    permission-checked download view (never /media/). A file deleted since the answer: "no longer available"."""
    dom_id = f"m{message.pk}-d{document_id}"
    if document is None:
        return {"dom_id": dom_id, "missing": True, "numbers": numbers or []}
    activity = document.activity
    label, tone = tools.source_state(indexing.DOCUMENT, document)
    return {
        "dom_id": dom_id,
        "missing": False,
        "numbers": numbers if numbers is not None else [],
        "title": document.title,
        "type": document.document_type.name,
        "activity": activity.title,
        "activity_url": reverse("activities:detail", args=[activity.pk]),
        "badge": {"label": label, "tone": tone},
        "date_label": "Uploaded",
        "uploaded_on": timezone.localtime(document.uploaded_at).date(),
        "open_url": reverse("documents:detail", args=[document.pk]),
        "download_url": reverse("documents:download", args=[document.pk]),
    }


def report_card(message: AIChatMessage, report, numbers: list[int]) -> dict:
    """A cited generated report (ASSUMPTIONS 438): any staff member may download it (reports:download)."""
    from reports.services import download_name

    label, tone = tools.source_state(indexing.REPORT, report)
    return {
        "dom_id": f"m{message.pk}-r{report.pk}", "missing": False, "numbers": numbers,
        "title": download_name(report), "type": "Report", "activity": "", "activity_url": "",
        "badge": {"label": label, "tone": tone}, "date_label": "Generated",
        "uploaded_on": timezone.localtime(report.generated_at).date(),
        "open_url": "", "download_url": reverse("reports:download", args=[report.pk]),
    }


def submission_card(message: AIChatMessage, submission, numbers: list[int]) -> dict:
    """A cited submission (ASSUMPTIONS 438): its page and file check who may open it (404 for anyone else)."""
    label, tone = tools.source_state(indexing.SUBMISSION, submission)
    activity = submission.activity
    which = "signed" if submission.signed_file_path else "original"
    return {
        "dom_id": f"m{message.pk}-s{submission.pk}", "missing": False, "numbers": numbers,
        "title": submission.title,
        "type": submission.document_type.name if submission.document_type_id else "Submission",
        "activity": activity.title if activity else "",
        "activity_url": reverse("activities:detail", args=[activity.pk]) if activity else "",
        "badge": {"label": label, "tone": tone}, "date_label": "Submitted",
        "uploaded_on": timezone.localtime(submission.submitted_at).date(),
        "open_url": reverse("approvals:detail", args=[submission.pk]),
        "download_url": reverse("approvals:file", args=[submission.pk, which]),
    }


def source_cards(message: AIChatMessage) -> tuple[list[dict], dict[int, str]]:
    """The cited files of a document-based answer: one card per file (never one per excerpt), in citation order, each
    with its citation numbers and its state (ASSUMPTIONS 438: documents in any review state, generated reports,
    submissions); and where each [n] links to (its card)."""
    cards: dict[tuple[str, int], dict] = {}
    links: dict[int, str] = {}
    for number, source in enumerate(message.sources.all(), start=1):
        kind, item = tools.source_of(source)
        key = (kind, item.pk)
        if key not in cards:
            if kind == indexing.DOCUMENT:
                cards[key] = file_card(message, item, item.pk, [])
            elif kind == indexing.REPORT:
                cards[key] = report_card(message, item, [])
            else:
                cards[key] = submission_card(message, item, [])
        cards[key]["numbers"].append(number)
        links[number] = f"#{cards[key]['dom_id']}"
    return list(cards.values()), links


ACTIVITY_GONE = "(activity no longer available)"


def _activity_heading(activity: Activity | None) -> dict:
    if activity is None:  # deleted since the answer (ASSUMPTIONS 389)
        return {"missing": True}
    return {"title": activity.title, "url": reverse("activities:detail", args=[activity.pk]), "dates": date_span(activity)}


def file_groups(message: AIChatMessage, lookup: Lookup) -> tuple[str, list[dict], str]:
    """A find-files answer split into (text before the files, groups of file cards by activity, text after). A line
    with [file:n] becomes a card; a line with [activity:n] right before file lines becomes its group's heading (the
    title and dates come from the database, not from the text)."""
    lines = message.content.splitlines()
    before, after, groups = [], [], []

    def next_line_is_file(index: int) -> bool:
        following = next((line for line in lines[index + 1:] if line.strip()), "")
        return bool(FILE_MARKER.search(following))

    for index, line in enumerate(lines):
        file_ids = [int(n) for n in FILE_MARKER.findall(line)]
        heading = ACTIVITY_MARKER.search(line)
        if file_ids:
            if not groups:
                groups.append({"activity": None, "cards": []})
            groups[-1]["cards"] += [file_card(message, lookup.documents.get(n), n) for n in file_ids]
        elif heading and next_line_is_file(index):
            groups.append({"activity": _activity_heading(lookup.activities.get(int(heading.group(1)))), "cards": []})
        elif not line.strip() and groups and not after:
            continue  # the blank lines between groups
        else:
            (after if groups else before).append(line)
    return "\n".join(before).strip(), groups, "\n".join(after).strip()


def message_view(message: AIChatMessage, lookup: Lookup | None = None) -> dict:
    """One saved message as the chat shows it. Sources must be prefetched (SOURCES); `lookup` names the rows of our
    markers (lookup_for)."""
    if message.role != AIChatMessage.Role.ASSISTANT:
        return {"message": message, "is_answer": False}
    lookup = lookup or lookup_for([message])
    kind = services.answered_from(message)
    label, tone = FROM_BADGES[kind]
    cards, links = source_cards(message)
    groups, after = [], ""
    text = message.content
    if cards:
        groups = [{"activity": None, "cards": cards}]
    elif FILE_MARKER.search(message.content):
        text, groups, after = file_groups(message, lookup)
    return {
        "message": message,
        "is_answer": True,
        "html": answer_html(text, links, lookup),
        "after_html": answer_html(after, links, lookup) if after else "",
        "text": answer_text(message.content),
        "file_groups": groups,
        "files_heading": "Sources" if cards else "Files",
        "badge": {"label": label, "tone": tone, "kind": kind},
    }


def conversation(chat: AIChat) -> list[dict]:
    messages = list(chat.messages.order_by("created_at", "message_id").prefetch_related(SOURCES))
    lookup = lookup_for(messages)
    return [message_view(message, lookup) for message in messages]


def exchange(answer: services.Answer) -> list[dict]:
    """The question and the answer that services.ask() just saved."""
    messages = list(
        AIChatMessage.objects.filter(chat=answer.message.chat_id, message_id__lte=answer.message.pk)
        .order_by("-message_id").prefetch_related(SOURCES)[:2]
    )
    lookup = lookup_for(messages)
    return [message_view(message, lookup) for message in reversed(messages)]


def activity_choices() -> list[Activity]:
    """The activity filter: every activity, the newest first."""
    return list(Activity.objects.order_by("-start_date", "title").only("pk", "title", "start_date"))


def suggestions(now: datetime | None = None) -> list[str]:
    """Three questions for an empty chat. The first names the next Scheduled activity (reports.metrics, S2)."""
    upcoming = metrics.upcoming_activities(metrics.default_filters(now), now)["next"]
    first = (f"Which documents are missing for {upcoming[0]['title']}?" if upcoming
             else "Which required documents are missing for our activities?")
    return [first, "Which documents are overdue right now?", "Which required documents are missing?"]
