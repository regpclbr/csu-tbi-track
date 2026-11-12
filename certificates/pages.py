"""Display helpers for the Certificates pages (phase F4): the activity's Certificates tab, the Certificates page and the
evidence modal, the preview dialog. Display only: every rule is in certificates.services (who may generate, approve or
act, the status moves, the all-or-nothing bulk actions, the missing signature reason). This module turns their results
into what the templates show (ASSUMPTIONS 357-358).
"""

from dataclasses import dataclass, field
from urllib.parse import urlencode

from django.db.models import Count, Q
from django.http import HttpRequest, QueryDict
from django.conf import settings
from django.urls import reverse

from accounts.permissions import Permission, has_permission
from activities.models import Activity
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

# The bulk actions of the Certificates tab: (value posted, button label, icon, permission). Each calls one service
# function (certificates.page_views.BULK_ACTIONS), which checks every selected certificate and changes all or nothing.
# A button is shown only to members with its permission; the view and the service check it again.
BULK_BUTTONS = [
    ("approve", "Approve selected", "circle-check", Permission.APPROVE_CERTIFICATES),
    ("regenerate", "Regenerate", "refresh-cw", Permission.CREATE_ACTIVITY),
    ("hand_over", "Mark handed over", "award", Permission.CREATE_ACTIVITY),
    ("resend", "Resend email", "send", Permission.CREATE_ACTIVITY),
]


def tab_url(activity_id: int) -> str:
    return f"{reverse('activities:detail', args=[activity_id])}?tab=certificates"


def recipient(certificate: Certificate) -> str:
    """Who the certificate is for: the participant's registration name or the resource person's name. (The printed
    name of a participant comes from the evaluation: services.recipient_name.)"""
    if certificate.recipient_type == RecipientType.RESOURCE_PERSON:
        return certificate.resource_person.name
    return certificate.registration.name


def sending(certificate: Certificate) -> dict:
    """The "Sent" column: label and tone. Hard copy; sent (date); Send failed (Resend); waiting (why)."""
    if certificate.delivery_method == DeliveryMethod.HARD_COPY:
        return {"key": "hard_copy", "label": "Hard copy", "tone": "neutral"}
    if certificate.sent_at:
        return {"key": "sent", "label": "Sent", "tone": "success"}
    if certificate.send_failed_at:
        return {"key": "failed", "label": "Send failed", "tone": "danger"}
    if certificate.status == Status.ISSUED:
        return {"key": "queued", "label": "Waiting for qcluster", "tone": "neutral"}
    if certificate.status == Status.APPROVED:
        return {"key": "evaluation", "label": "After the evaluation", "tone": "neutral"}
    return {"key": "approval", "label": "After approval", "tone": "neutral"}


def certificate_row(certificate: Certificate) -> dict:
    return {
        "certificate": certificate,
        "recipient": recipient(certificate),
        "kind": KIND_LABELS[certificate.recipient_type],
        "delivery": DELIVERY_LABELS[certificate.delivery_method],
        "status": {"label": certificate.get_status_display(), "tone": STATUS_TONES[certificate.status]},
        "sending": sending(certificate),
        "pdf_ready": bool(certificate.file_path),
        "evidence_count": getattr(certificate, "evidence_count", 0),
    }


def _plural(count: int, one: str, many: str) -> str:
    return f"{count} {one if count == 1 else many}"


def approval_summary(certificates: list[Certificate]) -> str:
    """"26 certificates · 24 participants by email · 2 resource persons by hard copy" (the waiting ones)."""
    by_email = sum(1 for c in certificates if c.delivery_method == DeliveryMethod.EMAIL)
    by_hand = len(certificates) - by_email
    parts = [_plural(len(certificates), "certificate", "certificates")]
    if by_email:
        parts.append(f"{_plural(by_email, 'participant', 'participants')} by email")
    if by_hand:
        parts.append(f"{_plural(by_hand, 'resource person', 'resource persons')} by hard copy")
    return " · ".join(parts)


