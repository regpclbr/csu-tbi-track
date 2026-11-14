"""JSON API and file views for certificates. Views are thin: parse input, check access, call services.

- Any active staff member: list, detail, download the PDF, preview, list/download evidence, upload
  evidence (Step 6 rule).
- can_create_activity: regenerate (Apply the current design), hand over, resend. can_approve_certificates (the
  Director, both Project Heads, the System Administrator): approve an activity's certificates (ASSUMPTIONS 369), reissue
  outdated ones (364), the signatory list (363). Certificates are created by the System (no generate endpoint).
  Evidence review: can_approve_documents (the Step 6 rules).
- can_manage_users (Admin Console): templates and their images. Signature images: can_manage_users or
  can_approve_certificates.
Files are never served from /media/: only through these views, as attachments (inline only for the certificate
preview: the rendered preview, or an Issued certificate's final PDF).
"""

import json
from collections.abc import Callable
from functools import wraps

from django.core.exceptions import ValidationError
from django.http import FileResponse, Http404, HttpRequest, HttpResponse
from django.shortcuts import get_object_or_404
from django.urls import reverse
from django.views.decorators.clickjacking import xframe_options_sameorigin
from django.views.decorators.http import require_GET, require_http_methods, require_POST

from config.json import JsonResponse  # date-times in Manila time

from accounts.permissions import Permission, has_permission, permission_required, require_permission, staff_login_required
from activities.models import Activity
from config.parsing import parse_bool
from documents import services as document_services
from documents.models import Document
from documents.views import document_json

from . import services
from .models import Certificate, CertificateSignatory, CertificateTemplate


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
        except (services.CertificateError, document_services.DocumentError, BadRequest) as error:
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


def _file_response(field_file, filename: str, inline: bool = False) -> FileResponse:
    """An attachment (inline=True only for an Issued certificate's final PDF in the preview); 404 when the file is
    missing."""
    if not field_file:
        raise Http404("The file is not available yet.")
    try:
        handle = field_file.open("rb")
    except (FileNotFoundError, ValueError) as error:
        raise Http404("The file is missing.") from error
    response = FileResponse(handle, as_attachment=not inline, filename=filename)
    response["X-Content-Type-Options"] = "nosniff"
    return response


def _no_store(response: HttpResponse) -> HttpResponse:
    """Certificate PDFs change when a signature or the design changes: never kept by the browser (the page's links
    also carry ?v=, services.preview_version; ASSUMPTIONS 362)."""
    response["Cache-Control"] = "private, no-store"
    return response


def _pdf_response(content: bytes, filename: str, inline: bool = False) -> HttpResponse:
    """inline=True only for the watermarked preview (phase F4: it opens in a new tab). We render it ourselves; it is
    never an uploaded file. Final PDFs and uploads are always attachments."""
    response = HttpResponse(content, content_type="application/pdf")
    response["Content-Disposition"] = f'{"inline" if inline else "attachment"}; filename="{filename}"'
    response["X-Content-Type-Options"] = "nosniff"
    return response


# --- JSON shapes -------------------------------------------------------------------------------------


def certificate_json(certificate: Certificate, detail: bool = False) -> dict:
    is_participant = certificate.recipient_type == Certificate.RecipientType.REGISTRATION
    evaluation = services.get_submitted_evaluation(certificate) if is_participant else None
    data = {
        "certificate_id": certificate.pk,
        "certificate_number": certificate.certificate_number,
        "activity_id": certificate.activity_id,
        "activity_title": certificate.activity.title,
        "recipient_type": certificate.recipient_type,
        "recipient": certificate.registration.name if is_participant else certificate.resource_person.name,
        "status": certificate.status,
        "status_label": certificate.get_status_display(),
        "delivery_method": certificate.delivery_method,
        "evaluation_submitted": evaluation is not None,
        "pdf_ready": bool(certificate.file_path),
        "approved_at": certificate.approved_at,
        "issued_at": certificate.issued_at,
        "sent_at": certificate.sent_at,
        "send_failed_at": certificate.send_failed_at,
    }
    if detail:
        data.update(
            {
                "registration_id": certificate.registration_id,
                "resource_person_id": certificate.resource_person_id,
                # The name printed and the address used come from the evaluation (participants).
                "certificate_name": services.recipient_name(certificate),
                "certificate_email": evaluation.certificate_email if evaluation else None,
                "template": {"certificate_template_id": certificate.certificate_template_id, "name": certificate.certificate_template.name},
                "left_signatory": {"signatory_id": certificate.left_signatory_id, "name": certificate.left_signatory.name,
                                   "position": certificate.left_signatory.position},
                "right_signatory": {"signatory_id": certificate.right_signatory_id, "name": certificate.right_signatory.name,
                                    "position": certificate.right_signatory.position},
                "reissued_at": certificate.reissued_at,
                "approved_by": {"member_id": certificate.approved_by_id, "name": certificate.approved_by.name} if certificate.approved_by else None,
                "download_url": reverse("certificates:download", args=[certificate.pk]),
                "preview_url": reverse("certificates:preview", args=[certificate.pk]),
                "evidence": [evidence_json(d) for d in services.evidence_for(certificate)],
            }
        )
    return data


