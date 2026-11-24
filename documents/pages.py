"""Display helpers for the Documents pages (phase F4): the activity's Documents tab, the repository, the review queue
and the document page. Display only: every rule is in documents.services (who may upload, review, replace or delete;
the checklist; the review state). This module turns their results into what the templates show.
"""

import re
from dataclasses import dataclass, field
from urllib.parse import parse_qs, urlencode, urlsplit

from django.conf import settings
from django.core.exceptions import ValidationError
from django.http import HttpRequest, QueryDict
from django.urls import reverse
from django.utils.http import url_has_allowed_host_and_scheme

from accounts.models import TBIMember
from accounts.permissions import Permission, has_permission
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
    required, payment = [], []
    for item in checklist["required"]:
        label, tone = checklist_state(item)
        # Payment documents for the Supply Office get their own group (ASSUMPTIONS 425).
        (payment if item.is_payment else required).append({
            "document_type": item.document_type,
            "due_date": item.due_date,
            "state": {"label": label, "tone": tone},
            "is_overdue": item.is_overdue,
            "rows": [document_row(member, document) for document in item.documents],
            # Event Photos: one or more pictures, so an "Add a photo" link stays after the first (ASSUMPTIONS 425).
            "accepts_more": item.document_type.name in settings.DOCUMENT_TYPES_IMAGES_ONLY,
        })
    optional = [  # only the optional types that have documents
        {"document_type": entry["document_type"], "rows": [document_row(member, d) for d in entry["documents"]]}
        for entry in checklist["optional"] if entry["documents"]
    ]
    upload = upload or upload_form_from(request)
    focus = next((key for key in ("document_type", "title", "file") if key in upload.errors), None)
    if focus is None and request.GET.get("focus") == "type":  # "Upload other document": straight to the dropdown
        focus = "document_type"
    upload_types = list(
        DocumentType.objects.filter(is_active=True).exclude(name=_certificate_type_name()).order_by("name")
    )
    return {
        "today": checklist["today"],
        "required": required,
        "payment": payment,
        "payment_packet": {  # "Download payment packet": the reason replaces the button while refused
            "url": reverse("activities:payment_packet", args=[activity.pk]),
            "refused": services.payment_packet_refused_reason(activity) if payment else None,
        },
        "optional": optional,
        "upload": upload,
        "upload_focus": focus,  # the first field with an error gets the focus (works without JavaScript)
        "upload_focus_file": focus == "file",
        # The dropdown in two groups (ASSUMPTIONS 386): what the checklist requires, then everything else.
        "upload_groups": [
            {"label": label, "types": types}
            for label, types in (("Required", [t for t in upload_types if t.is_required]),
                                 ("Other documents", [t for t in upload_types if not t.is_required]))
            if types
        ],
        "optional_type_names": [t.name for t in upload_types if not t.is_required],
        "other_title_required": DocumentType.OTHER,
        "limits": upload_limits(),
        "back": f"{reverse('activities:detail', args=[activity.pk])}?tab=documents",
    }


def _certificate_type_name() -> str:
    """Certificate evidence has its own upload (Certificates tab), so its type is not offered here."""
    from certificates.services import CERTIFICATE_DOCUMENT_TYPE

    return CERTIFICATE_DOCUMENT_TYPE


# --- Repository ("By activity" and "All documents") and review queue -------------------------------------------

VIEW_BY_ACTIVITY, VIEW_ALL = "activity", "all"
VIEWS = [(VIEW_BY_ACTIVITY, "By activity", "layout-grid"), (VIEW_ALL, "All documents", "list")]
MISSING = services.MISSING_STATE
STATE_CHOICES = REVIEW_STATE_CHOICES + [(MISSING, MISSING_LABEL[0])]  # Missing: the "By activity" view only
CARDS_PER_PAGE = 10  # activity cards per page


