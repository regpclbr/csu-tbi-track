"""Display helpers for the Certificates pages (phase F4): the activity's Certificates tab, the Certificates page and the
evidence modal, the preview page. Display only: every rule is in certificates.services (who may approve or act, the
status moves, readiness, the missing signature reason). This module turns their results into what the templates show
(ASSUMPTIONS 357-364). Also the Signatories page (/certificates/signatories/).
"""

from collections import Counter
from dataclasses import dataclass, field
from urllib.parse import urlencode

from django.db.models import Count, Q
from django.http import HttpRequest, QueryDict
from django.conf import settings
from django.urls import reverse

from accounts.permissions import Permission, has_permission
from activities.models import Activity
from config.links import activity_tab_url, is_internal_path
from config.mail import prints_emails
from documents import pages as document_pages
from registrations.models import EvaluationSubmission

from . import services, wording
from .models import ActivitySignatory, Certificate, CertificateSignatory

Status = Certificate.Status
RecipientType = Certificate.RecipientType
DeliveryMethod = Certificate.DeliveryMethod

STATUS_TONES = {Status.GENERATED: "warning", Status.APPROVED: "info", Status.ISSUED: "success"}
KIND_LABELS = {RecipientType.REGISTRATION: "Participation", RecipientType.RESOURCE_PERSON: "Appreciation"}
DELIVERY_LABELS = {DeliveryMethod.EMAIL: "Email", DeliveryMethod.HARD_COPY: "Hard copy"}
PREPARING_LABEL = "Being prepared"  # Approved, the PDF not rendered yet (the background worker does it)
ISSUED_NOTES = {
    DeliveryMethod.EMAIL: "Issued — this copy was already sent and is not changed.",
    DeliveryMethod.HARD_COPY: "Issued — this copy was already handed over and is not changed.",
}
REISSUED_NOTES = {
    DeliveryMethod.EMAIL: "Issued — updated copy with the current signatures (reissued).",
    DeliveryMethod.HARD_COPY: "Issued — updated copy with the current signatures (reissued).",
}
OUTDATED_LABEL = "Issued with an old signature"
UPDATED_COPY_LABEL = "Updated copy being prepared"
OUTDATED_NOTE = ("Issued with an old signature: this copy was printed with a signatory (or signature image) that this "
                 "activity no longer uses.")
REPRINT_LABEL = "Not handed over (updated copy to print)"
# Why an Approved (or reissued) certificate has no PDF yet: services.pdf_state (ASSUMPTIONS 368).
PDF_STATE_LABELS = {
    services.PDF_PRE_RULE_WAITING: ("Approved before the evaluation rule — waiting for the evaluation", "neutral"),
    services.PDF_PRE_RULE_CLOSED: ("Approved before the evaluation rule — evaluation closed without an answer", "neutral"),
    services.PDF_FAILED: ("PDF failed — Retry", "danger"),
    services.PDF_WORKER: ("Waiting for the background worker", "warning"),
    services.PDF_PREPARING: ("PDF being prepared", "neutral"),
}
PROBLEMS_KEY = "certificate_problems:{}"  # session: a refused row action's list, shown once on the tab


def tab_url(activity_id: int, preview: int | None = None) -> str:
    """The activity's Certificates tab (every action ends here, ASSUMPTIONS 362); preview=<id> opens that certificate
    in the preview card."""
    url = activity_tab_url(activity_id, "certificates")
    return f"{url}&preview={preview}#certificate-preview" if preview else url


def keep_problems(request: HttpRequest, activity: Activity, problems: list[str]) -> None:
    """Keep a refused action's list for the next view of the tab (the action redirects there, never renders)."""
    request.session[PROBLEMS_KEY.format(activity.pk)] = list(problems)


def recipient(certificate: Certificate) -> str:
    """Who the certificate is for, as registered: the registration name or the resource person's name. (The printed
    name of a participant comes from the evaluation: printed_names.)"""
    if certificate.recipient_type == RecipientType.RESOURCE_PERSON:
        return certificate.resource_person.name
    return certificate.registration.name


def printed_names(certificates: list[Certificate]) -> dict[int, str]:
    """Registration id -> the name typed in the evaluation (certificate_name), one query. The list, the preview header
    and the PDF then show the same name (ASSUMPTIONS 362)."""
    ids = [c.registration_id for c in certificates if c.registration_id]
    if not ids:
        return {}
    return dict(EvaluationSubmission.objects.filter(registration_id__in=ids).values_list("registration_id", "certificate_name"))


