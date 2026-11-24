"""Answers built by OUR code from the database, without Gemini (ASSUMPTIONS 381, 383).

- `activities_answer`: the activities of a period (optional status / activity type), earliest first, at most
  MAX_ITEMS listed with "N more". Drafts are left out unless the question asks for them.
- `files_answer`: the files of activities (by title, period, activity type) and/or of a document type, or uploaded in
  a period; grouped by activity, newest activity first; any review state (the Documents page's rule); at most
  MAX_ITEMS with "N more on the Documents page". Nothing found: the period / activity checked and the required
  documents still missing (document_checklist()).

The text holds markers that the page turns into links and cards (assistant.pages, built from the database again):
[activity:<id>], [file:<id>], [activities-list:<from>:<to>:<status id>:<type id>], [documents-list:<activity id>:<type id>]
("-" / 0 = not set). Titles typed by staff are written with their brackets broken, so they never form a marker.
Only activity and document information is used: no participant names, emails or contacts.
"""

import re
from dataclasses import dataclass, field
from datetime import datetime, time, timedelta

from django.utils import timezone

from activities import budget
from activities.formatting import format_money, format_time_range
from activities.models import Activity, ActivityStatus, ActivityType
from activities.services import (
    UPCOMING as UPCOMING_GROUP,
    activities_in_range,
    activity_group,
    apply_automatic_statuses,
    happening_now,
)
from config.ui_tags import date_span
from documents import services as document_services
from documents.models import DocumentType

from .periods import NOW, UPCOMING, Period

MAX_ITEMS = 20
MAX_MISSING_ACTIVITIES = 10
NOT_DRAFT = [ActivityStatus.SCHEDULED, ActivityStatus.ONGOING, ActivityStatus.CONDUCTED]

_MARKER_LIKE = re.compile(r"\[(?=\s*(?:\d+|activity|file|activities-list|documents-list)\b)", re.IGNORECASE)


def safe_text(value: str) -> str:
    """Text typed by staff (a title) written into an answer: a "[" that could start one of our markers or a citation
    becomes "［", and line breaks become spaces, so a title can never create a link, a card or a list line."""
    return _MARKER_LIKE.sub("［", " ".join(str(value).split()))


def _plural(count: int, word: str, plural: str | None = None) -> str:
    return f"{count} {word if count == 1 else (plural or word + 's')}"


@dataclass
class ActivityQuery:
    period: Period | None = None
    status: str | None = None  # an ACTIVITY_STATUS name
    activity_type: ActivityType | None = None
    include_drafts: bool = False
    count: bool = False


@dataclass
class Listing:
    """An answer built by our code: the text (with markers) and how many rows matched (for logs and tests)."""

    text: str
    total: int
    shown: int
    ids: list[int] = field(default_factory=list)


def _statuses(query: ActivityQuery) -> list[str]:
    if query.status:
        return [query.status]
    return NOT_DRAFT + ([ActivityStatus.DRAFT] if query.include_drafts else [])


def _subject(query: ActivityQuery) -> str:
    """"activities" / "Conducted activities" / "Training activities" / "Conducted Training activities"."""
    words = [query.status] if query.status else []
    if query.activity_type:
        words.append(query.activity_type.name)
    return " ".join(words + ["activities"])


def _matching_activities(query: ActivityQuery, now: datetime) -> tuple[list[Activity], int]:
    """(the matching activities in order, drafts left out in the same scope)."""
    period = query.period
    type_id = query.activity_type.pk if query.activity_type else None
    statuses = _statuses(query)
    if period and period.kind == NOW:
        rows = [a for a in happening_now(type_id, now).select_related("status", "activity_type") if a.status.name in statuses]
        return rows, 0  # happening_now() never includes drafts
    if period and period.kind == UPCOMING:
        rows = [a for a in activities_in_range(period.start, None, statuses=statuses, activity_type=type_id)
                if activity_group(a, now) == UPCOMING_GROUP
                or (a.status.name == ActivityStatus.DRAFT and a.start_date > period.start)]
        drafts = 0
        if query.status is None and ActivityStatus.DRAFT not in statuses:  # a chosen status never mentions drafts
            drafts = activities_in_range(period.start + timedelta(days=1), None, statuses=[ActivityStatus.DRAFT],
                                         activity_type=type_id).count()
        return rows, drafts
    start, end = (period.start, period.end) if period else (None, None)
    rows = list(activities_in_range(start, end, statuses=statuses, activity_type=type_id))
    drafts = 0
    if query.status is None and ActivityStatus.DRAFT not in statuses:
        drafts = activities_in_range(start, end, statuses=[ActivityStatus.DRAFT], activity_type=type_id).count()
    return rows, drafts


