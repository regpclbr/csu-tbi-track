"""The Certificates pages (phase F4; ASSUMPTIONS 362): the Certificates page (/certificates/), the actions of the
activity's Certificates tab ("Approve certificates for this activity", Apply the current design, Mark handed over,
Resend, signatures, the certificate text), the Certificate text page, the preview page and the evidence modal. Thin views: every rule is in
certificates.services (and documents.services for evidence); certificates/pages.py prepares what the templates show.

Viewing, previews, downloads and evidence uploads: any active staff member. Apply the current design, hand over and
resend: can_create_activity. Retry a failed PDF: can_create_activity or can_approve_certificates (ASSUMPTIONS 421).
Approve, reissue and the default text: can_approve_certificates (the Director only by default, ASSUMPTIONS 421; was also
both Project Heads, Admin Staff
and the System Administrator). Signature images, "Add a person" and the Signatories page: can_manage_signatories (the
Director only, ASSUMPTIONS 418). An activity's two signatories: can_create_activity or can_approve_certificates
(ASSUMPTIONS 363, 364). Certificates are created by the System (no Generate).

Every action ends on the activity's Certificates tab (/activities/<id>/?tab=certificates, a redirect after the POST);
a GET on an action address goes there too (config.links.post_only_or_tab). Refusals are kept for that one page view
(pages.keep_problems).
"""

import json

from django.conf import settings
from django.contrib import messages
from django.core.exceptions import PermissionDenied, ValidationError
from django.http import HttpRequest, HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_GET, require_POST

from django.urls import reverse

from accounts.permissions import (
    Permission,
    object_permission_required,
    has_permission,
    is_htmx,
    permission_required,
    require_any_permission,
    require_permission,
    staff_login_required,
)
from activities.models import Activity
from config.links import post_only_or_tab
from config.mail import email_sent_message
from config.parsing import parse_bool
from documents import pages as document_pages

from . import pages, services
from .models import Certificate, CertificateSignatory

# The row actions (value posted -> service call, permission, what it did). The service checks every certificate.
ROW_ACTIONS = {
    "hand_over": (lambda member, ids: services.mark_handed_over(member, certificate_ids=ids), Permission.CREATE_ACTIVITY,
                  "Marked {n} as handed over: now Issued."),
    "resend": (lambda member, ids: services.resend_certificates(member, certificate_ids=ids), Permission.CREATE_ACTIVITY,
               "RESEND"),
    # "PDF failed — Retry" (ASSUMPTIONS 368)
    "retry_pdf": (lambda member, ids: services.retry_certificate_pdfs(member, certificate_ids=ids),
                  services.RETRY_PERMISSIONS, "Queued {n} to be rendered again."),  # ASSUMPTIONS 421
}


# The evidence page's one-line description (ASSUMPTIONS 411).
EVIDENCE_SUBTITLE = "Files kept as evidence for this certificate"

def _keep_background(request: HttpRequest) -> bool:
    """The upload dialogs' "Keep the background" checkbox (ASSUMPTIONS 366)."""
    try:
        return bool(parse_bool(request.POST.get("keep_background"), "keep_background", default=False))
    except ValidationError:
        return False


def activity_from_url(activity_id: int, **kwargs) -> Activity:
    """The activity in the URL (404 if missing), for object_permission_required (ASSUMPTIONS 437)."""
    return _activity(activity_id)


def _activity(activity_id: int) -> Activity:
    return get_object_or_404(Activity.objects.select_related("status", "activity_type", "created_by"), pk=activity_id)


def _certificates(n: int) -> str:
    return f"{n} certificate{'' if n == 1 else 's'}"


