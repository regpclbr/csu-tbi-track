"""The Documents pages (phase F4): the repository and the review queue (/documents/, /documents/review/), the document
page, uploads from the activity's Documents tab and the review actions. Thin views: every rule is in
documents.services (which checks the permissions again); documents/pages.py prepares what the templates show.

Viewing and uploading: any active staff member. Approve and return: can_approve_documents (ASSUMPTIONS 344; a member
without it gets 403 from these URLs and from the review tab), approve also for an own upload, return never for an own
upload, never a returned document. Replace and delete: the uploader only, while For Review. Other refusals come back
as toasts with the service's message. The actions work for certificate evidence too (the same Step 6 rules).
"""

from django.contrib import messages
from django.core.exceptions import PermissionDenied, ValidationError
from django.http import HttpRequest, HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.views.decorators.http import require_GET, require_POST

from accounts.permissions import Permission, has_permission, is_htmx, permission_required, staff_login_required
from activities.models import Activity
from activities.page_views import render_activity_page
from assistant.indexing import with_searchable

from . import pages, services
from .models import Document, DocumentType

ACTIONS = ("return", "replace", "delete")  # the actions that ask something first (a reason, a file, "are you sure")


def _document(document_id: int) -> Document:
    """Any document: an activity document or certificate evidence (the services apply the same rules to both)."""
    queryset = with_searchable(Document.objects.all()).select_related(
        "activity", "document_type", "status", "uploaded_by", "reviewed_by", "related_certificate"
    )
    return get_object_or_404(queryset, pk=document_id)


# --- Repository and review queue -----------------------------------------------------------------------------


def _list_context(request: HttpRequest, tab: str) -> dict:
    return {
        "page_title": "Documents",
        "list_tab": tab,
        "review_count": services.review_queue(request.user).count(),  # the same number as the sidebar badge
        "can_review": has_permission(request.user, Permission.APPROVE_DOCUMENTS),  # shows the review tab
        "list_url": reverse("documents:list"),
    }


@staff_login_required
@require_GET
def document_list(request: HttpRequest) -> HttpResponse:
    """GET view=activity|all, q, activity, document_type, state (waiting_for_review|returned|approved|missing),
    searchable=true|false, page (and uploaded_by from older links). Default: "By activity" (ASSUMPTIONS 352). An HTMX
    request (a filter, a card, the view switch or a page) gets the part below the title (documents/_repository.html)."""
    filters = pages.parse_repository_filters(request.GET)
    context = {
        **_list_context(request, "all"), "filters": filters,
        **pages.repository_page(request.user, filters), **pages.filter_choices(),
    }
    if is_htmx(request):
        return render(request, "documents/_repository.html", context)
    return render(request, "documents/list.html", context)


@permission_required(Permission.APPROVE_DOCUMENTS)
@require_GET
def review_list(request: HttpRequest) -> HttpResponse:
    """"Waiting for my review": services.review_queue (my own uploads too, never returned ones)."""
    context = {**_list_context(request, "review"), **pages.review_page(request.user, request.GET.get("page", "1"))}
    if is_htmx(request):
        return render(request, "documents/_results.html", context)
    return render(request, "documents/list.html", context)


# --- The document page ---------------------------------------------------------------------------------------


@staff_login_required
@require_GET
def document_detail(request: HttpRequest, document_id: int) -> HttpResponse:
    document = _document(document_id)
    row = pages.document_row(request.user, document)
    context = {"page_title": document.title, **row, "view_row": row, "place": pages.place_of(document)}
    return render(request, "documents/detail.html", context)


@staff_login_required
@require_GET
def preview(request: HttpRequest, document_id: int) -> HttpResponse:
    """The preview panel (ASSUMPTIONS 329): an HTMX request gets it for the shared preview dialog
    (documents/_preview_dialog.html); without JavaScript it is its own page. Activity documents only (evidence: 404).
    The file itself comes from views.view_document (PDF in an iframe, images as <img>); other types: Download."""
    document = get_object_or_404(services.documents_for_listing(), pk=document_id)
    context = {
        "document": document,
        "state": pages.state_badge(document),
        "kind": pages.preview_kind(document),
        "download_url": pages.download_url(document),
        "file_url": reverse("documents:view", args=[document.pk]),
    }
    if is_htmx(request):
        return render(request, "documents/_preview.html", context)
    return render(request, "documents/preview_page.html", {"page_title": document.title, **context})


# --- Upload (any active staff member) ------------------------------------------------------------------------


@staff_login_required
@require_POST
def upload(request: HttpRequest, activity_id: int) -> HttpResponse:
    """POST document_type, title (optional), file. Errors come back under their fields on the Documents tab."""
    activity = get_object_or_404(Activity.objects.select_related("status", "activity_type", "created_by"), pk=activity_id)
    raw_type = request.POST.get("document_type", "")
    form = pages.UploadForm(document_type=int(raw_type) if raw_type.isdigit() else None, title=request.POST.get("title", ""))
    document_type = DocumentType.objects.filter(pk=form.document_type).first() if form.document_type else None
    if document_type is None:
        form.errors["document_type"] = "Choose the document type."
    else:
        try:
            document = services.upload_document(request.user, activity, document_type, request.FILES.get("file"), form.title)
        except ValidationError as error:
            form.errors = {key: " ".join(messages_) for key, messages_ in error.message_dict.items()}
        else:
            messages.success(request, f'"{document.title}" was uploaded. It is waiting for review.')
            return redirect(pages.place_of(document))
    return render_activity_page(request, activity, "documents", {"upload": form}, status=400)