def _heading(query: ActivityQuery) -> str:
    subject = _subject(query)
    period = query.period
    if period is None:
        return f"{subject[0].upper()}{subject[1:]} (all dates)"
    if period.kind == NOW:
        return f"{subject[0].upper()}{subject[1:]} happening now"
    if period.kind == UPCOMING:
        return f"Upcoming {subject} ({period.label.removeprefix('upcoming ').strip('()')})"
    return f"{subject[0].upper()}{subject[1:]} in {period.label}"


def _activity_line(number: int, activity: Activity) -> str:
    return (f"{number}. **{safe_text(activity.title)}** [activity:{activity.pk}] — {date_span(activity)} · "
            f"{format_time_range(activity.start_time, activity.end_time)} · {activity.status.name} · "
            f"{safe_text(activity.venue)}")


def _list_marker(query: ActivityQuery) -> str:
    period = query.period
    start = period.start.isoformat() if period and period.start else "-"
    end = period.end.isoformat() if period and period.end else "-"
    status_id = ActivityStatus.objects.get(name=query.status).pk if query.status else 0
    type_id = query.activity_type.pk if query.activity_type else 0
    return f"[activities-list:{start}:{end}:{status_id}:{type_id}]"


def activities_answer(query: ActivityQuery, now: datetime | None = None) -> Listing:
    """The activities answer (module docstring). Runs the automatic status moves first, as the Activities list does,
    so the statuses shown are current (never sends emails)."""
    now = now or timezone.now()
    apply_automatic_statuses(now)
    rows, drafts_left_out = _matching_activities(query, now)
    heading = _heading(query)
    drafts_note = (f" (drafts not included: {_plural(drafts_left_out, 'draft')} in this period; ask about drafts to "
                   f"see {'it' if drafts_left_out == 1 else 'them'})") if drafts_left_out else ""
    if not rows:
        what = _subject(query)
        if query.period is None:
            text = f"There are no {what}."
        elif query.period.kind == NOW:
            text = f"No {what} are happening now."
        elif query.period.kind == UPCOMING:
            text = f"There are no upcoming {what} ({query.period.label.removeprefix('upcoming ').strip('()')})."
        else:
            text = f"There are no {what} in {query.period.label}."
        return Listing(text + drafts_note, 0, 0)
    shown = rows[:MAX_ITEMS]
    lines = [f"**{heading}: {len(rows)}**{drafts_note}", ""]
    lines += [_activity_line(number, activity) for number, activity in enumerate(shown, start=1)]
    if len(rows) > len(shown):
        lines += ["", f"Showing the first {len(shown)} of {len(rows)}. {len(rows) - len(shown)} more are in the "
                      f"Activities list {_list_marker(query)}"]
    return Listing("\n".join(lines), len(rows), len(shown), [a.pk for a in shown])


# --- Budget (ASSUMPTIONS 433) -------------------------------------------------------------------------------------

MAX_BUDGETS = 3  # several activities matched: at most this many budgets, then a list of the others