def approved_message(certificates: list[Certificate]) -> str:
    """After "Approve certificates for this activity" (ASSUMPTIONS 369): what happens now and later. Development mode
    adds the "printed in the terminal" note (ASSUMPTIONS 342)."""
    by_email = sum(1 for c in certificates if c.delivery_method == Certificate.DeliveryMethod.EMAIL)
    by_hand = len(certificates) - by_email
    parts = []
    if by_email:
        parts.append(f"{by_email} participant{'' if by_email == 1 else 's'} who already evaluated "
                     f"{'is' if by_email == 1 else 'are'} emailed once the PDF is ready")
    if by_hand:
        parts.append(f"{by_hand} resource person certificate{'' if by_hand == 1 else 's'} "
                     f"{'is' if by_hand == 1 else 'are'} ready to print (Mark handed over afterwards)")
    text = "Approved the certificates of this activity. "
    if parts:
        text += "; ".join(parts).capitalize() + ". "
    text += "Everyone else's certificate is emailed automatically as soon as they submit their evaluation."
    return email_sent_message("", before=text + " ").strip()


# --- The Certificates page --------------------------------------------------------------------------------------


@staff_login_required
@require_GET
def certificate_list(request: HttpRequest) -> HttpResponse:
    """GET q, activity, status, recipient_type, delivery_method, view (activity | all), page (ASSUMPTIONS 372). View only
    for any active staff member. An HTMX request gets only the results (the filters are inside them)."""
    filters = pages.parse_filters(request.GET)
    context = {"page_title": "Certificates", "page_subtitle": "All certificates, the most recent activity first; approve, preview and reissue them on each activity's Certificates tab",
               "filters": filters, **pages.certificate_list(filters), **pages.filter_choices()}
    if is_htmx(request):
        return render(request, "certificates/_results.html", context)
    if has_permission(request.user, Permission.MANAGE_SIGNATORIES):  # the Signatories page (ASSUMPTIONS 363, 418)
        context["signatories_link"] = reverse("certificates:signatories")
    return render(request, "certificates/list.html", context)


# --- Approval per activity (can_approve_certificates, ASSUMPTIONS 369) ------------------------------------------------


@object_permission_required(Permission.APPROVE_CERTIFICATES, activity_from_url)
@post_only_or_tab("certificates")
def approve_activity(request: HttpRequest, activity_id: int) -> HttpResponse:
    """"Approve certificates for this activity" (after the confirm dialog): services.approve_activity_certificates. A
    refusal (already approved, a missing signature, no certificates) is one message."""
    activity = _activity(activity_id)
    early = services.before_conducted(activity)  # ASSUMPTIONS 420
    try:
        approved = services.approve_activity_certificates(request.user, activity)
    except services.CertificateError as error:
        messages.error(request, f"Not approved: {error}")
    else:
        messages.success(request, EARLY_APPROVED_MESSAGE if early else approved_message(approved))
    return redirect(pages.tab_url(activity.pk))


@permission_required(Permission.APPROVE_CERTIFICATES)
@post_only_or_tab("certificates")
def unapprove_activity(request: HttpRequest, activity_id: int) -> HttpResponse:
    """POST reason: "Unapprove" (services.unapprove_activity_certificates, ASSUMPTIONS 437): only while no certificate of
    the activity is Issued. A refusal or a missing reason is one message."""
    activity = _activity(activity_id)
    try:
        back = services.unapprove_activity_certificates(request.user, activity, request.POST.get("reason", ""))
    except ValidationError as error:
        messages.error(request, "Not unapproved: " + " ".join(error.messages))
    except services.CertificateError as error:
        messages.error(request, f"Not unapproved: {error}")
    else:
        messages.success(request, "The approval of this activity's certificates was taken back"
                         + (f": {back} certificate{'' if back == 1 else 's'} went back to waiting for approval."
                            if back else "."))
    return redirect(pages.tab_url(activity.pk))


@permission_required(Permission.MANAGE_SIGNATORIES)
@require_POST
def signatory_account(request: HttpRequest, signatory_id: int) -> HttpResponse:
    """POST account (a member ID, empty = no link): services.link_signatory_account (ASSUMPTIONS 437)."""
    from accounts.models import TBIMember

    person = get_object_or_404(CertificateSignatory, pk=signatory_id)
    raw = request.POST.get("account", "")
    account = TBIMember.objects.filter(pk=raw, is_active=True).first() if raw.isdigit() else None
    if raw and account is None:
        messages.error(request, "Choose an active member.")
        return redirect(_back(request))
    try:
        services.link_signatory_account(request.user, person, account)
    except services.CertificateError as error:
        messages.error(request, str(error))
    else:
        messages.success(request, f"{person.name} now signs approved submissions as {account.name}." if account
                         else f"{person.name} is no longer linked to an account.")
    return redirect(_back(request))


