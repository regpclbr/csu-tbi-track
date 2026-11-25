"""Read-only tools the chat model may call (Gemini function calling).

Record tools answer from the database through existing services (never from documents) and
return only activity, document and count information: no participant names, emails or contacts.
`search_documents` retrieves excerpts from every indexed file (ASSUMPTIONS 438): activity documents in any review
state, generated reports and approved submissions the member may open; each excerpt carries its state.
`list_activities_by_period` and `find_files` take the user's own words; assistant.listings works out the dates and
writes the answer (ASSUMPTIONS 381, 383). Which tools are offered depends on the path (assistant.routing).
"""

import html
import re
from dataclasses import dataclass
from datetime import date

from django.conf import settings
from django.db import connection, transaction
from django.db.models import Q
from django.utils import timezone
from pgvector.django import CosineDistance

from activities.models import Activity, ActivityStatus, ActivityType
from activities import budget as activity_budget
from activities.services import list_activities
from documents import services as document_services
from documents import pages as document_pages
from reports import metrics
from documents.models import Document, DocumentStatus, DocumentType

from . import routing
from .gemini import QUERY_TASK, GeminiGateway, ToolSpec
from . import indexing
from .indexing import excluded_type_names
from .listings import ActivityQuery, FileQuery, Listing, activities_answer, files_answer
from .models import DocumentEmbedding
from .periods import Period, parse_period

# ASSUMPTIONS 439: participation and approval record tools (counts only, never names).
ATTENDANCE_TOOL, REGISTRATIONS_TOOL = "get_attendance_by_day", "get_registration_counts"
MIX_TOOL, EVALUATION_TOOL, APPROVALS_TOOL = "get_participant_mix", "get_evaluation_results", "get_pending_approvals"
RECORD_TOOLS = {"find_activities", "get_document_checklist", "list_activity_documents", "documents_needing_attention",
                "get_activity_budget", ATTENDANCE_TOOL, REGISTRATIONS_TOOL, MIX_TOOL, EVALUATION_TOOL, APPROVALS_TOOL}
BUDGET_TOOL = "get_activity_budget"  # ASSUMPTIONS 433
SEARCH_TOOL = "search_documents"
# Tools whose answer our code writes (assistant.listings, ASSUMPTIONS 381, 383). Gemini passes the user's own words;
# our code works out the dates. When one of them is the only tool used, the reply is our text, not Gemini's.
ACTIVITIES_TOOL, FILES_TOOL = "list_activities_by_period", "find_files"
LISTING_TOOLS = {ACTIVITIES_TOOL, FILES_TOOL}