def evidence_json(document: Document) -> dict:
    data = {key: value for key, value in document_json(document).items() if key not in ("searchable",)}
    data["download_url"] = reverse("certificates:evidence_download", args=[document.pk])
    return data


def template_json(template: CertificateTemplate) -> dict:
    return {
        "certificate_template_id": template.pk,
        "name": template.name,
        "is_active": template.is_active,
        "uploaded_by": template.uploaded_by.name,
        "created_at": template.created_at,
        "image_url": reverse("certificates:template_image", args=[template.pk]),
    }


def signatory_json(signatory: CertificateSignatory) -> dict:
    return {
        "signatory_id": signatory.pk,
        "name": signatory.name,
        "position": signatory.position,
        "default_slot": signatory.default_slot,
        "is_active": signatory.is_active,
        "created_at": signatory.created_at,
        "signature_url": reverse("certificates:signature_image", args=[signatory.pk]) if signatory.signature_image_path else None,
    }


# --- Staff: reading ---------------------------------------------------------------------------------


@staff_login_required
@require_GET
@api_errors
def list_api(request: HttpRequest) -> JsonResponse:
    """GET ?activity=<id>&status=<generated|approved|issued>&recipient_type=&page="""
    activity = request.GET.get("activity") or None
    if activity is not None and not activity.isdigit():
        raise BadRequest("activity must be a number.")
    result = services.list_certificates(
        {"activity": activity, "status": request.GET.get("status"), "recipient_type": request.GET.get("recipient_type")},
        request.GET.get("page"),
    )
    return JsonResponse(
        {
            "certificates": [certificate_json(c) for c in result.items],
            "total": result.total,
            "page": result.page,
            "num_pages": result.num_pages,
        }
    )


@staff_login_required
@require_GET
def detail_api(request: HttpRequest, certificate_id: int) -> JsonResponse:
    return JsonResponse({"certificate": certificate_json(get_object_or_404(Certificate, pk=certificate_id), detail=True)})


@staff_login_required
@require_GET
def download_certificate(request: HttpRequest, certificate_id: int) -> HttpResponse:
    """"Download PDF" (any active staff member, always an attachment): the final PDF once it is rendered, otherwise
    the preview rendered now (<number>-preview.pdf, marked PREVIEW while waiting for approval; ASSUMPTIONS 358)."""
    certificate = get_object_or_404(Certificate, pk=certificate_id)
    if certificate.file_path:
        return _no_store(_file_response(certificate.file_path, f"{certificate.certificate_number}.pdf"))
    try:
        content = services.preview_pdf(request.user, certificate)
    except Exception as error:  # e.g. WeasyPrint's system libraries are missing
        return HttpResponse(f"The PDF could not be rendered ({type(error).__name__}). Run python manage.py check_pdf.",
                            status=503, content_type="text/plain; charset=utf-8")
    return _no_store(_pdf_response(content, f"{certificate.certificate_number}-preview.pdf"))