EARLY_APPROVED_MESSAGE = ("Approved the certificates of this activity. They will be created already approved when the "
                          "activity is Conducted: each participant's certificate is emailed as soon as they submit their "
                          "evaluation, and resource persons' certificates are ready to print.")


@staff_login_required
@post_only_or_tab("certificates")
def prepare(request: HttpRequest, activity_id: int) -> HttpResponse:
    """"Prepare the certificates now" on the Certificates tab before Conducted (ASSUMPTIONS 420):
    services.prepare_activity_certificates (can_create_activity or can_approve_certificates; 403 without either).
    The signatories, titles and text are the ones on the tab. A refusal is one message."""
    require_any_permission(request.user, Permission.CREATE_ACTIVITY, Permission.APPROVE_CERTIFICATES)
    activity = _activity(activity_id)
    try:
        services.prepare_activity_certificates(request.user, activity)
    except services.CertificateError as error:
        messages.error(request, str(error))
    else:
        messages.success(request, PREPARED_MESSAGE)
    return redirect(pages.tab_url(activity.pk))


PREPARED_MESSAGE = ("The certificates are prepared and wait for approval (the Director gets a notification). Once "
                    "approved, they are created already approved when the activity is Conducted.")


# --- Certificate text of the activity (ASSUMPTIONS 370) -------------------------------------------------------------


def _posted_texts(request: HttpRequest) -> dict[str, dict]:
    """{type: {field: text}} from the posted "<type>-<field>" values (both types)."""
    return pages.posted_texts(request.POST)


def text_saved_message(result: dict, what: str) -> str:
    text = what
    if result.get("updated"):
        text += f" {_certificates(result['updated'])} not issued yet now use it (the preview shows it)."
    if result.get("cancelled"):
        text += " The approval of the activity's certificates was cancelled: check the preview and approve again."
    return text


@staff_login_required
@post_only_or_tab("certificates")
def text_save(request: HttpRequest, activity_id: int) -> HttpResponse:
    """POST mode (default | custom) and the fields "<type>-<field>": "Use the default text" (services.use_default_text)
    or "Custom text for this activity" (services.save_activity_text). can_create_activity or can_approve_certificates.
    Refused text: the page again with what was typed and the errors under the fields (status 400)."""
    from activities.page_views import render_activity_page

    require_any_permission(request.user, Permission.CREATE_ACTIVITY, Permission.APPROVE_CERTIFICATES)
    activity = _activity(activity_id)
    mode = request.POST.get("mode", "")
    if mode not in ("default", "custom"):
        messages.error(request, "Choose the default text or custom text for this activity.")
        return redirect(pages.tab_url(activity.pk))
    try:
        if mode == "default":
            result = services.use_default_text(request.user, activity)
        else:
            result = services.save_activity_text(request.user, activity, _posted_texts(request))
    except ValidationError as error:
        form = {"values": request.POST, "errors": error.message_dict, "mode": mode}
        return render_activity_page(request, activity, "certificates", {"text_form": form}, status=400)
    if not result["changed"]:
        messages.info(request, "Nothing was changed.")
    elif mode == "default":
        messages.success(request, text_saved_message(result, "This activity now uses the default certificate text."))
    else:
        messages.success(request, text_saved_message(result, "Saved the certificate text for this activity."))
    return redirect(pages.tab_url(activity.pk))