def _budget_block(activity: Activity) -> list[str]:
    summary = budget.budget_summary(activity)
    lines = [f"**Budget of {safe_text(activity.title)}** [activity:{activity.pk}] — {date_span(activity)}",
             f"Fund source: {safe_text(summary['fund_source'])}"
             + (f" · Account / charging: {safe_text(summary['fund_account'])}" if summary["fund_account"] else "")]
    if not summary["rows"]:
        lines.append(f"No budget lines: the budget is {format_money(summary['total'])}.")
        return lines
    lines += [f"- {safe_text(row['particulars'])} ({safe_text(row['category'])}): {row['quantity']} × "
              f"{format_money(row['unit_cost'])} = {format_money(row['total_amount'])}" for row in summary["rows"]]
    if len(summary["category_totals"]) > 1:
        lines.append("Per category: " + "; ".join(f"{safe_text(name)} {format_money(total)}"
                                                  for name, total in summary["category_totals"]))
    lines.append(f"**Total: {format_money(summary['total'])}**")
    return lines


def budget_answer(activity_ids: list[int] | None, scope_activity_id: int | None = None) -> Listing:
    """An activity's budget from the database (budget.budget_summary): fund source, account, the lines, the totals per
    category and the grand total. Which activity: the page's activity filter first, else the activities named in the
    question. Nobody named: asks for the activity; several: the first MAX_BUDGETS, the others listed."""
    ids = [scope_activity_id] if scope_activity_id else (activity_ids or [])
    activities = list(Activity.objects.select_related("fund_source").filter(pk__in=ids).order_by("-start_date", "pk"))
    if not activities:
        if activity_ids == [] and not scope_activity_id:
            return Listing("No activity has that title. Type part of the activity's title, for example "
                           "\"How much is the budget of the Pitching Workshop?\".", 0, 0)
        return Listing("Which activity? Type part of its title, for example \"How much is the budget of the Pitching "
                       "Workshop?\".", 0, 0)
    shown = activities[:MAX_BUDGETS]
    lines: list[str] = []
    for activity in shown:
        if lines:
            lines.append("")
        lines += _budget_block(activity)
    if len(activities) > len(shown):
        lines += ["", f"{len(activities) - len(shown)} more activities match:"]
        lines += [f"- **{safe_text(a.title)}** [activity:{a.pk}]" for a in activities[len(shown):MAX_ITEMS]]
    return Listing("\n".join(lines), len(activities), len(shown), [a.pk for a in shown])


# --- Files --------------------------------------------------------------------------------------------------------


@dataclass
class FileQuery:
    period: Period | None = None
    uploaded: bool = False  # the period is the upload date, not the activity dates
    document_type: DocumentType | None = None
    activity_type: ActivityType | None = None
    activity_ids: list[int] | None = None  # named activities (None = no name given; [] = a name that matched nothing)
    name_words: list[str] = field(default_factory=list)


def _scope_activities(query: FileQuery, now: datetime) -> list[Activity] | None:
    """The activities the files must belong to, newest first, or None when the question does not limit them."""
    limited = query.activity_ids is not None or query.activity_type or (query.period and not query.uploaded)
    if not limited:
        return None
    activities = Activity.objects.select_related("status", "activity_type")
    if query.activity_ids is not None:
        activities = activities.filter(pk__in=query.activity_ids)
    if query.activity_type:
        activities = activities.filter(activity_type=query.activity_type)
    period = query.period
    if period and not query.uploaded:
        if period.kind == NOW:
            activities = activities.filter(pk__in=happening_now(None, now).values("pk"))
        elif period.kind == UPCOMING:
            ids = [a.pk for a in activities_in_range(period.start, None) if activity_group(a, now) == UPCOMING_GROUP]
            activities = activities.filter(pk__in=ids)
        else:
            activities = activities.filter(end_date__gte=period.start, start_date__lte=period.end)
    return list(activities.order_by("-start_date", "-activity_id"))


def _files_heading(query: FileQuery, scope: list[Activity] | None) -> str:
    what = f"{query.document_type.name} files" if query.document_type else "Files"
    period = query.period
    if query.activity_ids is not None and len(query.activity_ids) == 1 and scope:
        target = f"for {safe_text(scope[0].title)}"
    elif query.activity_ids is not None:
        target = f"for activities matching “{' '.join(query.name_words)}”"
    elif query.activity_type:
        target = f"for {query.activity_type.name} activities"
    else:
        target = "for activities" if period and not query.uploaded else ""
    if period and query.uploaded:
        when = "uploaded now" if period.kind == NOW else f"uploaded in {period.label}" if period.kind != UPCOMING else ""
        return " ".join(part for part in (what, when, target) if part)
    if period:
        when = {NOW: "happening now", UPCOMING: "that are upcoming"}.get(period.kind, f"in {period.label}")
        target = target or "for activities"
        return f"{what} {target} {when}"
    return " ".join(part for part in (what, target) if part)


