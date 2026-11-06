"""Display helpers for the Documents pages (phase F4): the activity's Documents tab, the repository, the review queue
and the document page. Display only: every rule is in documents.services (who may upload, review, replace or delete;
the checklist; the review state). This module turns their results into what the templates show.
"""

import re
from dataclasses import dataclass, field
from urllib.parse import urlencode

from django.core.exceptions import ValidationError
from django.http import HttpRequest, QueryDict
from django.urls import reverse
from django.utils.http import url_has_allowed_host_and_scheme

from accounts.models import TBIMember
from activities.models import Activity
from activities.services import PageResult, _paginate
from config.parsing import parse_bool

from . import services
from .models import Document, DocumentType

# The four states a member sees. "For Review" is split by reviewed_at (Document.review_state): waiting for a reviewer,
# or returned to the uploader. Tones are the badge colours (components/badge.html).
STATE_LABELS = {
    Document.WAITING_FOR_REVIEW: ("Waiting for review", "info"),
    Document.RETURNED: ("Returned", "warning"),
    Document.APPROVED: ("Approved", "success"),
}
MISSING_LABEL = ("Missing", "neutral")
REVIEW_STATE_CHOICES = [(key, label) for key, (label, _) in STATE_LABELS.items()]


def state_badge(document: Document) -> dict:
    label, tone = STATE_LABELS[document.review_state]
    return {"label": label, "tone": tone}


def document_row(member: TBIMember, document: Document) -> dict:
    """One document as the lists show it, with the actions services.allowed_actions() gives this member."""
    return {
        "document": document,
        "state": state_badge(document),
        "searchable": bool(getattr(document, "searchable", False)),
        "actions": services.allowed_actions(member, document),
        "download_url": download_url(document),
        # Activity documents only; certificate evidence keeps its own download (its view checks the certificate).
        "preview_url": None if document.related_certificate_id else reverse("documents:preview", args=[document.pk]),
    }


def download_url(document: Document) -> str:
    """Activity documents: documents:download; certificate evidence: its own download view (same login rule)."""
    if document.related_certificate_id:
        return reverse("certificates:evidence_download", args=[document.pk])
    return reverse("documents:download", args=[document.pk])


# --- Upload form ----------------------------------------------------------------------------------------------


def upload_limits() -> dict:
    """What the upload form tells the member. The server's file checks (services.validate_upload) decide."""
    extensions = list(dict.fromkeys(services.ALLOWED_EXTENSIONS.values()))  # pdf, docx, xlsx, jpg, png (jpeg = jpg)
    return {
        "extensions": ", ".join(ext.upper() for ext in extensions),
        "accept": ",".join(f".{ext}" for ext in sorted(services.ALLOWED_EXTENSIONS)),
        "max_mb": services.max_upload_bytes() // (1024 * 1024),
    }


@dataclass
class UploadForm:
    """The upload form's values and the service's errors (one message per field)."""

    document_type: int | None = None
    title: str = ""
    errors: dict[str, str] = field(default_factory=dict)


def upload_form_from(request: HttpRequest) -> UploadForm:
    """An empty form; ?type=<id> preselects the type (the dashboard's "Upload" links)."""
    raw = request.GET.get("type", "")
    return UploadForm(document_type=int(raw) if raw.isdigit() else None)


# --- The activity's Documents tab -------------------------------------------------------------------------------


def checklist_state(item: services.ChecklistItem) -> tuple[str, str]:
    """Missing / Waiting for review / Returned / Approved, from the checklist's state (Missing, For Review,
    Approved) and its documents' review state: "Returned" only when every uploaded document was returned."""
    if item.state == services.APPROVED:
        return STATE_LABELS[Document.APPROVED]
    if item.state == services.MISSING:
        return MISSING_LABEL
    if any(document.review_state == Document.WAITING_FOR_REVIEW for document in item.documents):
        return STATE_LABELS[Document.WAITING_FOR_REVIEW]
    return STATE_LABELS[Document.RETURNED]