@staff_login_required
@require_POST
def text_preview(request: HttpRequest, activity_id: int) -> HttpResponse:
    """HTMX, while typing: the filled wording of one type (services.wording_preview) or the errors saving would give.
    Nothing is saved. Any active staff member may see it (it only shows the activity's own details)."""
    activity = _activity(activity_id)
    kind = request.POST.get("type", "")
    if kind not in services.TEXT_TYPES:
        return HttpResponse(status=400)
    from . import wording

    values = {field: request.POST.get(f"{kind}-{field}", "") for field in wording.FIELDS}
    return render(request, "certificates/_text_preview.html", {"preview": services.wording_preview(activity, kind, values)})


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
    """POST action (hand_over | resend | retry_pdf) and certificate_ids (of this activity): a row's "Mark handed
    over", "Resend" or "Retry". 403 without the action's permission (checked again by the service)."""
    activity = _activity(activity_id)
    action = request.POST.get("action", "")
    if action not in ROW_ACTIONS:
        messages.error(request, "Choose an action.")
        return redirect(pages.tab_url(activity.pk))
    needed = ROW_ACTIONS[action][1]
    require_any_permission(request.user, *(needed if isinstance(needed, tuple) else (needed,)))
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


# --- Signatories of the activity (ASSUMPTIONS 363) ----------------------------------------------------------------


def changed_text(result: dict, *, what: str) -> str:
    """The message after a signatory change: what changed, the certificates updated, and the outdated notice."""
    text = what
    if result.get("updated"):
        text += f" {_certificates(result['updated'])} not issued yet now use it (the preview shows it)."
    if result.get("cancelled"):  # ASSUMPTIONS 369
        many = result["cancelled"] > 1
        text += (f" The certificate approval of {result['cancelled']} activities was cancelled: they need approval again."
                 if many else " The certificate approval was cancelled: check the preview and approve again.")
    notice = services.outdated_notice(result.get("outdated", 0), result.get("outdated_activities", 1) or 1)
    return f"{text} {notice}".strip()


@staff_login_required
@post_only_or_tab("certificates")
def signatory_choose(request: HttpRequest, activity_id: int, slot: str) -> HttpResponse:
    """POST signatory, title: choose who signs on `slot` and the "Title on this certificate"
    (services.set_activity_signatory). can_approve_certificates only (ASSUMPTIONS 431: only the Director chooses who
    signs; 403 otherwise, the service checks again)."""
    activity = _activity(activity_id)
    services.require_signatory_choice(request.user, activity)  # or its "Choose who signs" assignment (437)
    raw = request.POST.get("signatory", "")
    person = CertificateSignatory.objects.filter(pk=raw).first() if raw.isdigit() else None
    if person is None:
        messages.error(request, "Choose a person from the list.")
        return redirect(pages.tab_url(activity.pk))
    try:
        result = services.set_activity_signatory(request.user, activity, slot, person, title=request.POST.get("title", ""))
    except ValidationError as error:
        messages.error(request, " ".join(error.messages))
    except services.CertificateError as error:
        messages.error(request, str(error))
    else:
        if result["changed"]:
            label = services.SLOT_LABELS.get(slot, slot).lower()
            row = services.activity_slots(activity)[slot]
            messages.success(request, changed_text(
                {**result, "outdated_activities": 1},
                what=f"{services.slot_text(person, services.printed_title(row))} now signs on the {label} for this activity."))
        else:
            messages.info(request, "Nothing was changed.")
    return redirect(pages.tab_url(activity.pk))


@staff_login_required
@post_only_or_tab("certificates")
def signatory_current_version(request: HttpRequest, activity_id: int, slot: str) -> HttpResponse:
    """"Use the current version" on a slot whose person is inactive (services.use_current_version, ASSUMPTIONS 367).
    can_approve_certificates only (ASSUMPTIONS 431: it changes who signs)."""
    activity = _activity(activity_id)
    services.require_signatory_choice(request.user, activity)
    try:
        result = services.use_current_version(request.user, activity, slot)
    except ValidationError as error:
        messages.error(request, " ".join(error.messages))
    except services.CertificateError as error:
        messages.error(request, str(error))
    else:
        person = services.activity_signatories(activity)[slot]
        label = services.SLOT_LABELS.get(slot, slot).lower()
        messages.success(request, changed_text({**result, "outdated_activities": 1},
                                               what=f"The {label} slot now uses the current version of {person.name}."))
    return redirect(pages.tab_url(activity.pk))