TOOL_SPECS = [
    ToolSpec(
        "find_activities",
        "Find TBI activities by (part of) their title and/or a date they run on. Returns up to 10 activities with "
        "their activity_id, title, status and dates. Use it first when the question names an activity.",
        {
            "type": "object",
            "properties": {
                "name": {"type": "string", "description": "Part of the activity title."},
                "date": {"type": "string", "description": "A date the activity runs on, YYYY-MM-DD."},
            },
        },
    ),
    ToolSpec(
        "get_document_checklist",
        "The required-document checklist of one activity from the system's records: each required document type "
        "with its due date, state (Missing, For Review, Approved) and whether it is overdue, plus optional types. "
        "Use it for questions about missing, overdue, pending or due documents.",
        {"type": "object", "properties": {"activity_id": {"type": "integer"}}, "required": ["activity_id"]},
    ),
    ToolSpec(
        "list_activity_documents",
        "All uploaded documents of one activity from the system's records: type, title, status, review state "
        "(waiting_for_review, returned, approved), upload and approval dates.",
        {"type": "object", "properties": {"activity_id": {"type": "integer"}}, "required": ["activity_id"]},
    ),
    ToolSpec(
        "documents_needing_attention",
        "Across all non-draft activities, from the system's records: overdue required documents and documents "
        "waiting for review or returned to the uploader.",
        {"type": "object", "properties": {}},
    ),
    ToolSpec(
        BUDGET_TOOL,
        "The budget of one activity from the system's records (copied from its approved Activity Design): fund source, "
        "account / charging, every budget line (category, particulars, quantity, unit, days, unit price, line total), "
        "the totals per category and the grand total, in pesos. Use it for questions about an activity's budget, "
        "cost or fund source.",
        {"type": "object", "properties": {"activity_id": {"type": "integer"}}, "required": ["activity_id"]},
    ),
    ToolSpec(
        ATTENDANCE_TOOL,
        "How many people attended one activity on each of its days, from the system's records (QR check-in and staff "
        "marking; counts only, no names), and how many attended at least one day. Use it for attendance questions.",
        {"type": "object", "properties": {"activity_id": {"type": "integer"}}, "required": ["activity_id"]},
    ),
    ToolSpec(
        REGISTRATIONS_TOOL,
        "One activity's registration counts from the system's records: registered (walk-ins included, cancelled not "
        "counted), \"Will attend\" / \"Will not attend\" (pre-registrations), walk-ins, cancelled, attended, the "
        "attendance rate, max slots and slots left. Counts only, no names.",
        {"type": "object", "properties": {"activity_id": {"type": "integer"}}, "required": ["activity_id"]},
    ),
    ToolSpec(
        MIX_TOOL,
        "One activity's participant mix from the system's records: Faculty, Incubatees, MSMEs and other categories "
        "with counts and percentages (attendees once anybody attended, else registrations) and NAVIGATU's guideline "
        "check (Faculty at most 30%, Incubatees and MSMEs at least 70%).",
        {"type": "object", "properties": {"activity_id": {"type": "integer"}}, "required": ["activity_id"]},
    ),
    ToolSpec(
        EVALUATION_TOOL,
        "One activity's evaluation results from the system's records: evaluations submitted, the response rate "
        "(evaluations / attended) and, per rating question, the number of answers, the mean (1-5) and how many gave "
        "each rating. Numbers only: no names, no written answers.",
        {"type": "object", "properties": {"activity_id": {"type": "integer"}}, "required": ["activity_id"]},
    ),
    ToolSpec(
        APPROVALS_TOOL,
        "What waits for the member who asks, from the system's records: items waiting for their approval "
        "(certificate approvals, submissions, document reviews, overdue assignments they gave) and the work assigned "
        "to them that is still open, with due dates. Use it for questions about approvals, submissions or assignments "
        "that are waiting.",
        {"type": "object", "properties": {}},
    ),
    ToolSpec(
        ACTIVITIES_TOOL,
        "List TBI activities of a period from the system's records (schedule, counts, status). Pass the user's own "
        "words for the period (for example \"November\", \"this week\", \"next month\", \"upcoming\", \"now\"); "
        "the system works out the dates. Use it for questions about which activities happen, happened or are planned.",
        {
            "type": "object",
            "properties": {
                "period_text": {"type": "string", "description": "The period in the user's words. Never compute dates."},
                "status": {"type": "string", "description": "Optional: Draft, Scheduled, Ongoing or Conducted."},
                "activity_type": {"type": "string", "description": "Optional: an activity type, e.g. Training."},
            },
        },
    ),
    ToolSpec(
        FILES_TOOL,
        "Find uploaded files (documents) of activities from the system's records, to open or download them: by "
        "activity title, period (the user's own words) and/or document type. Returns file titles, types and review "
        "states, never file content. Use it when the user wants the files themselves, not what they say.",
        {
            "type": "object",
            "properties": {
                "activity_name": {"type": "string", "description": "Optional: part of the activity title."},
                "period_text": {"type": "string", "description": "Optional: the period in the user's words."},
                "document_type": {"type": "string", "description": "Optional: a document type, e.g. Attendance Sheet."},
                "uploaded": {"type": "boolean", "description": "True when the period is the upload date."},
            },
        },
    ),
    ToolSpec(
        SEARCH_TOOL,
        "Search the text of the system's files: activity documents (approved, approved copies, and documents still "
        "waiting for review or returned), generated reports and approved submissions. Returns numbered excerpts to "
        "cite as [n], each with its file kind and review state. Use it for questions about what files say (content), "
        "not for document status.",
        {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "What to look for, in your own words."},
                "activity_id": {"type": "integer", "description": "Optional: only this activity's documents."},
            },
            "required": ["query"],
        },
    ),
]