@dataclass
class RepositoryFilters:
    """The Documents page's view and filters from the query string (all kept in the URL). A value that cannot be read
    is ignored with a note (as the Activities list, item 262), never a 400 page. `review_state=` (older links) is read
    as `state=`. "Missing" exists only in the "By activity" view, so it switches to it."""

    view: str = VIEW_BY_ACTIVITY
    q: str = ""
    activity: int | None = None
    document_type: int | None = None
    state: str = ""
    uploaded_by: int | None = None  # no field in the form any more (ASSUMPTIONS 352); still read from links
    searchable: bool | None = None
    page: str = "1"
    ignored: list[str] = field(default_factory=list)

    def for_service(self) -> dict:
        """services.list_documents's filters (the "All documents" view; never "missing")."""
        return {
            "q": self.q, "activity": self.activity, "document_type": self.document_type,
            "review_state": self.state if self.state != MISSING else "", "uploaded_by": self.uploaded_by,
            "searchable": self.searchable,
        }

    def values(self, **changes) -> dict:
        """The URL's values (without the page), with `changes` applied; empty values and the default view left out."""
        values = {
            "view": self.view, "q": self.q, "activity": self.activity, "document_type": self.document_type,
            "state": self.state, "uploaded_by": self.uploaded_by,
            "searchable": None if self.searchable is None else ("true" if self.searchable else "false"),
        }
        values.update(changes)
        if values["view"] == VIEW_BY_ACTIVITY:
            values["view"] = None
        return {key: value for key, value in values.items() if value not in (None, "")}

    def query(self, **changes) -> str:
        """The filters as a query string, without the page (for the pager, the cards, the view switch and the URL)."""
        return urlencode(self.values(**changes))

    def url(self, **changes) -> str:
        """/documents/ with these filters (and `changes`)."""
        query = self.query(**changes)
        return f"{reverse('documents:list')}?{query}" if query else reverse("documents:list")

    @property
    def any_set(self) -> bool:
        return any(value not in (None, "") for value in (self.q, self.activity, self.document_type, self.state,
                                                          self.uploaded_by, self.searchable))


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
    view = query.get("view", "") or VIEW_BY_ACTIVITY
    if view not in (VIEW_BY_ACTIVITY, VIEW_ALL):
        ignored.append("view")
        view = VIEW_BY_ACTIVITY
    activity = _number(query.get("activity", ""), "activity", ignored)
    document_type = _number(query.get("document_type", ""), "type", ignored)
    state = query.get("state", "") or query.get("review_state", "")
    if state and state not in dict(STATE_CHOICES):
        ignored.append("state")
        state = ""
    if state == MISSING:
        view = VIEW_BY_ACTIVITY  # a missing document has no row in the flat list
    uploaded_by = _number(query.get("uploaded_by", ""), "uploader", ignored)
    try:
        searchable = parse_bool(query.get("searchable"), "searchable")
    except ValidationError:  # anything but true/false
        ignored.append("AI search")
        searchable = None
    return RepositoryFilters(
        view=view, q=query.get("q", "").strip(), activity=activity, document_type=document_type, state=state,
        uploaded_by=uploaded_by, searchable=searchable, page=query.get("page", "1"), ignored=ignored,
    )


def summary_cards(counts: dict[str, int], filters: RepositoryFilters, can_approve: bool,
                  review: bool = True) -> list[dict]:
    """The cards at the top (services.document_state_counts). A card is a link that filters the page by its
    state; the selected one links back to no state. "Missing" opens the "By activity" view. `review` False
    (services.review_in_use, ASSUMPTIONS 432: uploads are approved copies): only Missing and Approved."""
    waiting = "Waiting for approval" if can_approve else "Waiting for the Director"
    cards = [
        (Document.WAITING_FOR_REVIEW, waiting, "file-check", "blue"),
        (Document.RETURNED, "Returned", "refresh-cw", "amber"),
        (MISSING, "Missing", "circle-alert", "violet"),
        (Document.APPROVED, "Approved", "circle-check", "green"),
    ]
    if not review:
        cards = [card for card in cards if card[0] in (MISSING, Document.APPROVED)]
    result = []
    for key, label, icon, chip in cards:
        selected = filters.state == key
        view = VIEW_BY_ACTIVITY if key == MISSING else filters.view
        result.append({
            "key": key, "label": label, "icon": icon, "chip": chip, "count": counts[key], "selected": selected,
            "url": filters.url(state="" if selected else key, view=view),
        })
    return result