@permission_required(Permission.MANAGE_SIGNATORIES)
@post_only_or_tab("certificates")
def signature_upload(request: HttpRequest, activity_id: int, slot: str) -> HttpResponse:
    """POST file: add or replace the signature image of the person on `slot` (services.upload_signature). A replaced
    image is a new version of the person for every activity that uses them (the confirm dialog showed the impact);
    issued certificates keep the copy that was sent (they can be reissued)."""
    activity = _activity(activity_id)
    try:
        result = services.upload_signature(request.user, activity, slot, request.FILES.get("file"),
                                           keep_background=_keep_background(request))
    except ValidationError as error:
        messages.error(request, " ".join(error.messages))
    except services.CertificateError as error:
        messages.error(request, str(error))
    else:
        person = result["signatory"]
        messages.success(request, changed_text(result, what=f"{result['action'].capitalize()} the signature of "
                                                            f"{services.signatory_label(person)}."))
    return redirect(pages.tab_url(activity.pk))


# --- Reissue (can_approve_certificates, ASSUMPTIONS 364) -----------------------------------------------------------


def reissued_message(certificates: list[Certificate]) -> str:
    by_email = sum(1 for c in certificates if c.delivery_method == Certificate.DeliveryMethod.EMAIL)
    by_hand = len(certificates) - by_email
    parts = []
    if by_email:
        parts.append(f"{by_email} will be emailed again once the new PDF is ready")
    if by_hand:
        parts.append(f"{by_hand} hard {'copy needs' if by_hand == 1 else 'copies need'} reprinting (Mark handed over afterwards)")
    text = f"Reissued {_certificates(len(certificates))} with the current signatures. {'; '.join(parts)}."
    return email_sent_message("", before=text + " ").strip() if by_email else text


@permission_required(Permission.APPROVE_CERTIFICATES)
@post_only_or_tab("certificates")
def reissue_one(request: HttpRequest, activity_id: int, certificate_id: int) -> HttpResponse:
    """The preview's "Reissue with current signatures"."""
    activity = _activity(activity_id)
    certificate = get_object_or_404(Certificate, pk=certificate_id, activity=activity)
    try:
        services.reissue_certificate(request.user, certificate)
    except services.CertificateError as error:
        messages.error(request, f"Not reissued: {error.problems[0] if error.problems else error}")
    else:
        messages.success(request, reissued_message([certificate]))
    return redirect(pages.tab_url(activity.pk, preview=certificate.pk))


@permission_required(Permission.APPROVE_CERTIFICATES)
@post_only_or_tab("certificates")
def reissue_all(request: HttpRequest, activity_id: int) -> HttpResponse:
    """"Reissue all outdated certificates (N)" after the confirm dialog."""
    activity = _activity(activity_id)
    targets = list(services.outdated_certificates(activity))
    try:
        services.reissue_certificates(request.user, activity=activity)
    except services.CertificateError as error:
        messages.error(request, str(error))
    else:
        messages.success(request, reissued_message(targets))
    return redirect(pages.tab_url(activity.pk))


# --- The Certificate text page: the default wording (can_approve_certificates, ASSUMPTIONS 370) ----------------------


def _text_page(request: HttpRequest, *, values: dict | None = None, errors: dict | None = None,
               status: int = 200) -> HttpResponse:
    context = {"page_title": "Certificate text", "page_subtitle": "The default wording printed on certificates of participation and appreciation",
               **pages.default_text_page(values, errors)}
    return render(request, "certificates/text.html", context, status=status)


@permission_required(Permission.APPROVE_CERTIFICATES)
@require_GET
def certificate_text(request: HttpRequest) -> HttpResponse:
    return _text_page(request)


