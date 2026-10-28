"""Display helpers for the Certificates pages (phase F4): the activity's Certificates tab, the Certificates page and the
evidence modal. Display only: every rule is in certificates.services (who may generate or act, the status moves, the
all-or-nothing bulk actions, which design is needed). This module turns their results into what the templates show.
"""

from dataclasses import dataclass, field
from urllib.parse import urlencode

from django.db.models import Count, Q
from django.http import HttpRequest, QueryDict
from django.urls import reverse

from accounts.permissions import Permission, has_permission
from activities.models import Activity
from documents import pages as document_pages

from . import services
from .models import Certificate

Status = Certificate.Status
RecipientType = Certificate.RecipientType
DeliveryMethod = Certificate.DeliveryMethod

STATUS_TONES = {Status.GENERATED: "neutral", Status.PENDING_SIGNOFF: "warning", Status.APPROVED: "info", Status.ISSUED: "success"}
KIND_LABELS = {RecipientType.REGISTRATION: "Participation", RecipientType.RESOURCE_PERSON: "Appreciation"}
DELIVERY_LABELS = {DeliveryMethod.EMAIL: "Email", DeliveryMethod.HARD_COPY: "Hard copy"}

# The bulk actions of the Certificates tab: (value posted, button label, icon). Each calls one service function
# (certificates.page_views.BULK_ACTIONS), which checks every selected certificate and changes all or nothing.
BULK_BUTTONS = [
    ("review", "Review", "file-check"),
    ("sign_director", "Record Director sign-off", "pencil"),
    ("sign_vp", "Record VP sign-off", "pencil"),
    ("approve", "Approve", "circle-check"),
    ("reject", "Reject", "undo-2"),
    ("hand_over", "Mark handed over", "award"),
]


def tab_url(activity_id: int) -> str:
    return f"{reverse('activities:detail', args=[activity_id])}?tab=certificates"


def recipient(certificate: Certificate) -> str:
    """Who the certificate is for: the participant's registration name or the resource person's name. (The printed
    name of a participant comes from the evaluation: services.recipient_name.)"""
    if certificate.recipient_type == RecipientType.RESOURCE_PERSON:
        return certificate.resource_person.name
    return certificate.registration.name


def certificate_row(certificate: Certificate) -> dict:
    return {
        "certificate": certificate,
        "recipient": recipient(certificate),
        "kind": KIND_LABELS[certificate.recipient_type],
        "delivery": DELIVERY_LABELS[certificate.delivery_method],
        "status": {"label": certificate.get_status_display(), "tone": STATUS_TONES[certificate.status]},
        "pdf_ready": bool(certificate.file_path),
        "evidence_count": getattr(certificate, "evidence_count", 0),
    }


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
    """The table (every certificate of the activity), counts per status, Generate (with the reason it is not possible
    now, services.generation_closed_reason) and the bulk actions (can_create_activity only). `problems`: the refusals
    of a bulk action that changed nothing, one per certificate."""
    certificates = _with_evidence_count(
        Certificate.objects.filter(activity=activity).select_related("registration", "resource_person")
    ).order_by("certificate_number")
    can_act = has_permission(request.user, Permission.CREATE_ACTIVITY)
    return {
        "rows": [certificate_row(c) for c in certificates],
        "counts": status_counts(services.certificate_status_counts({"activity": activity.pk})),
        "can_act": can_act,
        "generation_reason": services.generation_closed_reason(activity) if can_act else None,
        "bulk_buttons": BULK_BUTTONS,
        "problems": problems or [],
        "back": tab_url(activity.pk),
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
