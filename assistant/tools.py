"""Read-only tools the chat model may call (Gemini function calling).

Record tools answer from the database through existing services (never from documents) and
return only activity and document information: no participant names, emails or contacts.
`search_approved_documents` retrieves excerpts from approved documents.
"""

import html
import re
from dataclasses import dataclass
from datetime import date

from django.conf import settings
from django.db import connection, transaction
from django.utils import timezone
from pgvector.django import CosineDistance

from activities.models import Activity, ActivityStatus
from activities.services import list_activities
from documents import services as document_services
from documents.models import Document, DocumentStatus

from .gemini import QUERY_TASK, GeminiGateway, ToolSpec
from .indexing import excluded_type_names
from .models import DocumentEmbedding

RECORD_TOOLS = {"find_activities", "get_document_checklist", "list_activity_documents", "documents_needing_attention"}
SEARCH_TOOL = "search_approved_documents"


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
        SEARCH_TOOL,
        "Search the text of APPROVED documents. Returns numbered excerpts to cite as [n]. Use it for questions "
        "about what documents say (content), not for document status.",
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


RECORD_FUNCTIONS = {
    "find_activities": find_activities,
    "get_document_checklist": get_document_checklist,
    "list_activity_documents": list_activity_documents,
    "documents_needing_attention": documents_needing_attention,
}


# --- Document search ------------------------------------------------------------------------------------


@dataclass
class Excerpt:
    number: int
    document: Document
    chunk_index: int
    text: str
    similarity: float

    @property
    def approved_on(self) -> date | None:
        return timezone.localtime(self.document.reviewed_at).date() if self.document.reviewed_at else None


_EXCERPT_TAG = re.compile(r"<\s*/?\s*excerpt", re.IGNORECASE)


def escape_excerpt_text(text: str) -> str:
    """A document can never open or close an <excerpt> tag (it would escape its data block)."""
    return _EXCERPT_TAG.sub(lambda match: match.group(0).replace("<", "&lt;"), text)


def excerpt_block(excerpt: Excerpt) -> str:
    """One excerpt as tagged DATA. Attribute values are HTML-escaped; the text cannot break the tag."""
    document = excerpt.document
    attributes = {
        "n": excerpt.number,
        "document": document.title,
        "type": document.document_type.name,
        "activity": document.activity.title,
        "approved_on": excerpt.approved_on.isoformat() if excerpt.approved_on else "",
    }
    attrs = " ".join(f'{key}="{html.escape(str(value), quote=True)}"' for key, value in attributes.items())
    return f"<excerpt {attrs}>\n{escape_excerpt_text(excerpt.text)}\n</excerpt>"


def retrieve(gateway: GeminiGateway, query: str, activity_id: int | None = None, top_k: int | None = None) -> list[tuple[DocumentEmbedding, float]]:
    """The most similar chunks of approved activity documents (cosine), above the relevance threshold."""
    vector = gateway.embed([query], task_type=QUERY_TASK)[0]
    queryset = (
        DocumentEmbedding.objects.filter(
            document__related_certificate__isnull=True, document__status__status_name=DocumentStatus.APPROVED
        )
        .exclude(document__document_type__name__in=[n for n in settings.ASSISTANT_EXCLUDED_DOCUMENT_TYPES])
        .select_related("document__activity", "document__document_type", "document__status")
        .annotate(distance=CosineDistance("embedding_vector", vector))
    )
    if activity_id:
        queryset = queryset.filter(document__activity_id=activity_id)
    with transaction.atomic(), connection.cursor() as cursor:
        # More candidates, and keep scanning when a filter removes rows (pgvector 0.8).
        cursor.execute("SET LOCAL hnsw.ef_search = 100")
        cursor.execute("SET LOCAL hnsw.iterative_scan = relaxed_order")
        rows = list(queryset.order_by("distance")[: top_k or settings.ASSISTANT_TOP_K])
    excluded = excluded_type_names()
    scored = [
        (row, 1 - float(row.distance)) for row in rows
        if row.document.document_type.name.strip().lower() not in excluded
    ]
    # relaxed_order may be slightly out of order; ties are broken the same way every time.
    scored.sort(key=lambda pair: (-pair[1], pair[0].document_id, pair[0].chunk_index))
    return [(row, similarity) for row, similarity in scored if similarity >= settings.ASSISTANT_MIN_SIMILARITY]