@permission_required(Permission.APPROVE_CERTIFICATES)
@require_POST
def certificate_text_save(request: HttpRequest, certificate_type: str) -> HttpResponse:
    """POST the fields "<type>-<field>" of one type (after the impact dialog): services.save_default_text."""
    from . import wording

    if certificate_type not in services.TEXT_TYPES:
        return redirect("certificates:text")
    values = {field: request.POST.get(f"{certificate_type}-{field}", "") for field in wording.FIELDS}
    try:
        result = services.save_default_text(request.user, certificate_type, values)
    except ValidationError as error:
        errors = {(key if key.startswith(certificate_type) else f"{certificate_type}-{key}"): value
                  for key, value in error.message_dict.items()}
        return _text_page(request, values=request.POST, errors=errors, status=400)
    label = services.TEXT_TYPE_LABELS[certificate_type]
    if not result["changed"]:
        messages.info(request, "Nothing was changed.")
    else:
        text = f"Saved the default text of the Certificate of {label}."
        if result["activities"]:
            text += (f" {_certificates(result['updated'])} of {result['activities']} "
                     f"activit{'y' if result['activities'] == 1 else 'ies'} now use it.")
        if result["cancelled"]:
            text += (f" The certificate approval of {result['cancelled']} "
                     f"activit{'y was' if result['cancelled'] == 1 else 'ies was'} cancelled.")
        messages.success(request, text)
    return redirect("certificates:text")


@permission_required(Permission.CREATE_ACTIVITY)
@require_POST
def new_activity_text_preview(request: HttpRequest) -> HttpResponse:
    """HTMX, while typing custom wording on the New activity form (ASSUMPTIONS 420): the wording filled with sample
    values (services.wording_preview without an activity). Nothing is saved."""
    from . import wording

    kind = request.POST.get("type", "")
    if kind not in services.TEXT_TYPES:
        return HttpResponse(status=400)
    values = {field: request.POST.get(f"{kind}-{field}", "") for field in wording.FIELDS}
    return render(request, "certificates/_text_preview.html", {"preview": services.wording_preview(None, kind, values)})


@permission_required(Permission.APPROVE_CERTIFICATES)
@require_POST
def certificate_text_preview(request: HttpRequest) -> HttpResponse:
    """HTMX, while typing on the Certificate text page: the default wording filled with sample values."""
    from . import wording

    kind = request.POST.get("type", "")
    if kind not in services.TEXT_TYPES:
        return HttpResponse(status=400)
    values = {field: request.POST.get(f"{kind}-{field}", "") for field in wording.FIELDS}
    return render(request, "certificates/_text_preview.html", {"preview": services.wording_preview(None, kind, values)})


# --- The Signatories page (can_manage_signatories, ASSUMPTIONS 363, 418) -----------------------------------------


def _signatories_page(request: HttpRequest, *, errors: dict | None = None, values: dict | None = None,
                      open_form: str = "", status: int = 200) -> HttpResponse:
    context = {"page_title": "Signatories", "page_subtitle": "The people whose names and signatures are printed on certificates, and the two defaults",
               **pages.signatories_page(request.user, errors, values, open_form)}
    return render(request, "certificates/signatories.html", context, status=status)


def _back(request: HttpRequest) -> str:
    return pages.safe_next(request, request.POST.get("next"), reverse("certificates:signatories"))


def _flat(error: ValidationError) -> dict:
    if hasattr(error, "message_dict"):
        return {key: " ".join(values) for key, values in error.message_dict.items()}
    return {"__all__": " ".join(error.messages)}


@permission_required(Permission.MANAGE_SIGNATORIES)
@require_GET
def signatories(request: HttpRequest) -> HttpResponse:
    return _signatories_page(request)


@permission_required(Permission.MANAGE_SIGNATORIES)
@require_POST
def signatory_add(request: HttpRequest) -> HttpResponse:
    """POST name, position, file (optional); from the Certificates tab also activity + slot (the new person then signs
    there) and next."""
    values = {"name": request.POST.get("name", ""), "position": request.POST.get("position", "")}
    try:
        person = services.add_signatory(request.user, values["name"], values["position"], request.FILES.get("file"),
                                        keep_background=_keep_background(request))
    except ValidationError as error:
        if request.POST.get("next"):
            messages.error(request, "Not added: " + " ".join(error.messages))
            return redirect(_back(request))
        return _signatories_page(request, errors=_flat(error), values=values, open_form="add", status=400)
    text = f"{services.signatory_label(person)} was added to the list."
    activity_id, slot = request.POST.get("activity", ""), request.POST.get("slot", "")
    if activity_id.isdigit() and slot in services.SLOTS:
        activity = _activity(int(activity_id))
        try:
            result = services.set_activity_signatory(request.user, activity, slot, person)
        except (services.CertificateError, PermissionDenied) as error:  # ASSUMPTIONS 431: only the Director chooses
            text += f" Not chosen for the activity: {error}"
        else:
            text = changed_text({**result, "outdated_activities": 1},
                                what=f"{text} They now sign on the {services.SLOT_LABELS[slot].lower()} for this activity.")
    messages.success(request, text)
    return redirect(_back(request))