def names(certificate: Certificate, printed: dict[int, str]) -> tuple[str, str]:
    """(name shown, "registered as" name or ""): the printed name first, the registration name only when it differs."""
    registered = recipient(certificate)
    shown = printed.get(certificate.registration_id) or registered
    return shown, registered if shown.strip().casefold() != registered.strip().casefold() else ""


class WorkerCheck:
    """Whether qcluster is running, asked at most once per page (and only when a certificate waits for a PDF)."""

    def __init__(self) -> None:
        self._running: bool | None = None

    def __call__(self) -> bool:
        if self._running is None:
            from accounts.system_status import OK, qcluster_status

            self._running = qcluster_status().tone == OK
        return self._running


def sending(certificate: Certificate, pdf: str | None = None) -> dict:
    """The "Sent" column: label and tone. While an Approved (or reissued) certificate has no PDF: the real reason
    (services.pdf_state, ASSUMPTIONS 368: approved before the evaluation rule, PDF failed — Retry, waiting for the
    background worker, PDF being prepared). Otherwise: hard copy (or a reissued one to print again); sent (date);
    Send failed (Resend); waiting (why)."""
    if pdf:
        label, tone = PDF_STATE_LABELS[pdf]
        return {"key": f"pdf_{pdf}", "label": label, "tone": tone, "error": certificate.render_error or ""}
    if certificate.delivery_method == DeliveryMethod.HARD_COPY:
        if certificate.status == Status.ISSUED and services.awaiting_hand_over(certificate):
            return {"key": "reprint", "label": REPRINT_LABEL, "tone": "warning"}
        return {"key": "hard_copy", "label": "Hard copy", "tone": "neutral"}
    if certificate.sent_at:
        return {"key": "sent", "label": "Sent", "tone": "success"}
    if certificate.send_failed_at:
        return {"key": "failed", "label": "Send failed", "tone": "danger"}
    if certificate.status == Status.ISSUED:
        return {"key": "queued", "label": "Being emailed", "tone": "neutral"}
    if services.is_approved(certificate.activity):  # approved activity: sent by itself after the evaluation (369)
        return {"key": "approval", "label": "Sent automatically after the evaluation", "tone": "neutral"}
    return {"key": "approval", "label": "After approval", "tone": "neutral"}


def state_label(certificate: Certificate, label: str | None, outdated: bool = False, pdf: str | None = None) -> dict | None:
    """The small second badge beside the status: for one waiting for approval, what it waits for besides the
    activity's approval (services.readiness: the evaluation, the signatures; ASSUMPTIONS 369); for an Approved one
    whose PDF failed, "PDF failed", while it is rendered "Being prepared"; for an Issued one, "Issued with an old
    signature" (services.outdated_q) or "Updated copy being prepared" (reissued)."""
    if outdated:
        return {"label": OUTDATED_LABEL, "tone": "warning"}
    if pdf == services.PDF_FAILED:
        return {"label": "PDF failed", "tone": "danger"}
    if services.updated_copy_pending(certificate):
        return {"label": UPDATED_COPY_LABEL, "tone": "neutral"}
    if certificate.status == Status.GENERATED:
        return {"label": label, "tone": "neutral"} if label else None
    if pdf in (services.PDF_PREPARING, services.PDF_WORKER):
        return {"label": PREPARING_LABEL, "tone": "neutral"}
    return None


def certificate_row(certificate: Certificate, printed: dict[int, str] | None = None, label: str | None = None,
                    can_act: bool = False, outdated: bool = False, worker: WorkerCheck | None = None,
                    can_approve: bool = False) -> dict:
    printed = printed or {}
    shown, registered_as = names(certificate, printed)
    hard_copy = certificate.delivery_method == DeliveryMethod.HARD_COPY
    pdf = services.pdf_state(certificate, evaluated=certificate.registration_id in printed, worker_running=worker or WorkerCheck())
    return {
        "certificate": certificate,
        "recipient": shown,
        "registered_as": registered_as,
        "kind": KIND_LABELS[certificate.recipient_type],
        "delivery": DELIVERY_LABELS[certificate.delivery_method],
        "status": {"label": certificate.get_status_display(), "tone": STATUS_TONES[certificate.status]},
        "state": state_label(certificate, label, outdated, pdf),
        "outdated": outdated,
        "sending": sending(certificate, pdf),
        "can_retry": can_approve and pdf == services.PDF_FAILED,  # "Retry" (ASSUMPTIONS 368)
        "pdf_ready": bool(certificate.file_path),
        "evidence_count": getattr(certificate, "evidence_count", 0),
        # The row's "Mark handed over" (can_create_activity): an Approved hard copy whose PDF is ready, or a reissued
        # one (the updated copy, ASSUMPTIONS 364).
        "can_hand_over": can_act and hard_copy and services.awaiting_hand_over(certificate) and bool(certificate.file_path),
    }