def view_links(filters: RepositoryFilters) -> list[dict]:
    """The "By activity / All documents" switch (the filters stay; "Missing" only exists in "By activity")."""
    links = []
    for key, label, icon in VIEWS:
        state = "" if key == VIEW_ALL and filters.state == MISSING else filters.state
        links.append({"key": key, "label": label, "icon": icon, "selected": filters.view == key,
                      "url": filters.url(view=key, state=state)})
    return links


# One line of an activity card: a document, or a required type with nothing uploaded ("missing").


def _document_line(member: TBIMember, document: Document, type_name: str, first_of_type: bool,
                   item: services.ChecklistItem | None) -> dict:
    return {
        **document_row(member, document),
        "kind": "document",
        "type_id": document.document_type_id,
        "type_name": type_name,
        "first_of_type": first_of_type,  # later uploads of the same type are indented under the first
        "is_overdue": bool(item and first_of_type and item.is_overdue),
        "due_date": item.due_date if item and first_of_type else None,
    }


def _missing_line(item: services.ChecklistItem, activity: Activity, counted: bool) -> dict:
    label, tone = MISSING_LABEL
    query = urlencode({"tab": "documents", "type": item.document_type.pk})
    return {
        "kind": MISSING, "type_id": item.document_type.pk, "type_name": item.document_type.name,
        "state": {"label": label, "tone": tone}, "due_date": item.due_date, "is_overdue": item.is_overdue,
        "counted": counted, "first_of_type": True,
        "upload_url": f"{reverse('activities:detail', args=[activity.pk])}?{query}",
    }


def _lines(member: TBIMember, entry: services.ActivityDocuments) -> list[dict]:
    """Required types in the checklist's order (each upload, newest first, or one "missing" line), then the other
    documents (newest first)."""
    lines = []
    for item in entry.required:
        if not item.documents:
            lines.append(_missing_line(item, entry.activity, entry.counts_missing))
        for position, document in enumerate(item.documents):
            lines.append(_document_line(member, document, item.document_type.name, position == 0, item))
    for document in entry.other:
        lines.append(_document_line(member, document, document.document_type.name, True, None))
    return lines


def _line_matches(line: dict, filters: RepositoryFilters, activity_matches: bool) -> bool:
    """The filters on one line (display only: the states come from the services' review_state / checklist)."""
    if filters.document_type and line["type_id"] != filters.document_type:
        return False
    if line["kind"] == MISSING:
        if filters.searchable is not None or filters.uploaded_by:
            return False  # nothing uploaded: no uploader, nothing for the AI search
        if filters.state and (filters.state != MISSING or not line["counted"]):
            return False  # "Missing" counts non-Draft activities only, like the card
        return activity_matches or not filters.q or filters.q.lower() in line["type_name"].lower()
    document = line["document"]
    if filters.state and document.review_state != filters.state:
        return False
    if filters.searchable is not None and line["searchable"] != filters.searchable:
        return False
    if filters.uploaded_by and document.uploaded_by_id != filters.uploaded_by:
        return False
    if activity_matches or not filters.q:
        return True
    text = filters.q.lower()
    return any(text in value.lower() for value in (document.title, document.file_name, line["type_name"]))