def documents_tab(request: HttpRequest, activity: Activity, upload: UploadForm | None = None) -> dict:
    """The checklist (services.document_checklist: required types with due date, state and overdue flag; optional
    types with their documents), each document with its actions, and the upload form."""
    member = request.user
    checklist = services.document_checklist(activity)
    required = []
    for item in checklist["required"]:
        label, tone = checklist_state(item)
        required.append({
            "document_type": item.document_type,
            "due_date": item.due_date,
            "state": {"label": label, "tone": tone},
            "is_overdue": item.is_overdue,
            "rows": [document_row(member, document) for document in item.documents],
        })
    optional = [  # only the optional types that have documents
        {"document_type": entry["document_type"], "rows": [document_row(member, d) for d in entry["documents"]]}
        for entry in checklist["optional"] if entry["documents"]
    ]
    upload = upload or upload_form_from(request)
    focus = next((key for key in ("document_type", "title", "file") if key in upload.errors), None)
    return {
        "today": checklist["today"],
        "required": required,
        "optional": optional,
        "upload": upload,
        "upload_focus": focus,  # the first field with an error gets the focus (works without JavaScript)
        "upload_focus_file": focus == "file",
        "upload_types": DocumentType.objects.filter(is_active=True).exclude(name=_certificate_type_name()).order_by("name"),
        "limits": upload_limits(),
        "back": f"{reverse('activities:detail', args=[activity.pk])}?tab=documents",
    }


def _certificate_type_name() -> str:
    """Certificate evidence has its own upload (Certificates tab), so its type is not offered here."""
    from certificates.services import CERTIFICATE_DOCUMENT_TYPE

    return CERTIFICATE_DOCUMENT_TYPE


# --- Repository and review queue ------------------------------------------------------------------------------


@dataclass
class RepositoryFilters:
    """The repository's filters from the query string. A value that cannot be read is ignored with a note (as the
    Activities list, item 262), never a 400 page."""

    q: str = ""
    activity: int | None = None
    document_type: int | None = None
    review_state: str = ""
    uploaded_by: int | None = None
    searchable: bool | None = None
    page: str = "1"
    ignored: list[str] = field(default_factory=list)

    def for_service(self) -> dict:
        return {
            "q": self.q, "activity": self.activity, "document_type": self.document_type,
            "review_state": self.review_state, "uploaded_by": self.uploaded_by, "searchable": self.searchable,
        }

    def query(self) -> str:
        """The filters as a query string, without the page (for the pager and the URL)."""
        values = {key: value for key, value in self.for_service().items() if value not in (None, "")}
        if self.searchable is not None:
            values["searchable"] = "true" if self.searchable else "false"
        return urlencode(values)

    @property
    def any_set(self) -> bool:
        return any(value not in (None, "") for value in self.for_service().values())


def _number(raw: str, label: str, ignored: list[str]) -> int | None:
    if not raw:
        return None
    if raw.isdigit():
        return int(raw)
    ignored.append(label)
    return None


def parse_repository_filters(query: QueryDict) -> RepositoryFilters:
    """Read in the order of the form's fields, so the "ignored" note names them in that order."""
    ignored: list[str] = []
    activity = _number(query.get("activity", ""), "activity", ignored)
    document_type = _number(query.get("document_type", ""), "type", ignored)
    review_state = query.get("review_state", "")
    if review_state and review_state not in STATE_LABELS:
        ignored.append("state")
        review_state = ""
    uploaded_by = _number(query.get("uploaded_by", ""), "uploader", ignored)
    try:
        searchable = parse_bool(query.get("searchable"), "searchable")
    except ValidationError:  # anything but true/false
        ignored.append("searchable")
        searchable = None
    return RepositoryFilters(
        q=query.get("q", "").strip(), activity=activity, document_type=document_type, review_state=review_state,
        uploaded_by=uploaded_by, searchable=searchable, page=query.get("page", "1"), ignored=ignored,
    )


def repository(member: TBIMember, filters: RepositoryFilters) -> dict:
    result = services.list_documents(filters.for_service(), page=filters.page)
    return {"result": result, "rows": [document_row(member, d) for d in result.items]}


def review_page(member: TBIMember, page: str) -> dict:
    """services.review_queue (oldest first; empty without can_approve_documents), 20 a page."""
    result: PageResult = _paginate(services.review_queue(member), page, services.DEFAULT_PAGE_SIZE)
    return {"result": result, "rows": [document_row(member, d) for d in result.items]}