_SPECS = {spec.name: spec for spec in TOOL_SPECS}
# The tools offered per path (assistant.routing): document status questions get only the existing record tools,
# content questions the document search; anything else every tool.
STATUS_TOOL_SPECS = [_SPECS[name] for name in ("find_activities", "get_document_checklist", "list_activity_documents",
                                               "documents_needing_attention", APPROVALS_TOOL)]
# Attendance, registrations, the mix and evaluation numbers of a named activity (routing.PARTICIPATION, ASSUMPTIONS 439).
PARTICIPATION_TOOL_SPECS = [_SPECS[name] for name in ("find_activities", ATTENDANCE_TOOL, REGISTRATIONS_TOOL, MIX_TOOL,
                                                      EVALUATION_TOOL)]
CONTENT_TOOL_SPECS = [_SPECS["find_activities"], _SPECS[SEARCH_TOOL]]


class ToolError(Exception):
    """A tool was called with bad arguments. The message goes back to the model."""


# --- Record tools -----------------------------------------------------------------------------------


def _iso(value) -> str | None:
    if value is None:
        return None
    if hasattr(value, "tzinfo") and getattr(value, "tzinfo", None) is not None:
        return timezone.localtime(value).isoformat()
    return value.isoformat()


def _activity(args: dict) -> Activity:
    try:
        return Activity.objects.select_related("status").get(pk=int(args.get("activity_id")))
    except (TypeError, ValueError, Activity.DoesNotExist) as error:
        raise ToolError("No activity with that activity_id. Use find_activities first.") from error


def _activity_json(activity: Activity) -> dict:
    return {
        "activity_id": activity.pk,
        "title": activity.title,
        "status": activity.status.name,
        "start_date": _iso(activity.start_date),
        "end_date": _iso(activity.end_date),
        "start_time": activity.start_time.strftime("%H:%M") if activity.start_time else None,  # Manila time
        "end_time": activity.end_time.strftime("%H:%M") if activity.end_time else None,
    }


def find_activities(args: dict) -> dict:
    filters = {"search": str(args.get("name") or "").strip()}
    if args.get("date"):
        try:
            day = date.fromisoformat(str(args["date"]))
        except ValueError as error:
            raise ToolError("date must look like 2026-10-20.") from error
        filters.update(date_from=day, date_to=day)
    result = list_activities(filters, page=1, per_page=10)
    return {"activities": [_activity_json(a) for a in result.items], "total_matches": result.total}


def get_document_checklist(args: dict) -> dict:
    activity = _activity(args)
    checklist = document_services.document_checklist(activity)
    return {
        "activity": _activity_json(activity),
        "today": _iso(checklist["today"]),
        "required": [
            {
                "document_type": item.document_type.name,
                "due_date": _iso(item.due_date),
                "state": item.state,
                "is_overdue": item.is_overdue,
                "uploaded_documents": len(item.documents),
            }
            for item in checklist["required"]
        ],
        "optional": [
            {"document_type": entry["document_type"].name, "uploaded_documents": len(entry["documents"])}
            for entry in checklist["optional"]
        ],
    }


