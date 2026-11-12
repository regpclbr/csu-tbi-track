"""Display helpers for the Certificates pages (phase F4): the activity's Certificates tab, the Certificates page and the
evidence modal, the preview page. Display only: every rule is in certificates.services (who may approve or act, the
status moves, readiness, the missing signature reason). This module turns their results into what the templates show
(ASSUMPTIONS 357-362).
"""

from dataclasses import dataclass, field
from urllib.parse import urlencode

from django.db.models import Count, Q
from django.http import HttpRequest, QueryDict
from django.conf import settings
from django.urls import reverse

from accounts.permissions import Permission, has_permission
from activities.models import Activity
from config.links import activity_tab_url
from config.mail import prints_emails
from documents import pages as document_pages
from registrations.models import EvaluationSubmission

from . import services
from .models import Certificate

Status = Certificate.Status
RecipientType = Certificate.RecipientType
DeliveryMethod = Certificate.DeliveryMethod

STATUS_TONES = {Status.GENERATED: "warning", Status.APPROVED: "info", Status.ISSUED: "success"}
KIND_LABELS = {RecipientType.REGISTRATION: "Participation", RecipientType.RESOURCE_PERSON: "Appreciation"}
DELIVERY_LABELS = {DeliveryMethod.EMAIL: "Email", DeliveryMethod.HARD_COPY: "Hard copy"}
READY_LABEL = "Ready for approval"
PREPARING_LABEL = "Being prepared"  # Approved, the PDF not rendered yet (the background worker does it)
ISSUED_NOTES = {
    DeliveryMethod.EMAIL: "Issued — this copy was already sent and is not changed.",
    DeliveryMethod.HARD_COPY: "Issued — this copy was already handed over and is not changed.",
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


def sending(certificate: Certificate) -> dict:
    """The "Sent" column: label and tone. Hard copy; sent (date); Send failed (Resend); waiting (why)."""
    if certificate.delivery_method == DeliveryMethod.HARD_COPY:
        return {"key": "hard_copy", "label": "Hard copy", "tone": "neutral"}
    if certificate.sent_at:
        return {"key": "sent", "label": "Sent", "tone": "success"}
    if certificate.send_failed_at:
        return {"key": "failed", "label": "Send failed", "tone": "danger"}
    if certificate.status == Status.ISSUED:
        return {"key": "queued", "label": "Being emailed", "tone": "neutral"}
    if certificate.status == Status.APPROVED:
        return {"key": "evaluation", "label": "After the PDF is ready", "tone": "neutral"}
    return {"key": "approval", "label": "After approval", "tone": "neutral"}


def state_label(certificate: Certificate, label: str | None) -> dict | None:
    """The small second badge beside the status: for one waiting for approval, "Ready for approval" or what it
    waits for (services.readiness); for an Approved one without its PDF yet, "Being prepared"."""
    if certificate.status == Status.GENERATED:
        return {"label": label, "tone": "neutral"} if label else {"label": READY_LABEL, "tone": "success"}
    if certificate.status == Status.APPROVED and not certificate.file_path and not services._not_ready_reason(certificate):
        return {"label": PREPARING_LABEL, "tone": "neutral"}
    return None


def certificate_row(certificate: Certificate, printed: dict[int, str] | None = None, label: str | None = None,
                    can_act: bool = False) -> dict:
    shown, registered_as = names(certificate, printed or {})
    hard_copy = certificate.delivery_method == DeliveryMethod.HARD_COPY
    return {
        "certificate": certificate,
        "recipient": shown,
        "registered_as": registered_as,
        "kind": KIND_LABELS[certificate.recipient_type],
        "delivery": DELIVERY_LABELS[certificate.delivery_method],
        "status": {"label": certificate.get_status_display(), "tone": STATUS_TONES[certificate.status]},
        "state": state_label(certificate, label),
        "sending": sending(certificate),
        "pdf_ready": bool(certificate.file_path),
        "evidence_count": getattr(certificate, "evidence_count", 0),
        # The row's "Mark handed over" (can_create_activity): an Approved hard copy whose PDF is ready.
        "can_hand_over": can_act and hard_copy and certificate.status == Status.APPROVED and bool(certificate.file_path),
    }


def _plural(count: int, one: str, many: str) -> str:
    return f"{count} {one if count == 1 else many}"


def approval_summary(certificates: list[Certificate]) -> str:
    """"26 certificates · 24 participants by email · 2 resource persons by hard copy"."""
    by_email = sum(1 for c in certificates if c.delivery_method == DeliveryMethod.EMAIL)
    by_hand = len(certificates) - by_email
    parts = [_plural(len(certificates), "certificate", "certificates")]
    if by_email:
        parts.append(f"{_plural(by_email, 'participant', 'participants')} by email")
    if by_hand:
        parts.append(f"{_plural(by_hand, 'resource person', 'resource persons')} by hard copy")
    return " · ".join(parts)


def approval_panel(member, activity: Activity, certificates: list[Certificate], labels: dict[int, str | None]) -> dict | None:
    """"Approve all ready certificates" (approvers only, ASSUMPTIONS 362): how many are ready (the confirm dialog's
    text: how many are approved, emailed and printed) and the reason it is closed (services.approval_closed_reason).
    None for members without can_approve_certificates (nothing is shown) or when nothing waits for approval."""
    if not has_permission(member, Permission.APPROVE_CERTIFICATES):
        return None
    waiting = [c for c in certificates if c.status == Status.GENERATED]
    if not waiting:
        return None
    ready = [c for c in waiting if labels.get(c.pk) is None]
    by_email = sum(1 for c in ready if c.delivery_method == DeliveryMethod.EMAIL)
    return {
        "ready": len(ready),
        "waiting": len(waiting),
        "not_ready": len(waiting) - len(ready),
        "by_email": by_email,
        "by_hand": len(ready) - by_email,
        "summary": approval_summary(ready) if ready else "",
        "closed_reason": services.approval_closed_reason(activity),
    }


def signatures_panel(member, activity: Activity) -> dict:
    """The Signatures section (ASSUMPTIONS 361): a VP and a Director slot with the signatory this activity's
    certificates use (services.activity_signatories), whether the image is there, and the upload form for members
    with can_approve_certificates (a thumbnail too: signature images are shown only to them and the Admin)."""
    can_edit = has_permission(member, Permission.APPROVE_CERTIFICATES)
    slots = []
    for position, signatory in services.activity_signatories(activity).items():
        present = services.signature_present(signatory)
        slots.append({
            "position": position,
            "label": services.SIGNATORY_LABELS[position],
            "signatory": signatory,
            "present": present,
            "image_url": reverse("certificates:signature_image", args=[signatory.pk]) if present and can_edit else "",
            "closed_reason": services.no_signatory_reason(position) if signatory is None else "",
            "upload_url": reverse("activities:certificates_signature", args=[activity.pk, position]),
        })
    return {"slots": slots, "can_edit": can_edit, "max_mb": settings.CERTIFICATE_IMAGE_MAX_MB}


def preview_items(certificates: list[Certificate], printed: dict[int, str], labels: dict[int, str | None],
                  can_approve: bool = False) -> list[dict]:
    """The large preview with Previous / Next: exactly the list below it, in the same order (ASSUMPTIONS 362).
    Each item: the printed name (and "registered as"), the readiness (services.readiness: the Approve button is
    enabled only when ready; approvers only), the URLs with ?v= (services.preview_version: a changed certificate is
    never shown from the browser's cache) and the Issued note."""
    items = []
    for c in certificates:
        shown, registered_as = names(c, printed)
        evaluated = c.registration_id in printed
        version = services.preview_version(c, evaluated)
        waiting = c.status == Status.GENERATED
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
            "issued_note": ISSUED_NOTES[c.delivery_method] if services.shows_final_copy(c) else "",
            "can_approve": can_approve and waiting,
            "ready": waiting and labels.get(c.pk) is None,
            "readiness": labels.get(c.pk) or "" if waiting else "",
            "approve_url": reverse("activities:certificates_approve", args=[c.activity_id, c.pk]) if can_approve and waiting else "",
        })
    return items