def _plural(count: int, one: str, many: str) -> str:
    return f"{count} {one if count == 1 else many}"


def approval_panel(member, activity: Activity, certificates: list[Certificate]) -> dict:
    """The approval state at the top of the tab (ASSUMPTIONS 369, 373), for everyone: services.approval_state ("Approved
    by ... on ...", "Needs approval" when certificates wait, "All certificates issued (N)", "Certificates are created
    automatically ..." or the grey "Not approved — ..."). For can_approve_certificates only, and only when the state
    offers approval: "Approve certificates for this activity" (with the confirm dialog's counts) or the reason it is
    closed (services.approval_closed_reason). All issued / no certificates: no button and no reason at all. Admin
    Staff: the state only. `certificates` = all of the activity's certificates."""
    approved = services.is_approved(activity)
    can_approve = has_permission(member, Permission.APPROVE_CERTIFICATES)
    not_issued = [c for c in certificates if c.status != Status.ISSUED]
    by_email = sum(1 for c in not_issued if c.delivery_method == DeliveryMethod.EMAIL)
    state = services.approval_state(activity, Counter(c.status for c in certificates))
    offered = can_approve and not approved and state.offers_approval
    return {
        "approved": approved,
        "state": state.kind,
        "text": state.text,
        "note": state.note,
        "has_certificates": bool(certificates),
        "can_approve": offered,
        "closed_reason": services.approval_closed_reason(activity) if offered else "",
        "by_email": by_email,
        "by_hand": len(not_issued) - by_email,
        "url": reverse("activities:certificates_approve_activity", args=[activity.pk]),
    }


def text_panel(member, activity: Activity, values: dict | None = None, errors: dict | None = None,
               mode: str | None = None) -> dict:
    """The Certificate text section (ASSUMPTIONS 370): "Use the default text" / "Custom text for this activity", the
    fields per type (the activity's current text; after a refused save, what was typed and the errors), the
    placeholders, the limits and a first live preview (services.wording_preview). can_create_activity or
    can_approve_certificates edit it; others see the text only."""
    custom = services.uses_custom_text(activity)
    current = services.current_texts(activity)
    defaults = services.current_texts(None)
    errors = errors or {}
    types = []
    for kind in services.TEXT_TYPES:
        shown = {field: (values or {}).get(f"{kind}-{field}", value)
                 for field, value in services.text_values(current[kind]).items()}
        preview = services.wording_preview(activity, kind, shown)
        types.append({
            "type": kind,
            "label": f"Certificate of {services.TEXT_TYPE_LABELS[kind]}",
            "fields": [{"name": f"{kind}-{field}", "field": field, "label": wording.FIELD_LABELS[field],
                        "value": shown[field], "max": wording.MAX_LENGTHS[field],
                        "required": field in wording.REQUIRED, "error": " ".join(errors.get(f"{kind}-{field}", [])),
                        "default": getattr(defaults[kind], field)} for field in wording.FIELDS],
            "custom": current[kind].activity_id is not None,
            "placeholders": wording.placeholder_list(kind),
            "preview": preview,
        })
    return {
        "custom": custom if mode is None else mode == "custom",
        "can_edit": services.can_edit_activity_text(member),
        "types": types,
        "save_url": reverse("activities:certificates_text", args=[activity.pk]),
        "preview_url": reverse("activities:certificates_text_preview", args=[activity.pk]),
        "defaults_url": reverse("certificates:text") if has_permission(member, Permission.APPROVE_CERTIFICATES) else "",
    }