def list_activity_documents(args: dict) -> dict:
    activity = _activity(args)
    documents = document_services.documents_for_listing().filter(activity=activity).order_by("uploaded_at")
    return {
        "activity": _activity_json(activity),
        "documents": [
            {
                "title": d.title,
                "document_type": d.document_type.name,
                "status": d.status.status_name,
                "review_state": d.review_state,
                "uploaded_on": _iso(timezone.localtime(d.uploaded_at).date()),
                "approved_on": _iso(timezone.localtime(d.reviewed_at).date()) if d.status.status_name == DocumentStatus.APPROVED else None,
                "searchable": d.searchable,
            }
            for d in documents
        ],
    }


def documents_needing_attention(args: dict) -> dict:
    overdue = []
    for activity in Activity.objects.select_related("status").exclude(status__name=ActivityStatus.DRAFT).order_by("start_date"):
        for item in document_services.document_checklist(activity)["required"]:
            if item.is_overdue:
                overdue.append({"activity": activity.title, "activity_id": activity.pk, "document_type": item.document_type.name,
                                "due_date": _iso(item.due_date), "state": item.state})
    waiting = [
        {"activity": d.activity.title, "activity_id": d.activity_id, "document_type": d.document_type.name,
         "title": d.title, "review_state": d.review_state}
        for d in document_services.documents_for_listing().filter(status__status_name=DocumentStatus.FOR_REVIEW).order_by("uploaded_at")[:50]
    ]
    return {"overdue_required_documents": overdue[:50], "documents_waiting_for_review_or_returned": waiting}


def get_activity_budget(args: dict) -> dict:
    """ASSUMPTIONS 433: activities.budget.budget_json (amounts as text with two decimals, never float)."""
    activity = _activity(args)
    data = activity_budget.budget_json(activity)

    def money(value) -> str:
        return f"{value:.2f}"

    return {
        "activity": _activity_json(activity),
        "fund_source": data["fund_source"],
        "fund_account": data["fund_account"],
        "currency": "PHP",
        "lines": [{**{k: v for k, v in line.items() if k not in ("category_id", "quantity", "unit_price", "line_total")},
                   "quantity": f"{line['quantity']:f}", "unit_price": money(line["unit_price"]),
                   "line_total": money(line["line_total"])} for line in data["lines"]],
        "category_totals": [{"category": item["category"], "total": money(item["total"])} for item in data["category_totals"]],
        "grand_total": money(data["total"]),
    }


def _period_from(args: dict, now) -> Period | None:
    text = str(args.get("period_text") or "").strip()
    if not text:
        return None
    period = parse_period(text, now)
    if period is None:
        raise ToolError("The period could not be read. Pass the user's own words, e.g. \"November\" or \"this week\".")
    return period


def _type_from(args: dict, key: str, model):
    text = str(args.get(key) or "").strip()
    if not text:
        return None
    found = routing.match_type(routing.normalize(text), model.objects.all())
    if found is None:
        names = ", ".join(model.objects.values_list("name", flat=True))
        raise ToolError(f"Unknown {key.replace('_', ' ')}. Use one of: {names}.")
    return found


def list_activities_by_period(args: dict, question: str, now) -> Listing:
    status = str(args.get("status") or "").strip().title() or None
    if status and status not in (ActivityStatus.DRAFT, ActivityStatus.SCHEDULED, ActivityStatus.ONGOING, ActivityStatus.CONDUCTED):
        raise ToolError("status must be Draft, Scheduled, Ongoing or Conducted.")
    include_drafts = status == ActivityStatus.DRAFT or bool(routing.DRAFT_WORDS.search(routing.normalize(question)))
    query = ActivityQuery(period=_period_from(args, now), status=status, include_drafts=include_drafts,
                          activity_type=_type_from(args, "activity_type", ActivityType))
    return activities_answer(query, now)


def find_files(args: dict, question: str, now, scope_activity_id: int | None = None) -> Listing:
    name = routing.normalize(str(args.get("activity_name") or ""))
    activity_ids, words = None, []
    if name:
        words = routing.name_words(name, [], [])
        activity_ids = routing.activities_named(words) if words else []
        words = words or name.split()
    query = FileQuery(period=_period_from(args, now), uploaded=args.get("uploaded") is True,
                      document_type=_type_from(args, "document_type", DocumentType),
                      activity_ids=activity_ids, name_words=words)
    return files_answer(query, now, scope_activity_id)