def preview_start(items: list[dict], raw: str | None) -> int:
    """Where the preview starts: the certificate named by ?preview=<id> (after an approval), else the first row."""
    if raw and raw.isdigit():
        for index, item in enumerate(items):
            if item["pk"] == int(raw):
                return index
    return 0


def next_to_approve(activity: Activity, current: Certificate) -> int | None:
    """After an approval: the next certificate in the list (number order) that is waiting for approval, a ready one
    first; from the top when none follows. None: nothing waits (the preview starts at the first row)."""
    waiting = list(services._waiting(activity))
    labels = services.readiness_map(waiting)
    after = [c for c in waiting if c.certificate_number > current.certificate_number]
    before = [c for c in waiting if c.certificate_number < current.certificate_number]
    for group in (after, before):
        ready = [c for c in group if labels[c.pk] is None]
        if ready:
            return ready[0].pk
    rest = after or before
    return rest[0].pk if rest else None


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
        or (c.status == Status.ISSUED and c.delivery_method == DeliveryMethod.EMAIL and not c.sent_at and not c.send_failed_at)
        for c in certificates
    )
    if not waiting:
        return False
    from accounts.system_status import OK, qcluster_status

    return qcluster_status().tone != OK


# --- The activity's Certificates tab -----------------------------------------------------------------------------