def signature_image_url(signatory: CertificateSignatory | None, member) -> str:
    """The thumbnail URL (signature images: can_manage_users or can_approve_certificates only), or ""."""
    if signatory is None or not signatory.signature_image_path:
        return ""
    if has_permission(member, Permission.APPROVE_CERTIFICATES) or has_permission(member, Permission.MANAGE_USERS):
        return reverse("certificates:signature_image", args=[signatory.pk])
    return ""


def signatures_panel(member, activity: Activity) -> dict:
    """The Signatures section (ASSUMPTIONS 363, 365, 367): the left and right slot with the activity's signatory, the
    title printed for them (services.printed_title: "Title on this certificate", else the position), "(inactive)",
    whether the image is there; a dropdown of active people (+ "Add a person…" for approvers) and the title field for
    can_create_activity / can_approve_certificates; for an inactive person an amber note with "Use the current
    version" when a newer active version exists (services.inactive_slots); the image form (with the impact of a new
    version, services.version_impact) and "Add a person" for can_approve_certificates. Without these flags: view only."""
    can_choose = services.can_choose_signatories(member)
    can_upload = has_permission(member, Permission.APPROVE_CERTIFICATES)
    rows = services.activity_slots(activity)
    inactive = services.inactive_slots(activity)
    people = list(services.active_signatories()) if can_choose else []
    slots = []
    for slot in services.SLOTS:
        row = rows[slot]
        signatory = row.signatory if row else None
        present = services.signature_present(signatory)
        replaces = can_upload and signatory is not None and present
        impact = services.version_impact(signatory) if replaces and services.signatory_in_use(signatory) else None
        newer = inactive.get(slot)
        slots.append({
            "slot": slot,
            "label": services.SLOT_LABELS[slot],
            "signatory": signatory,
            "title": services.printed_title(row),
            "custom_title": row.position_on_certificate or "" if row else "",
            "inactive": slot in inactive,
            "newer": newer,
            "current_version_url": reverse("activities:certificates_signatory_current", args=[activity.pk, slot]),
            "present": present,
            "image_url": signature_image_url(signatory, member) if present else "",
            "choices": people,
            "set_url": reverse("activities:certificates_signatory", args=[activity.pk, slot]),
            "upload_url": reverse("activities:certificates_signature", args=[activity.pk, slot]),
            "impact": services.version_impact_text(impact) if impact else "",
        })
    return {
        "slots": slots,
        "can_choose": can_choose,
        "can_upload": can_upload,
        "can_edit": can_upload,
        "missing_reason": services.missing_slots_reason({slot: row.signatory if row else None for slot, row in rows.items()}) or "",
        "max_mb": settings.CERTIFICATE_IMAGE_MAX_MB,
        "add_url": reverse("certificates:signatory_add"),
        "list_url": reverse("certificates:signatories"),
    }


def reissue_panel(member, activity: Activity, outdated: set[int]) -> dict | None:
    """"Reissue all outdated certificates (N)" (can_approve_certificates only, ASSUMPTIONS 364): the counts for the
    confirm dialog (emailed again / hard copies to reprint) and the reason it is closed. None for others or when
    nothing is outdated."""
    if not outdated or not has_permission(member, Permission.APPROVE_CERTIFICATES):
        return None
    counts = services.outdated_counts(activity)
    return {**counts, "closed_reason": services.reissue_closed_reason(activity),
            "label": f"Reissue all outdated certificates ({counts['total']})",
            "url": reverse("activities:certificates_reissue_all", args=[activity.pk])}


def preview_items(certificates: list[Certificate], printed: dict[int, str], labels: dict[int, str | None],
                  can_approve: bool = False, outdated: set[int] | None = None) -> list[dict]:
    """The large preview with Previous / Next: exactly the list below it, in the same order (ASSUMPTIONS 362). The
    approver checks it before "Approve certificates for this activity" (ASSUMPTIONS 369: no Approve per certificate).
    Each item: the printed name (and "registered as"), the URLs with ?v= (services.preview_version: a changed
    certificate is never shown from the browser's cache), the Issued note and "Issued with an old signature" with
    "Reissue with current signatures" (approvers only, ASSUMPTIONS 364)."""
    outdated = outdated or set()
    items = []
    for c in certificates:
        shown, registered_as = names(c, printed)
        evaluated = c.registration_id in printed
        version = services.preview_version(c, evaluated)
        items.append({
            "pk": c.pk,
            "url": f"{reverse('certificates:preview', args=[c.pk])}?v={version}",
            "download_url": f"{reverse('certificates:download', args=[c.pk])}?v={version}",
            "number": c.certificate_number,
            "recipient": shown,
            "registered_as": registered_as,
            "kind": KIND_LABELS[c.recipient_type],
            "status": c.get_status_display(),
            "name_final": c.recipient_type == RecipientType.RESOURCE_PERSON or evaluated,
            "issued_note": issued_note(c, c.pk in outdated),
            "outdated": c.pk in outdated,
            "can_reissue": can_approve and c.pk in outdated,
            "reissue_url": reverse("activities:certificates_reissue", args=[c.activity_id, c.pk]) if can_approve and c.pk in outdated else "",
        })
    return items