@permission_required(Permission.MANAGE_SIGNATORIES)
@require_POST
def signatory_edit(request: HttpRequest, signatory_id: int) -> HttpResponse:
    """POST name, position: in place when no certificate uses the person, else a new version (services.edit_signatory)."""
    person = get_object_or_404(CertificateSignatory, pk=signatory_id)
    try:
        result = services.edit_signatory(request.user, person, request.POST.get("name", ""), request.POST.get("position", ""))
    except ValidationError as error:
        messages.error(request, "Not saved: " + " ".join(f"{key.capitalize()}: {value}" for key, value in _flat(error).items()))
        return redirect(_back(request))
    except services.CertificateError as error:
        messages.error(request, str(error))
        return redirect(_back(request))
    if result["action"] == "unchanged":
        messages.info(request, "Nothing was changed.")
    else:
        what = (f"Saved {services.signatory_label(result['signatory'])}." if result["action"] == "edited"
                else f"Saved a new version of {services.signatory_label(result['signatory'])} "
                     f"({_plural(result['activities'], 'activity', 'activities')} updated).")
        messages.success(request, changed_text(result, what=what))
    return redirect(_back(request))


def _plural(count: int, one: str, many: str) -> str:
    return f"{count} {one if count == 1 else many}"


@permission_required(Permission.MANAGE_SIGNATORIES)
@require_POST
def signatory_image(request: HttpRequest, signatory_id: int) -> HttpResponse:
    """POST file: add or replace the person's signature image (services.replace_signature)."""
    person = get_object_or_404(CertificateSignatory, pk=signatory_id)
    try:
        result = services.replace_signature(request.user, person, request.FILES.get("file"),
                                            keep_background=_keep_background(request))
    except ValidationError as error:
        messages.error(request, " ".join(error.messages))
    except services.CertificateError as error:
        messages.error(request, str(error))
    else:
        messages.success(request, changed_text(result, what=f"{result['action'].capitalize()} the signature of "
                                                            f"{services.signatory_label(result['signatory'])}."))
    return redirect(_back(request))


@permission_required(Permission.MANAGE_SIGNATORIES)
@require_POST
def signatory_remove_background(request: HttpRequest, signatory_id: int) -> HttpResponse:
    """"Remove white background" on the Signatories page (services.remove_signature_background: a new version)."""
    person = get_object_or_404(CertificateSignatory, pk=signatory_id)
    try:
        result = services.remove_signature_background(request.user, person)
    except services.CertificateError as error:
        messages.error(request, str(error))
    else:
        messages.success(request, changed_text(result, what="Removed the white background of the signature of "
                                                            f"{services.signatory_label(result['signatory'])} (a new version)."))
    return redirect(_back(request))


@permission_required(Permission.MANAGE_SIGNATORIES)
@require_POST
def signature_preview(request: HttpRequest) -> HttpResponse:
    """HTMX, from the upload dialogs: the chosen image as it will be stored (white background removed unless "Keep
    the background"), on a checkered background. Nothing is saved (services.signature_preview)."""
    file = request.FILES.get("file")
    context = {}
    if file is not None:
        try:
            context["preview"] = services.signature_preview(request.user, file, _keep_background(request))
        except ValidationError as error:
            context["error"] = " ".join(error.messages)
    return render(request, "certificates/_signature_preview.html", context)