def certificates_tab(request: HttpRequest, activity: Activity, problems: list[str] | None = None) -> dict:
    """The tab (ASSUMPTIONS 362): counts per status, why certificates are not created yet (generation_closed_reason;
    they are created by the System), "Approve all ready certificates" (approvers only), Signatures, the preview with
    Approve, and the list (Preview, Mark handed over, Resend, PDF, Evidence). `problems`: a refused row action's list
    (kept in the session for this one view)."""
    certificates = list(_with_evidence_count(
        Certificate.objects.filter(activity=activity)
        .select_related("activity", "registration", "resource_person", "vp_signatory", "director_signatory")
    ).order_by("certificate_number"))
    member = request.user
    can_act = has_permission(member, Permission.CREATE_ACTIVITY)
    can_approve = has_permission(member, Permission.APPROVE_CERTIFICATES)
    labels = services.readiness_map(certificates)
    printed = printed_names(certificates)
    previews = preview_items(certificates, printed, labels, can_approve)
    start = preview_start(previews, request.GET.get("preview"))
    kept = request.session.pop(PROBLEMS_KEY.format(activity.pk), None) if hasattr(request, "session") else None
    return {
        "rows": [certificate_row(c, printed, labels.get(c.pk), can_act) for c in certificates],
        "counts": status_counts(services.certificate_status_counts({"activity": activity.pk})),
        "can_act": can_act,
        "can_approve": can_approve,
        "generation_reason": services.generation_closed_reason(activity),
        "approval": approval_panel(member, activity, certificates, labels),
        "signatures": signatures_panel(member, activity),
        "previews": previews,
        "preview_start": start,
        "shown": previews[start] if previews else None,  # the certificate the preview card shows first
        "can_apply_design": can_act and any(c.status != Status.ISSUED for c in certificates),
        "worker_stopped": worker_notice(certificates),
        "emails_printed": prints_emails() and any(c.delivery_method == DeliveryMethod.EMAIL for c in certificates),
        "problems": problems or kept or [],
        "back": tab_url(activity.pk),
    }


# --- The preview page (without JavaScript) --------------------------------------------------------------------


def preview(certificate: Certificate, member=None) -> dict:
    """The preview page: the PDF (certificates:preview in an <iframe>, ?v=), Download PDF, the printed name and
    whether it is final, the Issued note, and Approve for approvers (enabled only when ready)."""
    printed = printed_names([certificate])
    labels = services.readiness_map([certificate])
    can_approve = member is not None and has_permission(member, Permission.APPROVE_CERTIFICATES)
    item = preview_items([certificate], printed, labels, can_approve)[0]
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


@dataclass
class CertificateFilters:
    """The Certificates page's filters. A value that cannot be read is ignored with a note (as the other lists)."""

    activity: int | None = None
    status: str = ""
    recipient_type: str = ""
    delivery_method: str = ""
    page: str = "1"
    ignored: list[str] = field(default_factory=list)

    def for_service(self) -> dict:
        return {"activity": self.activity, "status": self.status, "recipient_type": self.recipient_type,
                "delivery_method": self.delivery_method}

    def query(self, **changes) -> str:
        values = {**self.for_service(), **changes}
        return urlencode({key: value for key, value in values.items() if value not in (None, "")})

    @property
    def base_query(self) -> str:
        """The other filters, without the status (the status count links add their own)."""
        return self.query(status="")

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
    return CertificateFilters(activity=activity, page=query.get("page", "1"), ignored=ignored, **values)


def certificate_list(filters: CertificateFilters) -> dict:
    result = services.list_certificates(filters.for_service(), page=filters.page)
    ids = [certificate.pk for certificate in result.items]
    counted = {c.pk: c.evidence_count for c in _with_evidence_count(Certificate.objects.filter(pk__in=ids))}
    labels = services.readiness_map(result.items)
    printed = printed_names(result.items)
    rows = []
    for certificate in result.items:
        certificate.evidence_count = counted.get(certificate.pk, 0)
        rows.append(certificate_row(certificate, printed, labels.get(certificate.pk)))
    without_status = {**filters.for_service(), "status": ""}
    return {
        "result": result,
        "rows": rows,
        "counts": status_counts(services.certificate_status_counts(without_status)),
    }


def filter_choices() -> dict:
    return {
        "activities": Activity.objects.filter(certificates__isnull=False).distinct().order_by("-end_date", "title").only("pk", "title"),
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