def _activity_card(member: TBIMember, entry: services.ActivityDocuments, filters: RepositoryFilters) -> dict | None:
    """One activity card, or None when the filters leave nothing in it. Progress = required types approved."""
    activity = entry.activity
    activity_matches = bool(filters.q) and filters.q.lower() in activity.title.lower()
    lines = [line for line in _lines(member, entry) if _line_matches(line, filters, activity_matches)]
    if filters.any_set and not lines:
        return None
    required, approved, missing = len(entry.required), entry.approved_count, entry.missing_count
    pending = any(document.review_state != Document.APPROVED for document in entry.documents)
    return {
        "activity": activity,
        "lines": lines,
        "required": required,
        "approved": approved,
        "missing": missing,
        "complete": required > 0 and approved == required,
        "percent": round(100 * approved / required) if required else 0,
        "progress_label": f"{approved} of {required} required documents approved",
        # Something to do (missing, waiting or returned) or a filter in use: open; complete ones start closed.
        "open": filters.any_set or missing > 0 or pending,
        "documents_url": f"{reverse('activities:detail', args=[activity.pk])}?tab=documents",
    }


def repository_page(member: TBIMember, filters: RepositoryFilters, now=None) -> dict:
    """The Documents page: the summary cards and either the activity cards ("By activity") or the flat table ("All
    documents", services.list_documents). Every activity of services.overview_activities() is loaded (three
    queries), so the cards' numbers cover every activity document whatever the filters."""
    activities = list(services.overview_activities())
    overview = services.activity_document_overview(activities, now)
    context = {
        "summary_cards": summary_cards(services.document_state_counts(overview), filters,
                                       has_permission(member, Permission.APPROVE_DOCUMENTS), services.review_in_use()),
        "view_links": view_links(filters),
        "activity_choices": activities,
    }
    if filters.view == VIEW_ALL:
        return {**context, **repository(member, filters)}
    shown = [entry for entry in overview if not filters.activity or entry.activity.pk == filters.activity]
    cards = [card for card in (_activity_card(member, entry, filters) for entry in shown) if card]
    result = _paginate(cards, filters.page, CARDS_PER_PAGE)
    return {**context, "result": result, "cards": result.items}


def repository(member: TBIMember, filters: RepositoryFilters) -> dict:
    result = services.list_documents(filters.for_service(), page=filters.page)
    return {"result": result, "rows": [document_row(member, d) for d in result.items]}


def review_page(member: TBIMember, page: str) -> dict:
    """services.review_queue (oldest first; empty without can_approve_documents), 20 a page."""
    result: PageResult = _paginate(services.review_queue(member), page, services.DEFAULT_PAGE_SIZE)
    return {"result": result, "rows": [document_row(member, d) for d in result.items]}


def filter_choices() -> dict:
    """Lists for the filter selects (inactive types too, so old documents can still be found). The activities come
    with repository_page (`activity_choices`, the activities the page lists). Waiting / Returned only while review is
    in use (services.review_in_use, ASSUMPTIONS 432)."""
    states = STATE_CHOICES if services.review_in_use() else [
        (key, label) for key, label in STATE_CHOICES if key in (Document.APPROVED, MISSING)]
    return {"document_types": DocumentType.objects.order_by("name"), "states": states}


# --- After an action --------------------------------------------------------------------------------------------


def safe_next(request: HttpRequest, fallback: str) -> str:
    """Where to go after an action: the posted `next` when it is a page of this site, otherwise `fallback`."""
    target = request.POST.get("next") or request.GET.get("next") or ""
    if target and url_has_allowed_host_and_scheme(target, allowed_hosts={request.get_host()}, require_https=request.is_secure()):
        return target
    return fallback


def back_link(request: HttpRequest, document: Document) -> dict:
    """The document page's Back link (ASSUMPTIONS 359): the page the member came from (?next=, internal paths only:
    the Documents page with its filters and view, the review tab, or the activity's tab), otherwise the document's
    place (place_of). {"url", "label"}."""
    from config.links import is_internal_path

    activity_title = document.activity.title
    where = "the certificates" if document.related_certificate_id else "the documents"
    fallback = {"url": place_of(document), "label": f"Back to {where} of {activity_title}"}
    target = request.GET.get("next", "")
    if not is_internal_path(request, target):
        return fallback
    path = urlsplit(target).path
    if path.startswith(reverse("documents:list")):
        label = "Back to Documents"
    elif path == reverse("activities:detail", args=[document.activity_id]):
        tab = parse_qs(urlsplit(target).query).get("tab", [""])[0]
        label = f"Back to the {tab} of {activity_title}" if tab in ("documents", "certificates") else f"Back to {activity_title}"
    else:
        label = "Back"
    return {"url": target, "label": label}


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
    label: str = ""  # the two fixed rows only: the type's name (ASSUMPTIONS 405)

    @property
    def is_filled_in(self) -> bool:
        """A file chosen or a title typed: the row is sent to the service (an untouched row is not, ASSUMPTIONS 417)."""
        return bool(self.file or self.title)

    @property
    def chosen_name(self) -> str:
        """The name of the file chosen before an error (shown in the "choose it again" note; never used in a path)."""
        return (getattr(self.file, "name", "") or "")[:80]


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