def _documents_marker(query: FileQuery, scope: list[Activity] | None) -> str:
    activity_id = scope[0].pk if scope and len(scope) == 1 else 0
    type_id = query.document_type.pk if query.document_type else 0
    return f"[documents-list:{activity_id}:{type_id}]"


def _missing_lines(query: FileQuery, scope: list[Activity]) -> list[str]:
    """Required documents with nothing uploaded (document_checklist()), for at most MAX_MISSING_ACTIVITIES activities."""
    lines = []
    # Outside Draft only, as the checklist's "missing" count (documents.services, ActivityDocuments.missing_count).
    counted = [activity for activity in scope if activity.status.name != ActivityStatus.DRAFT]
    for activity in counted[:MAX_MISSING_ACTIVITIES]:
        missing = [item.document_type.name for item in document_services.document_checklist(activity)["required"]
                   if item.state == document_services.MISSING
                   and (query.document_type is None or item.document_type.pk == query.document_type.pk)]
        if missing:
            lines.append(f"- **{safe_text(activity.title)}** [activity:{activity.pk}]: {', '.join(missing)}")
    return lines


def files_answer(query: FileQuery, now: datetime | None = None, scope_activity_id: int | None = None) -> Listing:
    """The files answer (module docstring). `scope_activity_id`: the page's activity filter, used when the question
    names no activity."""
    now = now or timezone.now()
    if scope_activity_id and query.activity_ids is None:
        query.activity_ids = [scope_activity_id]
        query.name_words = []
    scope = _scope_activities(query, now)
    heading = _files_heading(query, scope)

    documents = document_services.documents_for_listing()
    if scope is not None:
        documents = documents.filter(activity_id__in=[a.pk for a in scope])
    if query.document_type:
        documents = documents.filter(document_type=query.document_type)
    if query.period and query.uploaded and query.period.start:
        start = timezone.make_aware(datetime.combine(query.period.start, time(0, 0)))
        documents = documents.filter(uploaded_at__gte=start)
        if query.period.end:
            documents = documents.filter(uploaded_at__lt=start + timedelta(days=(query.period.end - query.period.start).days + 1))
    documents = documents.order_by("-activity__start_date", "-activity_id", "-uploaded_at", "-document_id")
    total = documents.count()

    if total == 0:
        lines = [f"No {'files' + heading.removeprefix('Files') if heading.startswith('Files') else heading}."]
        if query.activity_ids == []:
            lines = [f"No activity title matches “{' '.join(query.name_words)}”, so no files were found."]
        elif scope == []:
            period_text = f" in {query.period.label}" if query.period and query.period.kind not in (NOW, UPCOMING) else ""
            lines.append(f"There are no matching activities{period_text}.")
        elif scope:
            missing = _missing_lines(query, scope)
            if missing:
                lines += ["", "Required documents still missing (from the checklist):", *missing]
        return Listing("\n".join(lines), 0, 0)

    shown = list(documents[:MAX_ITEMS])
    lines = [f"**{heading}: {total}**"]
    current = None
    for document in shown:
        if document.activity_id != current:
            current = document.activity_id
            lines += ["", f"**{safe_text(document.activity.title)}** [activity:{document.activity_id}] · {date_span(document.activity)}"]
        lines.append(f"- {document.document_type.name}: {safe_text(document.title)} [file:{document.pk}]")
    if total > len(shown):
        note = " (the Documents page has no date filter)" if query.period and not scope_activity_id else ""
        lines += ["", f"Showing the first {len(shown)} of {total}. {total - len(shown)} more on the Documents page"
                      f"{note} {_documents_marker(query, scope)}"]
    return Listing("\n".join(lines), total, len(shown), [d.pk for d in shown])