def _share(fraction) -> str | None:
    """0.25 -> "25.0%" (metrics return fractions); None stays None (nothing to divide by)."""
    return None if fraction is None else f"{fraction * 100:.1f}%"


def get_attendance_by_day(args: dict) -> dict:
    """registrations.attendance.checkins_by_day (the Overview's Attendance card): counts per activity day."""
    from registrations import attendance

    activity = _activity(args)
    days = attendance.checkins_by_day(activity)
    counts = metrics.activity_registration_counts(activity)
    return {
        "activity": _activity_json(activity),
        "days": [{"day_number": number, "date": _iso(day["day"]), "attended": day["total"], "by_qr_check_in": day["qr"],
                  "by_staff": day["total"] - day["qr"]} for number, day in enumerate(days, start=1)],
        "attended_at_least_one_day": counts["attended"],
    }


def get_registration_counts(args: dict) -> dict:
    """reports.metrics.activity_registration_counts (the reports' rule, ASSUMPTIONS 440) + the Overview's pre-registration
    answers (registrations.attendance.pre_registration_counts) + the slots (activities.services)."""
    from activities.services import slots_remaining
    from registrations import attendance

    activity = _activity(args)
    counts = metrics.activity_registration_counts(activity)
    pre = attendance.pre_registration_counts(activity)
    return {
        "activity": _activity_json(activity),
        "registered": counts["registered"],
        "will_attend": pre["will_attend"],
        "will_not_attend": pre["will_not_attend"],
        "walk_ins": counts["walk_ins"],
        "cancelled_not_counted": counts["cancelled"],
        "attended": counts["attended"],
        "attendance_rate": _share(counts["attendance_rate"]),
        "max_slots": activity.max_slots,
        "slots_left": slots_remaining(activity),
        "rule": "Registered = everyone except cancelled registrations (\"Will not attend\"); a cancelled person who "
                "came anyway counts as registered and attended. Walk-ins count.",
    }


def get_participant_mix(args: dict) -> dict:
    """reports.metrics.participant_mix (the Overview's mix chart)."""
    activity = _activity(args)
    mix = metrics.participant_mix([activity.pk])[activity.pk]
    check = mix["check"]
    return {
        "activity": _activity_json(activity),
        "basis": mix["basis"],
        "groups": [{"group": row["group"], "registered": row["registered"], "registered_share": _share(row["registered_share"]),
                    "attended": row["attended"], "attended_share": _share(row["attended_share"])} for row in mix["rows"]],
        "guideline": metrics.guideline_text(),
        "guideline_checked": mix["checked"],
        "within_guideline": None if check is None else check["within"],
        "problems": [] if check is None else check["problems"],
    }


def get_evaluation_results(args: dict) -> dict:
    """reports.metrics.activity_evaluation_summary, numbers only (never the written answers or comments)."""
    activity = _activity(args)
    data = metrics.activity_evaluation_summary(activity)
    return {
        "activity": _activity_json(activity),
        "attended": data["attended"],
        "evaluations": data["evaluations"],
        "response_rate": _share(data["evaluation_response_rate"]),
        "rating_questions": [
            {"question": rating["question"], "answers": rating["answers"],
             "mean": None if rating["mean"] is None else f"{rating['mean']:.2f}",
             "counts": {str(item["rating"]): item["count"] for item in rating["frequency"]}}
            for rating in data["ratings"]
        ],
        "scale": "1 (lowest) to 5 (highest); no interpretation scale yet (the TBI has not given one).",
    }