def approval_panel(member, activity: Activity, certificates: list[Certificate]) -> dict | None:
    """The per-activity approval card: the summary of the certificates waiting for approval and "Approve all for this
    activity" (disabled with the service's reason, services.approval_closed_reason, e.g. "Add the VP's signature
    first."). The reason is shown to everyone; the button only to members with can_approve_certificates. None when
    nothing waits."""
    waiting = [c for c in certificates if c.status == Status.GENERATED]
    if not waiting:
        return None
    return {
        "count": len(waiting),
        "summary": approval_summary(waiting),
        "can_approve": has_permission(member, Permission.APPROVE_CERTIFICATES),
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


def preview_items(certificates: list[Certificate]) -> list[dict]:
    """The large preview with Previous / Next (ASSUMPTIONS 361): every certificate of the activity, in number order.
    name_final=False: a participant who has not evaluated yet (the preview shows the registration name; the page
    says "The final name comes from the evaluation.")."""
    registration_ids = [c.registration_id for c in certificates if c.registration_id]
    evaluated = set(EvaluationSubmission.objects.filter(registration_id__in=registration_ids)
                    .values_list("registration_id", flat=True))
    return [
        {
            "pk": c.pk,
            "url": reverse("certificates:preview", args=[c.pk]),
            "download_url": reverse("certificates:download", args=[c.pk]),
            "number": c.certificate_number,
            "recipient": recipient(c),
            "kind": KIND_LABELS[c.recipient_type],
            "status": c.get_status_display(),
            "name_final": c.recipient_type == RecipientType.RESOURCE_PERSON or c.registration_id in evaluated,
        }
        for c in certificates
    ]


def _with_evidence_count(queryset):
    return queryset.annotate(evidence_count=Count("documents", filter=Q(documents__related_certificate__isnull=False)))


def status_counts(counts: dict[str, int]) -> list[dict]:
    """services.certificate_status_counts() in status order, with labels and badge tones."""
    return [
        {"value": value, "label": label, "tone": STATUS_TONES[value], "count": counts.get(value, 0)}
        for value, label in Status.choices
    ]


# --- The activity's Certificates tab -----------------------------------------------------------------------------


def certificates_tab(request: HttpRequest, activity: Activity, problems: list[str] | None = None) -> dict:
    """The approval panel (approval_panel), the table (every certificate of the activity), counts per status,
    Generate (with the reason it is not possible now, services.generation_closed_reason) and the bulk actions this
    member may use. `problems`: the refusals of a bulk action that changed nothing, one per certificate."""
    certificates = list(_with_evidence_count(
        Certificate.objects.filter(activity=activity).select_related("registration", "resource_person")
    ).order_by("certificate_number"))
    member = request.user
    can_act = has_permission(member, Permission.CREATE_ACTIVITY)
    buttons = [(value, label, icon) for value, label, icon, permission in BULK_BUTTONS if has_permission(member, permission)]
    return {
        "rows": [certificate_row(c) for c in certificates],
        "counts": status_counts(services.certificate_status_counts({"activity": activity.pk})),
        "can_act": can_act,
        "can_select": bool(buttons),
        "generation_reason": services.generation_closed_reason(activity) if can_act else None,
        "bulk_buttons": buttons,
        "approval": approval_panel(member, activity, certificates),
        "signatures": signatures_panel(member, activity),
        "previews": preview_items(certificates),
        "emails_printed": prints_emails() and any(c.delivery_method == DeliveryMethod.EMAIL for c in certificates),
        "problems": problems or [],
        "back": tab_url(activity.pk),
    }


# --- The preview dialog -----------------------------------------------------------------------------------------


def preview(certificate: Certificate) -> dict:
    """The preview dialog / page: the PDF (certificates:preview in an <iframe>), Download PDF, and whether the name is
    final (a participant's printed name comes from the evaluation)."""
    name, final = services.preview_name(certificate)
    return {
        "certificate": certificate,
        "recipient": recipient(certificate),
        "kind": KIND_LABELS[certificate.recipient_type],
        "status": {"label": certificate.get_status_display(), "tone": STATUS_TONES[certificate.status]},
        "name": name,
        "name_final": final,
        "marked": services.preview_marked(certificate),
        "file_url": reverse("certificates:preview", args=[certificate.pk]),
        "download_url": reverse("certificates:download", args=[certificate.pk]),
        "back": tab_url(certificate.activity_id),
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
    rows = []
    for certificate in result.items:
        certificate.evidence_count = counted.get(certificate.pk, 0)
        rows.append(certificate_row(certificate))
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
