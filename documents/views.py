"""JSON API for documents, the checklist and the review queue, plus the file download view.

Views are thin: parse input, call documents.services (which checks the permissions again).
- Any active staff member: read, upload, download.
- can_approve_documents (ASSUMPTIONS 344): approve (also an own upload), return (never an own upload).
- The uploader only, while For Review: replace, delete.

Files are never served from /media/: only download_document sends them (as attachments) and view_document shows a
PDF or image inline inside our own preview dialog (the only view that may be framed, and only by this site).
"""

import json
from collections.abc import Callable
from functools import wraps

from django.core.exceptions import ValidationError
from django.http import FileResponse, Http404, HttpRequest
from django.utils.http import content_disposition_header
from django.contrib import messages
from django.shortcuts import get_object_or_404, redirect
from django.urls import reverse
from django.views.decorators.clickjacking import xframe_options_sameorigin
from django.views.decorators.http import require_GET, require_POST

from config.json import JsonResponse  # date-times in Manila time

from accounts.permissions import Permission, permission_required, staff_login_required
from activities.models import Activity
from config.links import activity_tab_url
from config.parsing import parse_bool

from . import services
from .models import Document, DocumentType


class BadRequest(Exception):
    """Invalid request body or query parameters."""


def api_errors(view: Callable) -> Callable:
    """Turn refusals and validation errors into 400 responses with clear messages."""

    @wraps(view)
    def wrapper(request: HttpRequest, *args, **kwargs) -> JsonResponse:
        try:
            return view(request, *args, **kwargs)
        except ValidationError as error:
            errors = error.message_dict if hasattr(error, "error_dict") else {"__all__": error.messages}
            return JsonResponse({"errors": errors}, status=400)
        except (services.DocumentError, BadRequest) as error:
            return JsonResponse({"error": str(error)}, status=400)

    return wrapper


def _json_body(request: HttpRequest) -> dict:
    try:
        data = json.loads(request.body or b"{}")
    except json.JSONDecodeError as error:
        raise BadRequest("Request body must be valid JSON.") from error
    if not isinstance(data, dict):
        raise BadRequest("Request body must be a JSON object.")
    return data


def _int_value(value: str | None, name: str) -> int | None:
    if not value:
        return None
    if not str(value).isdigit():
        raise BadRequest(f"{name} must be a number.")
    return int(value)


def _document(document_id: int) -> Document:
    """Activity documents only; certificate files (Step 9) give 404 here."""
    return get_object_or_404(services.documents_for_listing(), pk=document_id)


def _member_json(member) -> dict | None:
    return {"member_id": member.pk, "name": member.name} if member else None


def document_json(document: Document, member=None) -> dict:
    data = {
        "document_id": document.pk,
        "activity_id": document.activity_id,
        "activity_title": document.activity.title,
        "document_type_id": document.document_type_id,
        "document_type": document.document_type.name,
        "title": document.title,
        "file_name": document.file_name,
        "file_extension": document.file_extension,
        "file_size_kb": document.file_size_kb,
        "status": document.status.status_name,
        "review_state": document.review_state,  # waiting_for_review | returned | approved
        "searchable": bool(getattr(document, "searchable", False)),  # indexed for the assistant (Step 8)
        "uploaded_by": _member_json(document.uploaded_by),
        "uploaded_at": document.uploaded_at,
        "reviewed_by": _member_json(document.reviewed_by),
        "reviewed_at": document.reviewed_at,
        "updated_at": document.updated_at,
        "download_url": reverse("documents:download", args=[document.pk]),
    }
    if member is not None:
        data["allowed_actions"] = services.allowed_actions(member, document)
    return data


def _page_json(result, rows: list) -> dict:
    return {"documents": rows, "total": result.total, "page": result.page, "num_pages": result.num_pages}


# --- Repository, detail, review queue ----------------------------------------------------------


@staff_login_required
@require_GET
@api_errors
def repository_api(request: HttpRequest) -> JsonResponse:
    """GET ?activity=&document_type=&status=&uploaded_by=&review_state=&searchable=true|false&q=&page="""
    filters = {
        key: _int_value(request.GET.get(key), key) for key in ("activity", "document_type", "status", "uploaded_by")
    }
    filters["review_state"] = request.GET.get("review_state", "")
    filters["searchable"] = parse_bool(request.GET.get("searchable"), "searchable")
    filters["q"] = request.GET.get("q", "")
    result = services.list_documents(filters, page=request.GET.get("page", 1))
    return JsonResponse(_page_json(result, [document_json(d) for d in result.items]))


@staff_login_required
@require_GET
def detail_api(request: HttpRequest, document_id: int) -> JsonResponse:
    return JsonResponse({"document": document_json(_document(document_id), request.user)})


@permission_required(Permission.APPROVE_DOCUMENTS)
@require_GET
def review_queue_api(request: HttpRequest) -> JsonResponse:
    """Documents waiting for a reviewer (my own uploads too, not returned ones)."""
    documents = services.review_queue(request.user)
    return JsonResponse({"documents": [document_json(d) for d in documents]})