@staff_login_required
@require_GET
@xframe_options_sameorigin  # the preview dialog's <iframe> (like documents:view); every other page keeps DENY
def preview_certificate(request: HttpRequest, certificate_id: int) -> HttpResponse:
    """The certificate exactly as it will look (services.preview_pdf: same template, both signatures), rendered now
    and never stored. An Issued certificate shows its stored final PDF instead: the copy that was sent or handed
    over, never changed (services.shows_final_copy). Inline: shown in the tab's preview <iframe> or a new tab."""
    certificate = get_object_or_404(Certificate, pk=certificate_id)
    if services.shows_final_copy(certificate):
        return _no_store(_file_response(certificate.file_path, f"{certificate.certificate_number}.pdf", inline=True))
    try:
        content = services.preview_pdf(request.user, certificate)
    except Exception as error:  # e.g. WeasyPrint's system libraries are missing
        return HttpResponse(f"The preview could not be rendered ({type(error).__name__}). Run python manage.py check_pdf.",
                            status=503, content_type="text/plain; charset=utf-8")
    return _no_store(_pdf_response(content, f"PREVIEW-{certificate.certificate_number}.pdf", inline=True))


# --- Staff: actions (regenerate etc.: can_create_activity; approve: can_approve_certificates) ---------
# Certificates are created by the System when the activity is Conducted (ASSUMPTIONS 362): no generate endpoint.


def _target(data: dict) -> dict:
    """{"certificate_ids": [..]} or {"activity_id": n} -> keyword arguments for the services."""
    ids, activity_id = data.get("certificate_ids"), data.get("activity_id")
    if (ids is None) == (activity_id is None):
        raise BadRequest("Send either certificate_ids (a list) or activity_id.")
    if ids is not None:
        if not isinstance(ids, list) or not ids or not all(isinstance(i, int) for i in ids):
            raise BadRequest("certificate_ids must be a non-empty list of numbers.")
        return {"certificate_ids": ids}
    if not isinstance(activity_id, int):
        raise BadRequest("activity_id must be a number.")
    return {"activity": get_object_or_404(Activity, pk=activity_id)}


def _approve(member, *, activity=None, certificate_ids=None) -> int:
    """Approval is per activity (ASSUMPTIONS 369): {"activity_id": n} only. Returns how many were approved now."""
    if activity is None:
        raise BadRequest("Certificates are approved per activity: send activity_id.")
    return len(services.approve_activity_certificates(member, activity))


# action -> (service, permission). The view checks the permission first (403); the service checks it again.
ACTIONS = {
    "approve": (_approve, Permission.APPROVE_CERTIFICATES),
    "regenerate": (services.regenerate_certificates, Permission.CREATE_ACTIVITY),
    "hand-over": (services.mark_handed_over, Permission.CREATE_ACTIVITY),
    "resend": (services.resend_certificates, Permission.CREATE_ACTIVITY),
    "reissue": (services.reissue_certificates, Permission.APPROVE_CERTIFICATES),
}


@staff_login_required
@require_POST
@api_errors
def action_api(request: HttpRequest, action: str) -> JsonResponse:
    """POST /certificates/api/<approve|regenerate|hand-over|resend|reissue>/ with {"certificate_ids": [..]} or
    {"activity_id": n}. Each service checks its own permission (403 without it)."""
    if action not in ACTIONS:
        raise Http404("Unknown action.")
    run, permission = ACTIONS[action]
    require_permission(request.user, permission)
    target = _target(_json_body(request))
    return JsonResponse({"changed": run(request.user, **target)})


# --- Evidence documents (Step 6 rules) ---------------------------------------------------------------


def _evidence(document_id: int) -> Document:
    return get_object_or_404(Document.objects.certificate_documents(), pk=document_id)


@staff_login_required
@require_http_methods(["GET", "POST"])
@api_errors
def evidence_api(request: HttpRequest, certificate_id: int) -> JsonResponse:
    """GET: the certificate's evidence. POST (multipart: file, title): upload a scan (any active staff)."""
    certificate = get_object_or_404(Certificate, pk=certificate_id)
    if request.method == "GET":
        return JsonResponse({"evidence": [evidence_json(d) for d in services.evidence_for(certificate)]})
    document = services.upload_evidence(request.user, certificate, request.FILES.get("file"), request.POST.get("title", ""))
    return JsonResponse({"document": evidence_json(document)}, status=201)


