"""The Certificates pages (phase F4; ASSUMPTIONS 362): the Certificates page (/certificates/), the actions of the
activity's Certificates tab (Approve on the preview, "Approve all ready certificates", Apply the current design, Mark
handed over, Resend, signatures), the preview page and the evidence modal. Thin views: every rule is in
certificates.services (and documents.services for evidence); certificates/pages.py prepares what the templates show.

Viewing, previews, downloads and evidence uploads: any active staff member. Apply the current design, hand over and
resend: can_create_activity. Approve and add signatures: can_approve_certificates (the Director, both Project Heads and
the System Administrator). Certificates are created by the System (no Generate).

Every action ends on the activity's Certificates tab (/activities/<id>/?tab=certificates, a redirect after the POST);
a GET on an action address goes there too (config.links.post_only_or_tab). Refusals are kept for that one page view
(pages.keep_problems).
"""

from django.contrib import messages
from django.core.exceptions import ValidationError
from django.http import HttpRequest, HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_GET, require_POST

from accounts.permissions import Permission, is_htmx, permission_required, require_permission, staff_login_required
from activities.models import Activity
from config.links import post_only_or_tab
from config.mail import email_sent_message
from documents import pages as document_pages

from . import pages, services
from .models import Certificate

SIGNATURE_NAMES = {"vp": "VP", "director": "Director"}

# The row actions (value posted -> service call, permission, what it did). The service checks every certificate.
ROW_ACTIONS = {
    "hand_over": (lambda member, ids: services.mark_handed_over(member, certificate_ids=ids), Permission.CREATE_ACTIVITY,
                  "Marked {n} as handed over: now Issued."),
    "resend": (lambda member, ids: services.resend_certificates(member, certificate_ids=ids), Permission.CREATE_ACTIVITY,
               "RESEND"),
}


def _activity(activity_id: int) -> Activity:
    return get_object_or_404(Activity.objects.select_related("status", "activity_type", "created_by"), pk=activity_id)


def _certificates(n: int) -> str:
    return f"{n} certificate{'' if n == 1 else 's'}"


def approved_message(certificates: list[Certificate]) -> str:
    """"Approved 3 certificates. 2 are emailed once the PDF is ready; 1 is ready for printing (...)." Development
    mode adds the "printed in the terminal" note (ASSUMPTIONS 342)."""
    by_email = sum(1 for c in certificates if c.delivery_method == Certificate.DeliveryMethod.EMAIL)
    by_hand = len(certificates) - by_email
    parts = []
    if by_email:
        parts.append(f"{by_email} {'is' if by_email == 1 else 'are'} emailed once the PDF is ready")
    if by_hand:
        parts.append(f"{by_hand} {'is' if by_hand == 1 else 'are'} ready for printing (Mark handed over afterwards)")
    text = f"Approved {_certificates(len(certificates))}. {'; '.join(parts)}."
    return email_sent_message("", before=text + " ").strip() if by_email else text


# --- The Certificates page --------------------------------------------------------------------------------------


@staff_login_required
@require_GET
def certificate_list(request: HttpRequest) -> HttpResponse:
    """GET activity, status, recipient_type, delivery_method, page. An HTMX request gets only the results."""
    filters = pages.parse_filters(request.GET)
    context = {"page_title": "Certificates", "filters": filters, **pages.certificate_list(filters)}
    if is_htmx(request):
        return render(request, "certificates/_results.html", context)
    context.update(pages.filter_choices())
    return render(request, "certificates/list.html", context)


# --- Approval (can_approve_certificates) --------------------------------------------------------------------------


@permission_required(Permission.APPROVE_CERTIFICATES)
@post_only_or_tab("certificates")
def approve_one(request: HttpRequest, activity_id: int, certificate_id: int) -> HttpResponse:
    """The preview card's Approve: this certificate only (refused unless it is ready). Afterwards the preview shows
    the next certificate waiting for approval."""
    activity = _activity(activity_id)
    certificate = get_object_or_404(Certificate, pk=certificate_id, activity=activity)
    try:
        services.approve_certificate(request.user, certificate)
    except services.CertificateError as error:
        reason = error.problems[0] if error.problems else str(error)
        messages.error(request, f"Not approved: {reason}")
        return redirect(pages.tab_url(activity.pk, preview=certificate.pk))
    messages.success(request, approved_message([certificate]))
    return redirect(pages.tab_url(activity.pk, preview=pages.next_to_approve(activity, certificate)))


@permission_required(Permission.APPROVE_CERTIFICATES)
@post_only_or_tab("certificates")
def approve_ready(request: HttpRequest, activity_id: int) -> HttpResponse:
    """"Approve all ready certificates" (after the confirm dialog): every ready certificate of the activity; the
    others keep waiting. A refusal (none ready, a missing signature) is one message."""
    activity = _activity(activity_id)
    ready = services.ready_certificates(activity)
    try:
        services.approve_certificates(request.user, activity=activity)
    except services.CertificateError as error:
        messages.error(request, str(error))
    else:
        messages.success(request, approved_message(ready))
    return redirect(pages.tab_url(activity.pk))


# --- Apply the current design, hand over, resend (can_create_activity) --------------------------------------------


@permission_required(Permission.CREATE_ACTIVITY)
@post_only_or_tab("certificates")
def apply_design(request: HttpRequest, activity_id: int) -> HttpResponse:
    """"Apply the current design": the active template and signatories on every certificate of the activity that is
    not Issued; Approved ones wait for approval again (services.regenerate_certificates)."""
    activity = _activity(activity_id)
    try:
        changed = services.regenerate_certificates(request.user, activity=activity)
    except services.CertificateError as error:
        messages.error(request, str(error))
    else:
        messages.success(request, f"Applied the current design to {_certificates(changed)}. Approved ones wait for "
                                  "approval again; issued certificates are not changed.")
    return redirect(pages.tab_url(activity.pk))