# --- Checklist ------------------------------------------------------------------------------


@staff_login_required
@require_GET
def checklist_api(request: HttpRequest, activity_id: int) -> JsonResponse:
    activity = get_object_or_404(Activity, pk=activity_id)
    checklist = services.document_checklist(activity)
    return JsonResponse(
        {
            "activity_id": activity.pk,
            "today": checklist["today"],
            "required": [
                {
                    "document_type_id": item.document_type.pk,
                    "document_type": item.document_type.name,
                    "due_date": item.due_date,
                    "state": item.state,
                    "is_overdue": item.is_overdue,
                    "documents": [document_json(d) for d in item.documents],
                }
                for item in checklist["required"]
            ],
            "optional": [
                {
                    "document_type_id": entry["document_type"].pk,
                    "document_type": entry["document_type"].name,
                    "documents": [document_json(d) for d in entry["documents"]],
                }
                for entry in checklist["optional"]
            ],
        }
    )


# --- Changes ----------------------------------------------------------------------------------


@staff_login_required
@require_POST
@api_errors
def upload_api(request: HttpRequest, activity_id: int) -> JsonResponse:
    """POST multipart/form-data: document_type (ID), title (optional), file."""
    activity = get_object_or_404(Activity, pk=activity_id)
    type_id = _int_value(request.POST.get("document_type"), "document_type")
    document_type = DocumentType.objects.filter(pk=type_id).first() if type_id else None
    if document_type is None:
        raise ValidationError({"document_type": ["Choose a valid document type."]})
    document = services.upload_document(
        request.user, activity, document_type, request.FILES.get("file"), request.POST.get("title", "")
    )
    return JsonResponse({"document": document_json(_document(document.pk), request.user)}, status=201)


@staff_login_required
@require_POST
@api_errors
def approve_api(request: HttpRequest, document_id: int) -> JsonResponse:
    document = services.approve_document(request.user, _document(document_id))
    return JsonResponse({"document": document_json(_document(document.pk), request.user)})


@staff_login_required
@require_POST
@api_errors
def return_api(request: HttpRequest, document_id: int) -> JsonResponse:
    """POST JSON {"reason": "..."}"""
    reason = str(_json_body(request).get("reason", ""))
    document = services.return_document(request.user, _document(document_id), reason)
    return JsonResponse({"document": document_json(_document(document.pk), request.user)})


@staff_login_required
@require_POST
@api_errors
def replace_api(request: HttpRequest, document_id: int) -> JsonResponse:
    """POST multipart/form-data: file."""
    document = services.replace_document_file(request.user, _document(document_id), request.FILES.get("file"))
    return JsonResponse({"document": document_json(_document(document.pk), request.user)})


@staff_login_required
@require_POST
@api_errors
def delete_api(request: HttpRequest, document_id: int) -> JsonResponse:
    services.delete_document(request.user, _document(document_id))
    return JsonResponse({"deleted": document_id})


# --- Download (the only way files leave the server) ------------------------------------------------


@staff_login_required
@require_GET
def download_document(request: HttpRequest, document_id: int) -> FileResponse:
    """Any active staff member may download any activity document. Always an attachment, never inline."""
    document = _document(document_id)
    try:
        handle = document.file_path.open("rb")
    except (FileNotFoundError, ValueError) as error:
        raise Http404("The file is missing.") from error
    response = FileResponse(handle, as_attachment=True, filename=document.file_name)
    response["X-Content-Type-Options"] = "nosniff"
    return response


@staff_login_required
@require_GET
def download_payment_packet(request: HttpRequest, activity_id: int):
    """The payment packet zip (ASSUMPTIONS 425): any active staff member, like download_document. Refused (not Conducted,
    nothing Approved): back to the Documents tab with the reason. Never cached."""
    activity = get_object_or_404(Activity.objects.select_related("status"), pk=activity_id)
    try:
        handle, name = services.build_payment_packet(request.user, activity)
    except services.DocumentError as error:
        messages.error(request, str(error))
        return redirect(activity_tab_url(activity.pk, "documents"))
    response = FileResponse(handle, as_attachment=True, filename=name, content_type="application/zip")
    response["X-Content-Type-Options"] = "nosniff"
    response["Cache-Control"] = "private, no-store"
    return response


@staff_login_required
@require_GET
@xframe_options_sameorigin  # the preview dialog's <iframe>; every other page keeps X-Frame-Options: DENY
def view_document(request: HttpRequest, document_id: int) -> FileResponse:
    """Same rule as download_document, but inline, for the preview (ASSUMPTIONS 329): PDF, PNG and JPG only, with the
    type confirmed at upload and checked again on the stored bytes. Anything else, or a missing file: 404."""
    document = _document(document_id)
    try:
        handle = services.open_for_preview(document)
    except (FileNotFoundError, services.DocumentError) as error:
        raise Http404("This file cannot be previewed.") from error
    response = FileResponse(handle, content_type=services.preview_content_type(document))
    response["Content-Disposition"] = content_disposition_header(False, document.file_name)
    response["X-Content-Type-Options"] = "nosniff"
    return response