def get_pending_approvals(args: dict, member) -> dict:
    """approvals.inbox.inbox (what waits for this member's approval) + approvals.assignments.assigned_to_me (open work
    assigned to them). Titles, kinds and dates only."""
    from approvals import assignments, inbox

    items = inbox.inbox(member)
    assigned = list(assignments.assigned_to_me(member)[:50])
    return {
        "waiting_for_your_approval": [
            {"kind": inbox.KIND_LABELS[item.kind], "title": item.title, "activity": item.activity_title,
             "since": _iso(timezone.localtime(item.since).date()), "overdue": item.overdue}
            for item in items[:50]
        ],
        "waiting_total": len(items),
        "assigned_to_you": [
            {"work": assignment.get_kind_display(), "title": assignments.target_title(assignment),
             "assigned_by": assignment.assigned_by.name, "due_date": _iso(assignment.due_date),
             "overdue": assignments.is_overdue(assignment)}
            for assignment in assigned
        ],
    }


# Record tools that need the member who asks (their own inbox).
MEMBER_FUNCTIONS = {APPROVALS_TOOL: get_pending_approvals}

RECORD_FUNCTIONS = {
    ATTENDANCE_TOOL: get_attendance_by_day,
    REGISTRATIONS_TOOL: get_registration_counts,
    MIX_TOOL: get_participant_mix,
    EVALUATION_TOOL: get_evaluation_results,
    "find_activities": find_activities,
    "get_document_checklist": get_document_checklist,
    "list_activity_documents": list_activity_documents,
    "documents_needing_attention": documents_needing_attention,
    BUDGET_TOOL: get_activity_budget,
}


# --- Document search ------------------------------------------------------------------------------------


def source_state(kind: str, item) -> tuple[str, str]:
    """The state shown on a source card and given to the model with each excerpt (ASSUMPTIONS 438): (label, tone)."""
    if kind == indexing.DOCUMENT:
        if document_services.is_approved_copy(item):
            return "Approved copy", "success"
        return document_pages.STATE_LABELS[item.review_state]
    if kind == indexing.REPORT:
        return "Generated report", "info"
    from approvals.models import Submission

    return {Submission.Status.APPROVED: ("Approved submission", "success"),
            Submission.Status.RETURNED: ("Returned submission", "warning")}.get(item.status, ("Waiting for approval", "info"))


def source_of(row) -> tuple[str, object]:
    """(kind, file) of a DOCUMENT_EMBEDDING or AI_MESSAGE_SOURCE row."""
    if row.document_id:
        return indexing.DOCUMENT, row.document
    if row.generated_report_id:
        return indexing.REPORT, row.generated_report
    return indexing.SUBMISSION, row.submission


@dataclass
class Excerpt:
    number: int
    kind: str  # indexing.DOCUMENT / REPORT / SUBMISSION
    file: object  # Document / GeneratedReport / Submission
    chunk_index: int
    text: str
    similarity: float

    @property
    def key(self) -> tuple[str, int, int]:
        return self.kind, self.file.pk, self.chunk_index

    @property
    def document(self) -> Document | None:
        return self.file if self.kind == indexing.DOCUMENT else None

    @property
    def title(self) -> str:
        if self.kind == indexing.REPORT:
            from reports.services import download_name

            return download_name(self.file)
        return self.file.title

    @property
    def activity(self) -> Activity | None:
        return None if self.kind == indexing.REPORT else self.file.activity

    @property
    def dated_on(self) -> date | None:
        """When the file got its current state: reviewed / approved (documents, submissions), generated (reports)."""
        moment = self.file.generated_at if self.kind == indexing.REPORT else self.file.reviewed_at
        return timezone.localtime(moment).date() if moment else None

    @property
    def approved_on(self) -> date | None:  # the older name (documents)
        return self.dated_on


KIND_NAMES = {indexing.DOCUMENT: "activity document", indexing.REPORT: "generated report",
              indexing.SUBMISSION: "submission to the Director"}
_EXCERPT_TAG = re.compile(r"<\s*/?\s*excerpt", re.IGNORECASE)