# --- Review and the uploader's actions --------------------------------------------------------------------------


def _refused(request: HttpRequest, error: Exception) -> None:
    """The service's refusal as an error toast (e.g. "already approved", or returning your own upload)."""
    messages.error(request, str(error) or "You do not have permission to do this.")


def _done(request: HttpRequest, target: str) -> HttpResponse:
    """After an action: HTMX (the modal's form) is told to load the page; otherwise a normal redirect."""
    if is_htmx(request):
        response = HttpResponse()
        response["HX-Redirect"] = target
        return response
    return redirect(target)


@staff_login_required
@require_GET
def action_form(request: HttpRequest, document_id: int, action: str) -> HttpResponse:
    """GET the form of return (a reason), replace (a file) or delete (a confirmation): the modal's content for HTMX,
    a page of its own without JavaScript."""
    if action not in ACTIONS:
        return redirect("documents:detail", document_id=document_id)
    document = _document(document_id)
    context = {"document": document, "action": action, "next": pages.safe_next(request, ""), "errors": {},
               "limits": pages.upload_limits(), "reason_max": services.RETURN_REASON_MAX_LENGTH, "page_title": document.title}
    template = "documents/_action_form.html" if is_htmx(request) else "documents/action_page.html"
    return render(request, template, context)


def _form_again(request: HttpRequest, document: Document, action: str, errors: dict, reason: str = "") -> HttpResponse:
    """The action's form with the service's errors under its field: inside the modal (HTMX) or as its own page."""
    context = {"document": document, "action": action, "next": pages.safe_next(request, ""), "errors": errors,
               "reason": reason, "limits": pages.upload_limits(), "reason_max": services.RETURN_REASON_MAX_LENGTH,
               "page_title": document.title}
    if is_htmx(request):
        return render(request, "documents/_action_form.html", context)  # 200: HTMX swaps it into the modal
    return render(request, "documents/action_page.html", context, status=400)


@permission_required(Permission.APPROVE_DOCUMENTS)
@require_POST
def approve(request: HttpRequest, document_id: int) -> HttpResponse:
    document = _document(document_id)
    back = pages.safe_next(request, reverse("documents:detail", args=[document.pk]))
    try:
        services.approve_document(request.user, document)
    except (PermissionDenied, services.DocumentError) as error:
        _refused(request, error)
    else:
        messages.success(request, f'"{document.title}" was approved.')
    return _done(request, back)


@permission_required(Permission.APPROVE_DOCUMENTS)
@require_POST
def return_document(request: HttpRequest, document_id: int) -> HttpResponse:
    """POST reason (required, at most services.RETURN_REASON_MAX_LENGTH characters)."""
    document = _document(document_id)
    back = pages.safe_next(request, reverse("documents:detail", args=[document.pk]))
    reason = request.POST.get("reason", "")
    try:
        services.return_document(request.user, document, reason)
    except ValidationError as error:
        return _form_again(request, document, "return", {k: " ".join(v) for k, v in error.message_dict.items()}, reason)
    except (PermissionDenied, services.DocumentError) as error:
        _refused(request, error)
    else:
        messages.success(request, f'"{document.title}" was returned to {document.uploaded_by.name}.')
    return _done(request, back)


@staff_login_required
@require_POST
def replace(request: HttpRequest, document_id: int) -> HttpResponse:
    """POST file. The same file checks as an upload; the document then waits for a reviewer again."""
    document = _document(document_id)
    back = pages.safe_next(request, reverse("documents:detail", args=[document.pk]))
    try:
        services.replace_document_file(request.user, document, request.FILES.get("file"))
    except ValidationError as error:
        return _form_again(request, document, "replace", {k: " ".join(v) for k, v in error.message_dict.items()})
    except (PermissionDenied, services.DocumentError) as error:
        _refused(request, error)
    else:
        messages.success(request, f'The file of "{document.title}" was replaced. It is waiting for review again.')
    return _done(request, back)


@staff_login_required
@require_POST
def delete(request: HttpRequest, document_id: int) -> HttpResponse:
    document = _document(document_id)
    place = pages.place_of(document)
    back = pages.safe_next(request, place)
    if back == reverse("documents:detail", args=[document.pk]):
        back = place  # the document's own page is gone afterwards
    try:
        services.delete_document(request.user, document)
    except (PermissionDenied, services.DocumentError) as error:
        _refused(request, error)
        back = pages.safe_next(request, reverse("documents:detail", args=[document.pk]))
    else:
        messages.success(request, f'"{document.title}" was deleted.')
    return _done(request, back)