def filter_choices() -> dict:
    """Lists for the repository's filter selects (inactive types too, so old documents can still be found)."""
    listing = services.documents_for_listing()
    return {
        "activities": Activity.objects.filter(documents__isnull=False, documents__related_certificate__isnull=True)
        .distinct().order_by("-start_date", "title").only("pk", "title"),
        "document_types": DocumentType.objects.order_by("name"),
        "uploaders": TBIMember.objects.filter(pk__in=listing.values("uploaded_by")).order_by("name").only("pk", "name"),
        "review_states": REVIEW_STATE_CHOICES,
    }


# --- After an action --------------------------------------------------------------------------------------------


def safe_next(request: HttpRequest, fallback: str) -> str:
    """Where to go after an action: the posted `next` when it is a page of this site, otherwise `fallback`."""
    target = request.POST.get("next") or request.GET.get("next") or ""
    if target and url_has_allowed_host_and_scheme(target, allowed_hosts={request.get_host()}, require_https=request.is_secure()):
        return target
    return fallback


def place_of(document: Document) -> str:
    """Where a document lives: its certificate's tab (evidence) or its activity's Documents tab."""
    tab = "certificates" if document.related_certificate_id else "documents"
    return f"{reverse('activities:detail', args=[document.activity_id])}?tab={tab}"


# --- Documents on the New activity form (ASSUMPTIONS 327–328) ---------------------------------------------------

_NEW_DOCUMENT_FIELD = re.compile(r"^documents-(\d{1,4})-(type|file|title)$")


@dataclass
class NewDocumentRow:
    """One optional document row of the New activity form, as posted (the file itself is not kept: browsers clear
    file inputs after an error, so the member chooses it again)."""

    key: int
    document_type: int | None = None
    title: str = ""
    file: object = None
    errors: dict[str, str] = field(default_factory=dict)


def new_document_rows(request: HttpRequest) -> list[NewDocumentRow]:
    """The posted rows `documents-<key>-type|file|title`, in key order. A row left completely empty is ignored, so
    an untouched section creates the activity exactly as before."""
    rows: dict[int, NewDocumentRow] = {}
    for name in [*request.POST.keys(), *request.FILES.keys()]:
        match = _NEW_DOCUMENT_FIELD.match(name)
        if match:
            rows.setdefault(int(match.group(1)), NewDocumentRow(key=int(match.group(1))))
    for key, row in rows.items():
        raw_type = request.POST.get(f"documents-{key}-type", "").strip()
        row.document_type = int(raw_type) if raw_type.isdigit() else None
        row.title = request.POST.get(f"documents-{key}-title", "").strip()
        row.file = request.FILES.get(f"documents-{key}-file")
    return [row for _, row in sorted(rows.items()) if row.document_type or row.title or row.file]


def new_documents_for_service(rows: list[NewDocumentRow]) -> list[services.NewDocument]:
    """The rows as services.NewDocument. An unknown type id becomes None ("Choose a document type."); a type that is
    not allowed at creation is passed on, so the service refuses it with its own message."""
    types = DocumentType.objects.in_bulk([row.document_type for row in rows if row.document_type])
    return [services.NewDocument(types.get(row.document_type), row.file, row.title) for row in rows]


def put_row_errors(rows: list[NewDocumentRow], row_errors: dict[int, dict[str, str]]) -> None:
    """The service's errors (by row position) under each row's fields."""
    for index, errors in row_errors.items():
        rows[index].errors = errors


def new_documents_context(rows: list[NewDocumentRow]) -> dict:
    """What the Documents (optional) section shows: the allowed types, the posted rows and the file limits."""
    return {
        "types": services.types_allowed_at_creation().order_by("name"),
        "rows": rows,
        "empty_row": NewDocumentRow(key=0),  # the one row shown without JavaScript
        "next_key": max((row.key for row in rows), default=-1) + 1,
        "limits": upload_limits(),
        "max_rows": services.MAX_DOCUMENTS_AT_CREATION,
        "choose_again": any(row.file for row in rows),  # files were chosen but the browser cleared them
    }


# --- Preview (ASSUMPTIONS 329) ------------------------------------------------------------------------------------


def preview_kind(document: Document) -> str:
    """pdf | image | none, from services.preview_content_type (the confirmed stored type, not the file name)."""
    content_type = services.preview_content_type(document)
    if content_type == "application/pdf":
        return "pdf"
    return "image" if content_type else "none"