@staff_login_required
@require_POST
@api_errors
def evidence_action_api(request: HttpRequest, document_id: int, action: str) -> JsonResponse:
    """POST approve | return {"reason"} (JSON) | replace (multipart: file) | delete.
    The Step 6 rules apply: reviewers need can_approve_documents (ASSUMPTIONS 344; never return their own upload);
    only the uploader replaces or deletes, and only while For Review."""
    document = _evidence(document_id)
    if action == "approve":
        document = document_services.approve_document(request.user, document)
    elif action == "return":
        document = document_services.return_document(request.user, document, str(_json_body(request).get("reason", "")))
    elif action == "replace":
        document = document_services.replace_document_file(request.user, document, request.FILES.get("file"))
    elif action == "delete":
        document_services.delete_document(request.user, document)
        return JsonResponse({"deleted": True})
    else:
        raise Http404("Unknown action.")
    return JsonResponse({"document": evidence_json(document)})


@staff_login_required
@require_GET
def evidence_download(request: HttpRequest, document_id: int) -> FileResponse:
    document = _evidence(document_id)
    return _file_response(document.file_path, document.file_name)


# --- Admin Console: templates (can_manage_users); signatories (can_approve_certificates) --------


@permission_required(Permission.MANAGE_USERS)
@require_http_methods(["GET", "POST"])
@api_errors
def templates_api(request: HttpRequest) -> JsonResponse:
    """GET: every template. POST (multipart: name, file, activate=true|false): upload one."""
    if request.method == "GET":
        templates = CertificateTemplate.objects.select_related("uploaded_by").order_by("-created_at")
        return JsonResponse({"templates": [template_json(t) for t in templates]})
    activate = parse_bool(request.POST.get("activate"), "activate", default=False)
    template = services.upload_template(request.user, request.POST.get("name", ""), request.FILES.get("file"), activate)
    return JsonResponse({"template": template_json(template)}, status=201)


@permission_required(Permission.MANAGE_USERS)
@require_POST
@api_errors
def template_action_api(request: HttpRequest, template_id: int, action: str) -> JsonResponse:
    template = get_object_or_404(CertificateTemplate, pk=template_id)
    if action == "activate":
        template = services.activate_template(request.user, template)
    elif action == "deactivate":
        template = services.deactivate_template(request.user, template)
    else:
        raise Http404("Unknown action.")
    return JsonResponse({"template": template_json(template)})


@permission_required(Permission.APPROVE_CERTIFICATES)
@require_http_methods(["GET", "POST"])
@api_errors
def signatories_api(request: HttpRequest) -> JsonResponse:
    """GET: every signatory (active first). POST (multipart: name, position (free text), file optional): add a
    person to the list (ASSUMPTIONS 363)."""
    if request.method == "GET":
        people = CertificateSignatory.objects.order_by("-is_active", "name", "-created_at")
        return JsonResponse({"signatories": [signatory_json(s) for s in people]})
    signatory = services.add_signatory(request.user, request.POST.get("name", ""), request.POST.get("position", ""), request.FILES.get("file"))
    return JsonResponse({"signatory": signatory_json(signatory)}, status=201)


@permission_required(Permission.APPROVE_CERTIFICATES)
@require_POST
@api_errors
def signatory_deactivate_api(request: HttpRequest, signatory_id: int) -> JsonResponse:
    signatory = services.deactivate_signatory(request.user, get_object_or_404(CertificateSignatory, pk=signatory_id))
    return JsonResponse({"signatory": signatory_json(signatory)})


@permission_required(Permission.MANAGE_USERS)
@require_GET
def template_image(request: HttpRequest, template_id: int) -> FileResponse:
    template = get_object_or_404(CertificateTemplate, pk=template_id)
    return _file_response(template.file_path, f"template-{template.pk}{_suffix(template.file_path.name)}")


@staff_login_required
@require_GET
def signature_image(request: HttpRequest, signatory_id: int) -> FileResponse:
    """Signature images are never public: members who manage users (Admin Console) or approve certificates (the
    Signatures section of the Certificates tab, ASSUMPTIONS 361). 404 while no image was added."""
    if not (has_permission(request.user, Permission.MANAGE_USERS)
            or has_permission(request.user, Permission.APPROVE_CERTIFICATES)):
        require_permission(request.user, Permission.APPROVE_CERTIFICATES)  # 403 with the usual message
    signatory = get_object_or_404(CertificateSignatory, pk=signatory_id)
    if not signatory.signature_image_path:
        raise Http404("No signature image was added yet.")
    name = signatory.signature_image_path.name
    return _file_response(signatory.signature_image_path, f"signature-{signatory.pk}{_suffix(name)}")


def _suffix(name: str) -> str:
    return "." + name.rsplit(".", 1)[-1] if "." in name else ""