def issued_note(certificate: Certificate, outdated: bool) -> str:
    """The note above an Issued certificate's preview."""
    if outdated:
        return OUTDATED_NOTE
    if services.updated_copy_pending(certificate):
        return "Issued — the updated copy is being prepared (shown here as it will look)."
    if not services.shows_final_copy(certificate):
        return ""
    return REISSUED_NOTES[certificate.delivery_method] if certificate.reissued_at else ISSUED_NOTES[certificate.delivery_method]


def preview_start(items: list[dict], raw: str | None) -> int:
    """Where the preview starts: the certificate named by ?preview=<id> (after an approval), else the first row."""
    if raw and raw.isdigit():
        for index, item in enumerate(items):
            if item["pk"] == int(raw):
                return index
    return 0


def _with_evidence_count(queryset):
    return queryset.annotate(evidence_count=Count("documents", filter=Q(documents__related_certificate__isnull=False)))


def status_counts(counts: dict[str, int]) -> list[dict]:
    """services.certificate_status_counts() in status order, with labels and badge tones."""
    return [
        {"value": value, "label": label, "tone": STATUS_TONES[value], "count": counts.get(value, 0)}
        for value, label in Status.choices
    ]


def worker_notice(certificates: list[Certificate]) -> bool:
    """True when this activity has background work waiting (a PDF to render, an email to send) and the background
    worker (qcluster) is not running: the tab says so instead of leaving "Being prepared" unexplained."""
    waiting = any(
        (c.status == Status.APPROVED and not c.file_path and not services._not_ready_reason(c))
        or services.updated_copy_pending(c)  # reissued (ASSUMPTIONS 364)
        or (c.status == Status.ISSUED and c.delivery_method == DeliveryMethod.EMAIL and not c.sent_at and not c.send_failed_at)
        for c in certificates
    )
    if not waiting:
        return False
    from accounts.system_status import OK, qcluster_status

    return qcluster_status().tone != OK


# --- The activity's Certificates tab -----------------------------------------------------------------------------


def certificates_tab(request: HttpRequest, activity: Activity, problems: list[str] | None = None,
                     text_form: dict | None = None) -> dict:
    """The tab (ASSUMPTIONS 362, 369, 370): the approval state and "Approve certificates for this activity"
    (approvers only), counts per status, why certificates are not created yet (generation_closed_reason; they are
    created by the System), Signatures, Certificate text, the preview, and the list (Preview, Mark handed over,
    Resend, PDF, Evidence). `problems`: a refused row action's list (kept in the session for this one view).
    `text_form`: a refused Certificate text save ({"values", "errors", "mode"})."""
    certificates = list(_with_evidence_count(
        Certificate.objects.filter(activity=activity)
        .select_related("activity", "registration", "resource_person", "left_signatory", "right_signatory")
    ).order_by("certificate_number"))
    member = request.user
    can_act = has_permission(member, Permission.CREATE_ACTIVITY)
    can_approve = has_permission(member, Permission.APPROVE_CERTIFICATES)
    labels = services.readiness_map(certificates)
    printed = printed_names(certificates)
    outdated = services.outdated_ids(activity)
    previews = preview_items(certificates, printed, labels, can_approve, outdated)
    start = preview_start(previews, request.GET.get("preview"))
    kept = request.session.pop(PROBLEMS_KEY.format(activity.pk), None) if hasattr(request, "session") else None
    worker = WorkerCheck()
    return {
        "rows": [certificate_row(c, printed, labels.get(c.pk), can_act, c.pk in outdated, worker, can_approve)
                 for c in certificates],
        "counts": status_counts(services.certificate_status_counts({"activity": activity.pk})),
        "can_act": can_act,
        "can_approve": can_approve,
        "generation_reason": services.generation_closed_reason(activity),
        "approval": approval_panel(member, activity, certificates),
        "signatures": signatures_panel(member, activity),
        "text": text_panel(member, activity, **(text_form or {})),
        "reissue": reissue_panel(member, activity, outdated),
        "max_mb": settings.CERTIFICATE_IMAGE_MAX_MB,  # the "Add a person" dialog
        "previews": previews,
        "preview_start": start,
        "shown": previews[start] if previews else None,  # the certificate the preview card shows first
        "can_apply_design": can_act and any(c.status != Status.ISSUED for c in certificates),
        "worker_stopped": worker_notice(certificates),
        "emails_printed": prints_emails() and any(c.delivery_method == DeliveryMethod.EMAIL for c in certificates),
        "problems": problems or kept or [],
        "back": tab_url(activity.pk),
    }


