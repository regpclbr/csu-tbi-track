"""What needs attention on an activity (ASSUMPTIONS 347): the amber box on the activity page and the warning count
on the Activities list.

No new rules here: every item reuses an existing one (document checklist and reminder rules, the review states,
the automatic status rules, the evaluation window, the certificate statuses). The facts for many activities are
loaded in a fixed number of queries (_load_facts), and ONE function turns facts into items (_items), so
activity_attention() and attention_counts() always agree (a parity test checks it).
"""

from dataclasses import dataclass, field
from datetime import datetime, timedelta

from django.conf import settings
from django.db.models import Count, Q
from django.utils import timezone

from certificates.models import Certificate
from documents import services as document_services
from documents.models import Document, DocumentType
from notifications.models import Notification
from registrations import services as registration_services
from registrations.models import FormTemplate, FormType, Registration

from .formatting import format_date
from .models import Activity, ActivityStatus
from .services import activity_start_end

DRAFT, SCHEDULED, ONGOING, CONDUCTED = (
    ActivityStatus.DRAFT, ActivityStatus.SCHEDULED, ActivityStatus.ONGOING, ActivityStatus.CONDUCTED,
)

ONGOING_NO_ATTENDANCE = "Activity is ongoing: mark attendance on the Registrations tab."
ENDED_NO_ATTENDANCE = "Activity ended but no attendance recorded. Mark attendance or change the status."
SET_TIMES = "Set the start and end time."


@dataclass(frozen=True)
class AttentionItem:
    """One thing to do. `tab`: the activity page tab it links to ("edit" = the edit page)."""

    text: str
    tab: str


@dataclass
class _Facts:
    activity: Activity
    required: list = field(default_factory=list)  # documents.services.ChecklistItem
    waiting_review: int = 0
    returned: int = 0
    forms: set[str] = field(default_factory=set)
    attended: int = 0
    links_failed: int = 0
    links_not_sent: int = 0
    certificates: dict[str, int] = field(default_factory=dict)


def _plural(count: int, one: str, many: str) -> str:
    return f"{count} {one if count == 1 else many}"


# Attendees who still have to evaluate = registrations.services.evaluation_link_recipients().
_WAITING_TO_EVALUATE = Q(registrations__attendance_confirmed=True, registrations__evaluation_submission__isnull=True)


def _load_facts(activities: list[Activity], now: datetime) -> dict[int, _Facts]:
    """Everything the items need, for any number of activities, in five queries."""
    facts = {activity.pk: _Facts(activity) for activity in activities}
    if not facts:
        return facts
    ids = list(facts)
    today = timezone.localdate(now)

    types = list(DocumentType.objects.filter(is_active=True, is_required=True).order_by("name"))
    documents = document_services.documents_by_activity(ids)
    for activity_id, fact in facts.items():
        own = documents.get(activity_id, [])
        fact.required = document_services.required_items(fact.activity, own, types, today)
        fact.waiting_review = sum(1 for d in own if d.review_state == Document.WAITING_FOR_REVIEW)
        fact.returned = sum(1 for d in own if d.review_state == Document.RETURNED)

    for activity_id, form_type in FormTemplate.objects.filter(activity_id__in=ids).values_list("activity_id", "form_type"):
        facts[activity_id].forms.add(form_type)

    registrations = (
        Activity.objects.filter(pk__in=ids)
        .annotate(
            attended=Count("registrations", filter=Q(registrations__attendance_confirmed=True)),
            links_failed=Count("registrations", filter=_WAITING_TO_EVALUATE & Q(registrations__evaluation_link_failed_at__isnull=False)),
            links_not_sent=Count("registrations", filter=_WAITING_TO_EVALUATE & Q(
                registrations__evaluation_link_sent_at__isnull=True, registrations__evaluation_link_failed_at__isnull=True,
            )),
        )
        .values_list("pk", "attended", "links_failed", "links_not_sent")
    )
    for activity_id, attended, failed, not_sent in registrations:
        facts[activity_id].attended, facts[activity_id].links_failed, facts[activity_id].links_not_sent = attended, failed, not_sent

    Status = Certificate.Status
    signed = Q(director_signed_at__isnull=False, vp_signed_at__isnull=False)
    certificates = (
        Certificate.objects.filter(activity_id__in=ids).values("activity_id").order_by()
        .annotate(
            total=Count("pk"),
            to_review=Count("pk", filter=Q(status=Status.GENERATED)),
            to_sign=Count("pk", filter=Q(status=Status.PENDING_SIGNOFF) & ~signed),
            to_approve=Count("pk", filter=Q(status=Status.PENDING_SIGNOFF) & signed),
            to_hand_over=Count("pk", filter=Q(status=Status.APPROVED, recipient_type=Certificate.RecipientType.RESOURCE_PERSON)),
            unsent=Count("pk", filter=Q(status=Status.ISSUED, delivery_method=Certificate.DeliveryMethod.EMAIL, sent_at__isnull=True)),
        )
    )
    for row in certificates:
        facts[row.pop("activity_id")].certificates = row
    return facts