@permission_required(Permission.MANAGE_SIGNATORIES)
@require_POST
def signatory_add_inline(request: HttpRequest) -> HttpResponse:
    """HTMX, from the activity form's "Add a person…" (ASSUMPTIONS 365): add the person (services.add_signatory) and
    tell the form (event "signatory-added": the new option, selected in that slot). The activity itself is saved only
    with the form. Errors come back in the dialog."""
    values = {"name": request.POST.get("name", ""), "position": request.POST.get("position", ""),
              "slot": request.POST.get("slot", "") if request.POST.get("slot") in services.SLOTS else "left"}
    context = {"values": values, "max_mb": settings.CERTIFICATE_IMAGE_MAX_MB, "add_url": reverse("certificates:signatory_add_inline")}
    try:
        person = services.add_signatory(request.user, values["name"], values["position"], request.FILES.get("file"),
                                        keep_background=_keep_background(request))
    except ValidationError as error:
        return render(request, "certificates/_add_person_inline_form.html", {**context, "errors": _flat(error)})
    response = render(request, "certificates/_add_person_inline_form.html",
                      {**context, "values": {"slot": values["slot"]}, "added": services.signatory_label(person)})
    response["HX-Trigger"] = json.dumps({"signatory-added": {
        "pk": person.pk, "label": f"{person.name} · {person.position}", "position": person.position, "slot": values["slot"]}})
    return response


@permission_required(Permission.MANAGE_SIGNATORIES)
@require_POST
def signatory_deactivate(request: HttpRequest, signatory_id: int) -> HttpResponse:
    person = get_object_or_404(CertificateSignatory, pk=signatory_id)
    try:
        services.deactivate_signatory(request.user, person)
    except services.CertificateError as error:
        messages.error(request, str(error))
    else:
        messages.warning(request, f"{services.signatory_label(person)} was deactivated: no longer offered in the lists. "
                                  "Activities that use them keep them.")
    return redirect(_back(request))


@permission_required(Permission.MANAGE_SIGNATORIES)
@require_POST
def signatory_defaults(request: HttpRequest) -> HttpResponse:
    """POST left, right (signatory IDs): the people a new activity starts with (services.set_default_signatory)."""
    chosen = {slot: request.POST.get(slot, "") for slot in services.SLOTS}
    people = {slot: CertificateSignatory.objects.filter(pk=raw).first() if raw.isdigit() else None
              for slot, raw in chosen.items()}
    if any(person is None for person in people.values()):
        messages.error(request, "Choose a person for both sides.")
        return redirect(_back(request))
    try:
        result = services.set_default_signatories(request.user, people["left"], people["right"])
    except services.CertificateError as error:
        messages.error(request, str(error))
    else:
        filled = result["filled"]
        extra = (f" {filled} empty slot{'' if filled == 1 else 's'} of activities without certificates now use{'s' if filled == 1 else ''} "
                 "them." if filled else "")
        messages.success(request, "Saved the default signatories. New activities start with them; a slot someone "
                                  f"already chose is never changed.{extra}")
    return redirect(_back(request))


# --- Preview (any active staff member) ---------------------------------------------------------------------------


@staff_login_required
@require_GET
def preview_dialog(request: HttpRequest, certificate_id: int) -> HttpResponse:
    """The preview dialog's content for HTMX (the PDF in an <iframe> + Download PDF); its own page without JavaScript."""
    certificate = get_object_or_404(Certificate.objects.select_related("activity", "registration", "resource_person"), pk=certificate_id)
    context = {"page_title": f"Certificate {certificate.certificate_number}", "page_subtitle": "The certificate as it is printed",
               **pages.preview(certificate, request.user)}
    if is_htmx(request):
        return render(request, "certificates/_preview.html", context)
    return render(request, "certificates/preview_page.html", context)


# --- Evidence (any active staff member; the Step 6 rules) -------------------------------------------------------


@staff_login_required
@require_GET
def evidence(request: HttpRequest, certificate_id: int) -> HttpResponse:
    """The evidence list and upload form: the modal's content for HTMX, a page of its own without JavaScript."""
    certificate = get_object_or_404(Certificate.objects.select_related("activity", "registration", "resource_person"), pk=certificate_id)
    context = {"page_title": f"Evidence for {certificate.certificate_number}", "page_subtitle": EVIDENCE_SUBTITLE, **pages.evidence(request, certificate)}
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
        context = {"page_title": f"Evidence for {certificate.certificate_number}", "page_subtitle": EVIDENCE_SUBTITLE,
                   **pages.evidence(request, certificate, errors, title)}
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