def default_text_page(values: dict | None = None, errors: dict | None = None) -> dict:
    """The Certificate text page (can_approve_certificates, ASSUMPTIONS 370): the default wording of both types, the
    placeholders, the limits, a first preview with sample values, and the impact of saving (the confirm dialog:
    services.default_text_impact)."""
    defaults = services.current_texts(None)
    errors = errors or {}
    types = []
    for kind in services.TEXT_TYPES:
        shown = {field: (values or {}).get(f"{kind}-{field}", value)
                 for field, value in services.text_values(defaults[kind]).items()}
        types.append({
            "type": kind,
            "label": f"Certificate of {services.TEXT_TYPE_LABELS[kind]}",
            "fields": [{"name": f"{kind}-{field}", "field": field, "label": wording.FIELD_LABELS[field],
                        "value": shown[field], "max": wording.MAX_LENGTHS[field],
                        "required": field in wording.REQUIRED, "error": " ".join(errors.get(f"{kind}-{field}", []))}
                       for field in wording.FIELDS],
            "placeholders": wording.placeholder_list(kind),
            "preview": services.wording_preview(None, kind, shown),
            "impact": services.default_text_impact_text(services.default_text_impact(kind)),
            "save_url": reverse("certificates:text_save", args=[kind]),
            "changed_by": defaults[kind].created_by.name if defaults[kind].created_by_id else "System (the original text)",
            "changed_at": defaults[kind].created_at,
        })
    return {"types": types, "preview_url": reverse("certificates:text_preview"),
            "signatories_url": reverse("certificates:signatories")}


# --- The preview page (without JavaScript) --------------------------------------------------------------------


def preview(certificate: Certificate, member=None) -> dict:
    """The preview page: the PDF (certificates:preview in an <iframe>, ?v=), Download PDF, the printed name and
    whether it is final, and the Issued note (approval is per activity, on the tab: ASSUMPTIONS 369)."""
    printed = printed_names([certificate])
    labels = services.readiness_map([certificate])
    can_approve = member is not None and has_permission(member, Permission.APPROVE_CERTIFICATES)
    outdated = services.outdated_ids(certificate.activity) & {certificate.pk}
    item = preview_items([certificate], printed, labels, can_approve, outdated)[0]
    return {
        "certificate": certificate,
        "item": item,
        "recipient": item["recipient"],
        "registered_as": item["registered_as"],
        "kind": item["kind"],
        "status": {"label": certificate.get_status_display(), "tone": STATUS_TONES[certificate.status]},
        "name_final": item["name_final"],
        "marked": services.preview_marked(certificate),
        "file_url": item["url"],
        "download_url": item["download_url"],
        "back": tab_url(certificate.activity_id, preview=certificate.pk),
    }


# --- The Certificates page --------------------------------------------------------------------------------------


VIEW_BY_ACTIVITY, VIEW_ALL = "activity", "all"  # ?view= (ASSUMPTIONS 372); "activity" is the default
VIEWS = [(VIEW_BY_ACTIVITY, "By activity (recommended)", "layout-grid"), (VIEW_ALL, "All certificates (newest first)", "list")]
SEARCH_MAX_LENGTH = 100