def _items(fact: _Facts, now: datetime) -> list[AttentionItem]:
    """The one place that turns facts into items (both public functions use it)."""
    activity = fact.activity
    status = activity.status.name
    today = timezone.localdate(now)
    start, end = activity_start_end(activity)
    items: list[AttentionItem] = []

    if status != CONDUCTED and (activity.start_time is None or activity.end_time is None):
        items.append(AttentionItem(SET_TIMES, "edit"))
    if status == DRAFT and activity.start_date - today <= timedelta(days=settings.ATTENTION_DRAFT_DAYS_BEFORE):
        verb = "started" if activity.start_date < today else "starts"
        items.append(AttentionItem(f"Still a Draft but it {verb} on {format_date(activity.start_date)}. Change the status.", "overview"))

    # Attendance (the automatic status rules: Ongoing -> Conducted needs attendance after the end).
    if status == ONGOING and not fact.attended:
        items.append(AttentionItem(ENDED_NO_ATTENDANCE if now >= end else ONGOING_NO_ATTENDANCE, "registrations"))

    # Forms
    opens, closes = registration_services.evaluation_window(activity)
    if status in (DRAFT, SCHEDULED) and FormType.REGISTRATION not in fact.forms:
        items.append(AttentionItem("Registration form not built yet.", "forms"))
    if FormType.EVALUATION not in fact.forms and now <= closes:
        items.append(AttentionItem("Evaluation form not built yet.", "forms"))

    # Documents: the reminders' rule (overdue; required, not uploaded and due soon) and the review states.
    kinds = {item.document_type.name: document_services.reminder_kind(item, today) for item in fact.required}
    overdue = [name for name, kind in kinds.items() if kind == Notification.DOCUMENT_OVERDUE]
    due_soon = [name for name, kind in kinds.items() if kind == Notification.DOCUMENT_DUE_SOON]
    if overdue:
        items.append(AttentionItem(f"Overdue required documents: {', '.join(overdue)}.", "documents"))
    if due_soon:
        items.append(AttentionItem(f"Required documents due soon and not uploaded: {', '.join(due_soon)}.", "documents"))
    if fact.waiting_review:
        items.append(AttentionItem(f"{_plural(fact.waiting_review, 'document', 'documents')} waiting for approval.", "documents"))
    if fact.returned:
        items.append(AttentionItem(f"{_plural(fact.returned, 'document', 'documents')} returned to the uploader.", "documents"))

    # Certificates
    certificates = fact.certificates
    if status == CONDUCTED and fact.attended and not certificates.get("total"):
        items.append(AttentionItem(f"Conducted with {_plural(fact.attended, 'attendee', 'attendees')} but no certificates generated yet.", "certificates"))
    for key, text in (
        ("to_review", "waiting for review"),
        ("to_sign", "waiting for the Director's and VP's sign-off"),
        ("to_approve", "signed and waiting for approval"),
        ("to_hand_over", "for resource persons approved but not handed over"),
        ("unsent", "issued but their email not sent yet"),
    ):
        if count := certificates.get(key):
            items.append(AttentionItem(f"{_plural(count, 'certificate', 'certificates')} {text}.", "certificates"))

    # Evaluation links: only while they can still be sent (Conducted, a form, the window open).
    if status == CONDUCTED and FormType.EVALUATION in fact.forms and opens <= now <= closes:
        if fact.links_failed:
            items.append(AttentionItem(f"{_plural(fact.links_failed, 'evaluation link email', 'evaluation link emails')} failed. Resend on the Evaluation tab.", "evaluation"))
        if fact.links_not_sent:
            items.append(AttentionItem(f"{_plural(fact.links_not_sent, 'attendee has', 'attendees have')} not been sent an evaluation link yet.", "evaluation"))
    return items


def activity_attention(activity: Activity, now: datetime | None = None) -> list[AttentionItem]:
    """Everything that needs attention on one activity (the amber box below the title). Empty = "All set"."""
    now = now or timezone.now()
    return _items(_load_facts([activity], now)[activity.pk], now)


def attention_counts(activities: list[Activity], now: datetime | None = None) -> dict[int, int]:
    """How many items each activity has (the Activities list), in a fixed number of queries."""
    now = now or timezone.now()
    return {activity_id: len(_items(fact, now)) for activity_id, fact in _load_facts(list(activities), now).items()}