@staff_login_required
@post_only_or_tab("certificates")
def row_action(request: HttpRequest, activity_id: int) -> HttpResponse:
    """POST action (hand_over | resend) and certificate_ids (of this activity): a row's "Mark handed over" or
    "Resend". 403 without the action's permission (checked again by the service)."""
    activity = _activity(activity_id)
    action = request.POST.get("action", "")
    if action not in ROW_ACTIONS:
        messages.error(request, "Choose an action.")
        return redirect(pages.tab_url(activity.pk))
    require_permission(request.user, ROW_ACTIONS[action][1])
    raw_ids = {value for value in request.POST.getlist("certificate_ids") if value.isdigit()}
    ids = list(activity.certificates.filter(pk__in=raw_ids).values_list("pk", flat=True))
    if not raw_ids or len(ids) != len(raw_ids):
        messages.error(request, "Choose a certificate of this activity first.")
        return redirect(pages.tab_url(activity.pk))
    run, _permission, done = ROW_ACTIONS[action]
    try:
        changed = run(request.user, ids)
    except services.CertificateError as error:
        messages.error(request, "Nothing was changed. See the list on the page.")
        pages.keep_problems(request, activity, error.problems or [str(error)])
        return redirect(pages.tab_url(activity.pk))
    if done == "RESEND":
        # Development mode adds "Email printed in the terminal ..." (ASSUMPTIONS 342).
        messages.success(request, email_sent_message("", before=f"Queued {_certificates(changed)} to be emailed again. ").strip())
    else:
        messages.success(request, done.format(n=_certificates(changed)))
    return redirect(pages.tab_url(activity.pk))


# --- Signatures (can_approve_certificates, ASSUMPTIONS 361) -------------------------------------------------------


@permission_required(Permission.APPROVE_CERTIFICATES)
@post_only_or_tab("certificates")
def signature_upload(request: HttpRequest, activity_id: int, position: str) -> HttpResponse:
    """POST file: add or replace the VP's or Director's signature (services.upload_signature checks everything). The
    preview is drawn again on its next load (no background worker needed); approved certificates wait for approval
    again; issued ones keep the copy that was sent."""
    activity = _activity(activity_id)
    try:
        result = services.upload_signature(request.user, activity, position, request.FILES.get("file"))
    except ValidationError as error:
        messages.error(request, " ".join(error.messages))
    except services.CertificateError as error:
        messages.error(request, str(error))
    else:
        text = f"{result['action'].capitalize()} the {SIGNATURE_NAMES[position]}'s signature."
        if result["updated"]:
            text += (f" {_certificates(result['updated'])} of this activity now use it (the preview shows it); approved "
                     "ones wait for approval again. Issued certificates keep the copy that was sent.")
        messages.success(request, text)
    return redirect(pages.tab_url(activity.pk))


# --- Preview (any active staff member) ---------------------------------------------------------------------------


@staff_login_required
@require_GET
def preview_dialog(request: HttpRequest, certificate_id: int) -> HttpResponse:
    """The preview dialog's content for HTMX (the PDF in an <iframe> + Download PDF); its own page without JavaScript."""
    certificate = get_object_or_404(Certificate.objects.select_related("activity", "registration", "resource_person"), pk=certificate_id)
    context = {"page_title": f"Certificate {certificate.certificate_number}", **pages.preview(certificate, request.user)}
    if is_htmx(request):
        return render(request, "certificates/_preview.html", context)
    return render(request, "certificates/preview_page.html", context)


# --- Evidence (any active staff member; the Step 6 rules) -------------------------------------------------------


@staff_login_required
@require_GET
def evidence(request: HttpRequest, certificate_id: int) -> HttpResponse:
    """The evidence list and upload form: the modal's content for HTMX, a page of its own without JavaScript."""
    certificate = get_object_or_404(Certificate.objects.select_related("activity", "registration", "resource_person"), pk=certificate_id)
    context = {"page_title": f"Evidence for {certificate.certificate_number}", **pages.evidence(request, certificate)}
    template = "certificates/_evidence.html" if is_htmx(request) else "certificates/evidence_page.html"
    return render(request, template, context)


@staff_login_required
@require_POST
def evidence_upload(request: HttpRequest, certificate_id: int) -> HttpResponse:
    """POST file, title (optional). Errors come back under the field (in the modal for HTMX)."""
    certificate = get_object_or_404(Certificate.objects.select_related("activity", "registration", "resource_person"), pk=certificate_id)
    title = request.POST.get("title", "")
    try:
        document = services.upload_evidence(request.user, certificate, request.FILES.get("file"), title)
    except (ValidationError, services.CertificateError) as error:
        if isinstance(error, ValidationError):
            errors = {key: " ".join(values) for key, values in error.message_dict.items()}
        else:
            errors = {"file": str(error)}
        context = {"page_title": f"Evidence for {certificate.certificate_number}", **pages.evidence(request, certificate, errors, title)}
        if is_htmx(request):
            return render(request, "certificates/_evidence.html", context)
        return render(request, "certificates/evidence_page.html", context, status=400)
    messages.success(request, f'"{document.title}" was uploaded as evidence for {certificate.certificate_number}. It is waiting for review.')
    target = document_pages.safe_next(request, pages.tab_url(certificate.activity_id))
    if is_htmx(request):
        response = HttpResponse()
        response["HX-Redirect"] = target
        return response
    return redirect(target)