@dataclass
class CertificateFilters:
    """The Certificates page's filters, the search and the view, all in the URL. A value that cannot be read is ignored
    with a note (as the other lists); an unknown view is the default one."""

    activity: int | None = None
    status: str = ""
    recipient_type: str = ""
    delivery_method: str = ""
    q: str = ""
    view: str = VIEW_BY_ACTIVITY
    page: str = "1"
    ignored: list[str] = field(default_factory=list)

    def for_service(self) -> dict:
        return {"activity": self.activity, "status": self.status, "recipient_type": self.recipient_type,
                "delivery_method": self.delivery_method, "q": self.q}

    def query(self, **changes) -> str:
        """The URL's query without the page (the pager adds it); the default view is left out."""
        values = {**self.for_service(), "view": self.view, **changes}
        if values["view"] == VIEW_BY_ACTIVITY:
            values["view"] = ""
        return urlencode({key: value for key, value in values.items() if value not in (None, "")})

    @property
    def base_query(self) -> str:
        """The other filters, without the status (the status count links add their own)."""
        return self.query(status="")

    @property
    def clear_query(self) -> str:
        """"Clear": no filter, the same view."""
        return self.query(activity=None, status="", recipient_type="", delivery_method="", q="")

    @property
    def any_set(self) -> bool:
        return any(value not in (None, "") for value in self.for_service().values())


def parse_filters(query: QueryDict) -> CertificateFilters:
    ignored: list[str] = []
    raw_activity = query.get("activity", "")
    activity = int(raw_activity) if raw_activity.isdigit() else None
    if raw_activity and activity is None:
        ignored.append("activity")
    choices = {"status": Status.values, "recipient_type": RecipientType.values, "delivery_method": DeliveryMethod.values}
    values = {}
    for key, allowed in choices.items():
        value = query.get(key, "")
        if value and value not in allowed:
            ignored.append(key.replace("_", " "))
            value = ""
        values[key] = value
    view = query.get("view", "")
    view = view if view in (VIEW_BY_ACTIVITY, VIEW_ALL) else VIEW_BY_ACTIVITY
    q = " ".join(query.get("q", "").split())[:SEARCH_MAX_LENGTH]
    return CertificateFilters(activity=activity, page=query.get("page", "1"), ignored=ignored, q=q, view=view, **values)


def view_links(filters: CertificateFilters) -> list[dict]:
    """The "By activity / All certificates" switch: the filters and the search stay, the page goes back to 1."""
    return [{"key": key, "label": label, "icon": icon, "selected": filters.view == key,
             "url": f"{reverse('certificates:list')}?{filters.query(view=key)}".rstrip("?")}
            for key, label, icon in VIEWS]


def _rows(certificates: list[Certificate]) -> list[dict]:
    """The list rows of one page, in a fixed number of queries whatever the number of certificates or activities:
    evidence counts, readiness, printed names, outdated (one query each) and the worker check once."""
    ids = [certificate.pk for certificate in certificates]
    counted = {c.pk: c.evidence_count for c in _with_evidence_count(Certificate.objects.filter(pk__in=ids))} if ids else {}
    labels = services.readiness_map(certificates)
    printed = printed_names(certificates)
    outdated = set(Certificate.objects.filter(pk__in=ids).filter(services.outdated_q()).values_list("pk", flat=True)) if ids else set()
    worker = WorkerCheck()
    rows = []
    for certificate in certificates:
        certificate.evidence_count = counted.get(certificate.pk, 0)
        rows.append(certificate_row(certificate, printed, labels.get(certificate.pk), outdated=certificate.pk in outdated,
                                    worker=worker))
    return rows


def activity_groups(entries: list) -> list[dict]:
    """services.certificates_by_activity()'s groups for the page: the header (title, link to the tab, the approval state
    as on the tab: services.approval_state with ALL the activity's certificates, whatever the filters; counts
    Waiting for approval / Approved / Issued) and the rows (built together, so the queries do not grow with the number
    of activities: one query for every group's counts per status)."""
    rows = iter(_rows([certificate for entry in entries for certificate in entry.certificates]))
    every = services.approval_counts([entry.activity.pk for entry in entries])
    groups = []
    for entry in entries:
        activity = entry.activity
        state = services.approval_state(activity, every[activity.pk])
        groups.append({
            "activity": activity,
            "url": tab_url(activity.pk),
            "approved": state.kind == services.APPROVED_KIND,
            "approval_state": state.kind,
            "approval": state.text,
            "approval_note": state.note,
            "counts": status_counts(entry.counts),
            "rows": [next(rows) for _ in entry.certificates],
        })
    return groups