def escape_excerpt_text(text: str) -> str:
    """A document can never open or close an <excerpt> tag (it would escape its data block)."""
    return _EXCERPT_TAG.sub(lambda match: match.group(0).replace("<", "&lt;"), text)


def excerpt_block(excerpt: Excerpt) -> str:
    """One excerpt as tagged DATA. Attribute values are HTML-escaped; the text cannot break the tag."""
    item = excerpt.file
    if excerpt.kind == indexing.DOCUMENT:
        type_name = item.document_type.name
    elif excerpt.kind == indexing.REPORT:
        type_name = item.report_type
    else:
        type_name = item.document_type.name if item.document_type_id else ""
    activity = excerpt.activity
    attributes = {
        "n": excerpt.number,
        "file": excerpt.title,
        "kind": KIND_NAMES[excerpt.kind],
        "type": type_name,
        "activity": activity.title if activity else "",
        "state": source_state(excerpt.kind, item)[0],
        "dated": excerpt.dated_on.isoformat() if excerpt.dated_on else "",
    }
    attrs = " ".join(f'{key}="{html.escape(str(value), quote=True)}"' for key, value in attributes.items())
    return f"<excerpt {attrs}>\n{escape_excerpt_text(excerpt.text)}\n</excerpt>"


def searchable_q(member=None) -> Q:
    """The chunks a member may get excerpts from (ASSUMPTIONS 438): activity documents in any review state (never
    certificate evidence, never an excluded type), generated reports (any staff may download them) and approved
    submissions not saved to the documents that THIS member may open (approvals.services.visible_submissions; none
    without a member)."""
    documents = Q(document__isnull=False, document__related_certificate__isnull=True) & ~Q(
        document__document_type__name__in=list(settings.ASSISTANT_EXCLUDED_DOCUMENT_TYPES))
    allowed = documents | Q(generated_report__isnull=False)
    if member is not None:
        from approvals.models import Submission
        from approvals.services import visible_submissions

        allowed |= Q(submission__isnull=False, submission__status=Submission.Status.APPROVED,
                     submission__saved_document__isnull=True,
                     submission_id__in=visible_submissions(member).order_by().values("pk"))
    return allowed


def retrieve(gateway: GeminiGateway, query: str, activity_id: int | None = None, top_k: int | None = None,
             member=None) -> list[tuple[DocumentEmbedding, float]]:
    """The most similar chunks the member may read (searchable_q, cosine), above the relevance threshold. With an
    activity: only that activity's documents and submissions (reports cover periods, not one activity)."""
    vector = gateway.embed([query], task_type=QUERY_TASK)[0]
    queryset = (
        DocumentEmbedding.objects.filter(searchable_q(member))
        .select_related("document__activity", "document__document_type", "document__status", "generated_report",
                        "submission__activity", "submission__document_type")
        .annotate(distance=CosineDistance("embedding_vector", vector))
    )
    if activity_id:
        queryset = queryset.filter(Q(document__activity_id=activity_id) | Q(submission__activity_id=activity_id))
    with transaction.atomic(), connection.cursor() as cursor:
        # More candidates, and keep scanning when a filter removes rows (pgvector 0.8).
        cursor.execute("SET LOCAL hnsw.ef_search = 100")
        cursor.execute("SET LOCAL hnsw.iterative_scan = relaxed_order")
        rows = list(queryset.order_by("distance")[: top_k or settings.ASSISTANT_TOP_K])
    excluded = excluded_type_names()
    scored = [
        (row, 1 - float(row.distance)) for row in rows
        if not (row.document_id and row.document.document_type.name.strip().lower() in excluded)
    ]
    # relaxed_order may be slightly out of order; ties are broken the same way every time.
    scored.sort(key=lambda pair: (-pair[1], pair[0].document_id or 0, pair[0].generated_report_id or 0,
                                  pair[0].submission_id or 0, pair[0].chunk_index))
    return [(row, similarity) for row, similarity in scored if similarity >= settings.ASSISTANT_MIN_SIMILARITY]
