"""The Certificates pages (phase F4): the Certificates page (/certificates/), Generate, "Approve all for this activity"
and the bulk actions of the activity's Certificates tab, the preview dialog and the evidence modal. Thin views: every
rule is in certificates.services (and documents.services for evidence); certificates/pages.py prepares what the
templates show.

Viewing, previews, downloads and evidence uploads: any active staff member. Generate, regenerate, hand over and resend:
can_create_activity. Approve and add signatures: can_approve_certificates (the Director, both Project Heads and the
System Administrator, ASSUMPTIONS 361). A bulk action changes all the selected certificates or none; a refusal shows
every refused certificate with its reason.
"""

from django.contrib import messages
from django.core.exceptions import ValidationError
from django.http import HttpRequest, HttpResponse
from config.mail import email_sent_message
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_GET, require_POST

from accounts.permissions import Permission, is_htmx, permission_required, require_permission, staff_login_required
from activities.models import Activity
from activities.page_views import render_activity_page
from documents import pages as document_pages

from . import pages, services
from .models import Certificate

APPROVED_DONE = "Approved {n}. Each participant's certificate is emailed once their evaluation is in; ready PDFs are rendered in the background."
SIGNATURE_NAMES = {"vp": "VP", "director": "Director"}

# value posted -> (service call, permission, what it did). The services check every certificate: all or nothing.
BULK_ACTIONS = {
    "approve": (lambda member, ids: services.approve_certificates(member, certificate_ids=ids), Permission.APPROVE_CERTIFICATES,
                APPROVED_DONE),
    "regenerate": (lambda member, ids: services.regenerate_certificates(member, certificate_ids=ids), Permission.CREATE_ACTIVITY,
                   "Regenerated {n} with the current design: waiting for approval again."),
    "hand_over": (lambda member, ids: services.mark_handed_over(member, certificate_ids=ids), Permission.CREATE_ACTIVITY,
                  "Marked {n} as handed over: now Issued."),
    "resend": (lambda member, ids: services.resend_certificates(member, certificate_ids=ids), Permission.CREATE_ACTIVITY,
               "RESEND"),
}


def _activity(activity_id: int) -> Activity:
    return get_object_or_404(Activity.objects.select_related("status", "activity_type", "created_by"), pk=activity_id)


def _certificates(n: int) -> str:
    return f"{n} certificate{'' if n == 1 else 's'}"


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


# --- Generate and the bulk actions (can_create_activity) --------------------------------------------------------


@permission_required(Permission.CREATE_ACTIVITY)
@require_POST
def generate(request: HttpRequest, activity_id: int) -> HttpResponse:
    activity = _activity(activity_id)
    try:
        result = services.generate_certificates(request.user, activity)
    except services.CertificateError as error:
        messages.error(request, str(error))
    else:
        created = result["participation_created"] + result["appreciation_created"]
        if created:
            messages.success(request, f"Created {result['participation_created']} Certificate(s) of Participation and "
                                      f"{result['appreciation_created']} of Appreciation. The activity now has {result['total']}.")
        else:
            messages.info(request, f"Nothing new to create: the activity already has all {result['total']} certificates.")
    return redirect(pages.tab_url(activity.pk))


@permission_required(Permission.APPROVE_CERTIFICATES)
@require_POST
def approve_all(request: HttpRequest, activity_id: int) -> HttpResponse:
    """"Approve all for this activity" (after the confirm dialog): every certificate waiting for approval, all or
    nothing. A refusal (e.g. a missing signature image) is shown as one message."""
    activity = _activity(activity_id)
    try:
        changed = services.approve_certificates(request.user, activity=activity)
    except services.CertificateError as error:
        messages.error(request, str(error))
    else:
        messages.success(request, APPROVED_DONE.format(n=_certificates(changed)))
    return redirect(pages.tab_url(activity.pk))


@staff_login_required
@require_POST
def bulk_action(request: HttpRequest, activity_id: int) -> HttpResponse:
    """POST action (BULK_ACTIONS) and certificate_ids (one or more, of this activity). 403 without the action's
    permission (checked again by the service)."""
    activity = _activity(activity_id)
    action = request.POST.get("action", "")
    if action in BULK_ACTIONS:
        require_permission(request.user, BULK_ACTIONS[action][1])
    raw_ids = {value for value in request.POST.getlist("certificate_ids") if value.isdigit()}
    ids = list(activity.certificates.filter(pk__in=raw_ids).values_list("pk", flat=True))
    if action not in BULK_ACTIONS:
        messages.error(request, "Choose an action.")
        return redirect(pages.tab_url(activity.pk))
    if not raw_ids or len(ids) != len(raw_ids):
        messages.error(request, "Select at least one certificate of this activity first.")
        return redirect(pages.tab_url(activity.pk))
    run, _permission, done = BULK_ACTIONS[action]
    try:
        changed = run(request.user, ids)
    except services.CertificateError as error:
        problems = error.problems or [str(error)]
        messages.error(request, f"Nothing was changed: {len(problems)} of the {_certificates(len(ids))} selected "
                                f"{'was' if len(problems) == 1 else 'were'} refused. See the list on the page.")
        return render_activity_page(request, activity, "certificates", {"problems": problems}, status=400)
    if done == "RESEND":
        # Development mode adds "Email printed in the terminal ..." (ASSUMPTIONS 342).
        messages.success(request, email_sent_message("", before=f"Queued {_certificates(changed)} to be emailed again. ").strip())
    else:
        messages.success(request, done.format(n=_certificates(changed)))
    return redirect(pages.tab_url(activity.pk))


# --- Signatures (can_approve_certificates, ASSUMPTIONS 361) -------------------------------------------------------


@permission_required(Permission.APPROVE_CERTIFICATES)
@require_POST
def signature_upload(request: HttpRequest, activity_id: int, position: str) -> HttpResponse:
    """POST file: add or replace the VP's or Director's signature (services.upload_signature checks everything)."""
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
            text += (f" {_certificates(result['updated'])} of this activity now use it; approved ones wait for "
                     "approval again.")
        messages.success(request, text)
    return redirect(pages.tab_url(activity.pk))


# --- Preview (any active staff member) ---------------------------------------------------------------------------


@staff_login_required
@require_GET
def preview_dialog(request: HttpRequest, certificate_id: int) -> HttpResponse:
    """The preview dialog's content for HTMX (the PDF in an <iframe> + Download PDF); its own page without JavaScript."""
    certificate = get_object_or_404(Certificate.objects.select_related("activity", "registration", "resource_person"), pk=certificate_id)
    context = {"page_title": f"Certificate {certificate.certificate_number}", **pages.preview(certificate)}
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