def certificate_list(filters: CertificateFilters) -> dict:
    """The Certificates page (ASSUMPTIONS 299, 372): the status counts and either the activity groups ("By activity",
    the default, 10 activities a page) or the flat list ("All certificates", newest number first, 20 a page)."""
    without_status = {**filters.for_service(), "status": ""}
    per_status = services.certificate_status_counts(without_status)
    context = {
        "counts": status_counts(per_status),
        "view_links": view_links(filters),
        # Certificates matching every filter (the counts leave out only the status): no extra query.
        "certificates_total": per_status[filters.status] if filters.status else sum(per_status.values()),
    }
    if filters.view == VIEW_ALL:
        result = services.list_certificates_newest(filters.for_service(), page=filters.page)
        context.update(result=result, rows=_rows(result.items), groups=[])
    else:
        result = services.certificates_by_activity(filters.for_service(), page=filters.page)
        groups = activity_groups(result.items)
        context.update(result=result, groups=groups, rows=[])
    return context


def filter_choices() -> dict:
    return {
        "activities": Activity.objects.filter(pk__in=Certificate.objects.values("activity_id")).order_by(*services.NEWEST_ACTIVITY_FIRST).only("pk", "title"),
        "statuses": Status.choices,
        "recipient_types": [(value, KIND_LABELS[value]) for value in RecipientType.values],
        "delivery_methods": [(value, DELIVERY_LABELS[value]) for value in DeliveryMethod.values],
    }


# --- Evidence modal ---------------------------------------------------------------------------------------------


def evidence(request: HttpRequest, certificate: Certificate, errors: dict | None = None, title: str = "") -> dict:
    """The certificate's evidence documents (services.evidence_for) with the Step 6 actions, and the upload form."""
    return {
        "certificate": certificate,
        "recipient": recipient(certificate),
        "rows": [document_pages.document_row(request.user, d) for d in services.evidence_for(certificate).order_by("-uploaded_at")],
        "errors": errors or {},
        "title": title,
        "limits": document_pages.upload_limits(),
        "back": tab_url(certificate.activity_id),
    }


# --- The Signatories page (/certificates/signatories/, can_approve_certificates; ASSUMPTIONS 363) ------------------


def safe_next(request: HttpRequest, raw: str | None, default: str) -> str:
    """Where a Signatories form goes back to: an internal path only (the activity's Certificates tab), else `default`."""
    return raw if raw and is_internal_path(request, raw) else default


def signatories_page(member, errors: dict | None = None, values: dict | None = None, open_form: str = "") -> dict:
    """The defaults (two dropdowns), "Add a person", the active people (name, position, image, default, how many
    activities use them, Edit / Signature with the impact of a new version, Deactivate) and the inactive ones."""
    active = list(services.active_signatories())
    usage = dict(ActivitySignatory.objects.filter(signatory__in=active).values("signatory").order_by()
                 .annotate(n=Count("activity", distinct=True)).values_list("signatory", "n"))
    in_use = (set(Certificate.objects.filter(left_signatory__in=active).values_list("left_signatory", flat=True))
              | set(Certificate.objects.filter(right_signatory__in=active).values_list("right_signatory", flat=True)))
    people = []
    for person in active:
        used = person.pk in in_use
        impact = services.version_impact(person) if used else None
        people.append({
            "signatory": person,
            "activities": usage.get(person.pk, 0),
            "in_use": used,
            "impact": services.version_impact_text(impact) if impact else "",
            "image_url": signature_image_url(person, member),
            "deactivate_reason": services.deactivate_closed_reason(person) or "",
            "background_removable": services.white_background_removable(person),  # ASSUMPTIONS 366
            "background_id": f"person-background-{person.pk}",
            "default_label": f"Default {services.SLOT_LABELS[person.default_slot].lower()}" if person.default_slot else "",
            "edit_id": f"edit-person-{person.pk}",
            "image_id": f"person-image-{person.pk}",
        })
    return {
        "people": people,
        "inactive": CertificateSignatory.objects.filter(is_active=False).order_by("name", "-created_at"),
        "defaults": services.default_signatories(),
        "slots": [(slot, services.SLOT_LABELS[slot]) for slot in services.SLOTS],
        "errors": errors or {},
        "values": values or {},
        "open_form": open_form,
        "max_mb": settings.CERTIFICATE_IMAGE_MAX_MB,
    }