def required_document_rows(request: HttpRequest) -> list[NewDocumentRow]:
    """The two fixed rows of the New activity form (ASSUMPTIONS 405, optional since 417): one per
    services.creation_required_types() type (the Activity Design and the Request Letter), posted as
    `required-<type id>-file|title`. Always every type (the page shows each); the view sends only the filled-in ones
    (`is_filled_in`), so a title without a file gets "Choose a file to upload." and a file without a title gets
    creation_title_error (410)."""
    rows = []
    for document_type in services.creation_required_types().order_by("name"):
        row = NewDocumentRow(key=document_type.pk, document_type=document_type.pk, label=document_type.name)
        if request.method == "POST":
            row.title = request.POST.get(f"required-{document_type.pk}-title", "").strip()
            row.file = request.FILES.get(f"required-{document_type.pk}-file")
        rows.append(row)
    return rows


def new_documents_for_service(rows: list[NewDocumentRow]) -> list[services.NewDocument]:
    """The rows as services.NewDocument. An unknown type id becomes None ("Choose a document type."); a type that is
    not allowed at creation is passed on, so the service refuses it with its own message."""
    types = DocumentType.objects.in_bulk([row.document_type for row in rows if row.document_type])
    return [services.NewDocument(types.get(row.document_type), row.file, row.title) for row in rows]


def put_row_errors(rows: list[NewDocumentRow], row_errors: dict[int, dict[str, str]]) -> None:
    """The service's errors (by row position) under each row's fields."""
    for index, errors in row_errors.items():
        rows[index].errors = errors


def new_documents_context(rows: list[NewDocumentRow], required: list[NewDocumentRow] | None = None) -> dict:
    """What the Documents section shows: the two fixed rows (ASSUMPTIONS 405, optional since 417), the types offered
    for the other rows (the two left out: they have their own fields), the posted rows and the file limits.
    `choose_again`: "Activity Design (design.pdf)" for every file chosen before an error (browsers clear file inputs,
    so the member chooses them again; everything typed is kept)."""
    required = required or []
    types = services.types_allowed_at_creation().exclude(pk__in=[row.document_type for row in required]).order_by("name")
    names = dict(DocumentType.objects.filter(pk__in=[row.document_type for row in rows if row.document_type])
                 .values_list("pk", "name"))
    choose_again = [
        f"{row.label or names.get(row.document_type, 'Document')} ({row.chosen_name})"
        for row in [*required, *rows] if row.file
    ]
    return {
        "required": required,
        "types": types,
        "rows": rows,
        "empty_row": NewDocumentRow(key=0),  # the one row shown without JavaScript
        "next_key": max((row.key for row in rows), default=-1) + 1,
        "limits": upload_limits(),
        "max_total": services.MAX_DOCUMENTS_AT_CREATION,
        "max_rows": services.MAX_DOCUMENTS_AT_CREATION - len(required),  # other rows: the limit counts the two fixed ones
        "choose_again": choose_again,
    }


# --- Preview (ASSUMPTIONS 329) ------------------------------------------------------------------------------------


def preview_kind(document: Document) -> str:
    """pdf | image | none, from services.preview_content_type (the confirmed stored type, not the file name)."""
    content_type = services.preview_content_type(document)
    if content_type == "application/pdf":
        return "pdf"
    return "image" if content_type else "none"
