"""What needs attention on an activity (ASSUMPTIONS 347): the amber box on the activity page and the warning count
on the Activities list. Informational items (AttentionItem.informational, e.g. "N issued certificates have an old
signature", ASSUMPTIONS 364) are shown in the box but never counted (warning_count).

No new rules here: every item reuses an existing one (document checklist and reminder rules, the review states,
the automatic status rules, the evaluation window, the certificate statuses). The facts for many activities are
loaded in a fixed number of queries (_load_facts), and ONE function turns facts into items (_items), so
activity_attention() and attention_counts() always agree (a parity test checks it).
"""

from dataclasses import dataclass, field
from datetime import datetime, timedelta

from django.conf import settings
from django.db.models import Count, Exists, OuterRef, Q
from django.utils import timezone

from certificates.models import ActivitySignatory, Certificate
from documents import services as document_services
from documents.models import Document, DocumentType
from notifications.models import Notification
from registrations import services as registration_services
from registrations.models import AttendanceDay, FormTemplate, FormType, Registration

from .formatting import format_date
from .models import Activity, ActivityStatus
from .services import activity_start_end

DRAFT, SCHEDULED, ONGOING, CONDUCTED = (
    ActivityStatus.DRAFT, ActivityStatus.SCHEDULED, ActivityStatus.ONGOING, ActivityStatus.CONDUCTED,
)

ONGOING_NO_ATTENDANCE = "Activity is ongoing: mark attendance on the Registrations tab."
ENDED_NO_ATTENDANCE = "Activity ended but no attendance recorded. Mark attendance or change the status."
NO_REGISTRATIONS = "No one registered. Mark as Conducted or change the status."
ATTENDANCE_AFTER_EVALUATION = "Attendance was marked after the evaluation closed. These attendees can't evaluate."
SET_TIMES = "Set the start and end time."
CHOOSE_SIGNATORIES = "Choose who signs the certificates ({sides})."  # ASSUMPTIONS 365, 366
INACTIVE_SIGNATORY = "A signatory on this activity is inactive."  # ASSUMPTIONS 367
NEEDS_CERTIFICATE_APPROVAL = "Certificates need approval."  # ASSUMPTIONS 369
MIX_OUTSIDE = "Participant mix is outside NAVIGATU's guideline: {problems}."  # ASSUMPTIONS 424
PAYMENT_LABEL = "payment documents for the Supply Office"  # ASSUMPTIONS 425
FORM_NOT_READY = "Registration form not marked as ready: registration stays closed. Check it and click “Open registration”."  # 409


@dataclass(frozen=True)
class AttentionItem:
    """One thing to do. `tab`: the activity page tab it links to ("edit" = the edit page). `kind`: what it is
    about, for the Activities list's "Needs follow-up" cards ("draft", "attendance"; "" for the others).
    `informational`: shown in the box, never counted (one image replacement must not light up every past activity)."""

    text: str
    tab: str
    kind: str = ""
    informational: bool = False


@dataclass
class _Facts:
    activity: Activity
    required: list = field(default_factory=list)  # documents.services.ChecklistItem
    waiting_review: int = 0
    returned: int = 0
    forms: set[str] = field(default_factory=set)
    registration_form_ready_at: object = None  # FORM_TEMPLATE.ready_at of the registration form (ASSUMPTIONS 409)
    registered: int = 0
    attended: int = 0
    attended_after_evaluation: int = 0
    links_failed: int = 0
    links_not_sent: int = 0
    signature_missing: str = ""  # certificates.services.missing_signature_reason for the waiting certificates
    certificates: dict[str, int] = field(default_factory=dict)
    signatory_slots: dict[str, bool] = field(default_factory=dict)  # slot -> the person is active (ACTIVITY_SIGNATORY)
    participant_mix: dict = field(default_factory=dict)  # reports.metrics.participant_mix (ASSUMPTIONS 424)


def _plural(count: int, one: str, many: str) -> str:
    return f"{count} {one if count == 1 else many}"


# Attendees who still have to evaluate = registrations.services.evaluation_link_recipients().
_WAITING_TO_EVALUATE = Q(registrations__attendance_confirmed=True, registrations__evaluation_submission__isnull=True)


def _load_facts(activities: list[Activity], now: datetime) -> dict[int, _Facts]:
    """Everything the items need, for any number of activities, in a fixed number of queries (+ two for the participant
    mix, ASSUMPTIONS 424)."""
    facts = {activity.pk: _Facts(activity) for activity in activities}
    if not facts:
        return facts
    ids = list(facts)
    today = timezone.localdate(now)

    from reports.metrics import participant_mix  # reports.metrics imports documents.services, like this module

    for activity_id, mix in participant_mix(ids).items():  # two queries (ASSUMPTIONS 424)
        facts[activity_id].participant_mix = mix

    types = list(DocumentType.objects.filter(is_active=True, is_required=True).order_by("name"))
    documents = document_services.documents_by_activity(ids)
    for activity_id, fact in facts.items():
        own = documents.get(activity_id, [])
        fact.required = document_services.required_items(fact.activity, own, types, today)
        fact.waiting_review = sum(1 for d in own if d.review_state == Document.WAITING_FOR_REVIEW)
        fact.returned = sum(1 for d in own if d.review_state == Document.RETURNED)

    for activity_id, form_type, ready_at in FormTemplate.objects.filter(activity_id__in=ids).values_list(
            "activity_id", "form_type", "ready_at"):
        facts[activity_id].forms.add(form_type)
        if form_type == FormType.REGISTRATION:
            facts[activity_id].registration_form_ready_at = ready_at

    registrations = (
        Activity.objects.filter(pk__in=ids)
        .annotate(
            # Who counts as registered: reports.metrics.COUNTED (ASSUMPTIONS 440: "Will not attend" only when they came).
            registered=Count("registrations", filter=Q(registrations__cancelled_at__isnull=True)
                             | Q(registrations__attendance_confirmed=True)),
            attended=Count("registrations", filter=Q(registrations__attendance_confirmed=True)),
            # Marked attended after the evaluation deadline and not evaluated: their link can never work. Since
            # ASSUMPTIONS 434: no attendance day of theirs was recorded on or before the deadline.
            attended_after_evaluation=Count("registrations", filter=_WAITING_TO_EVALUATE & Q(
                evaluation_deadline__isnull=False) & ~Q(Exists(AttendanceDay.objects.filter(
                    registration=OuterRef("registrations"), created_at__lte=OuterRef("evaluation_deadline"))))),
            links_failed=Count("registrations", filter=_WAITING_TO_EVALUATE & Q(registrations__evaluation_link_failed_at__isnull=False)),
            links_not_sent=Count("registrations", filter=_WAITING_TO_EVALUATE & Q(
                registrations__evaluation_link_sent_at__isnull=True, registrations__evaluation_link_failed_at__isnull=True,
            )),
        )
        .values_list("pk", "registered", "attended", "attended_after_evaluation", "links_failed", "links_not_sent")
    )
    for activity_id, registered, attended, late, failed, not_sent in registrations:
        fact = facts[activity_id]
        fact.registered, fact.attended, fact.attended_after_evaluation = registered, attended, late
        fact.links_failed, fact.links_not_sent = failed, not_sent

    from certificates import services as certificate_services

    Status = Certificate.Status
    unsent = Q(status=Status.ISSUED, delivery_method=Certificate.DeliveryMethod.EMAIL, sent_at__isnull=True)
    certificates = (
        Certificate.objects.filter(activity_id__in=ids).values("activity_id").order_by()
        .annotate(
            total=Count("pk"),
            issued=Count("pk", filter=Q(status=Status.ISSUED)),
            # Waiting for approval while the activity is not approved (ASSUMPTIONS 369); the join is one to one.
            needs_approval=Count("pk", filter=certificate_services.needs_approval_q()),
            to_hand_over=Count("pk", filter=Q(status=Status.APPROVED, recipient_type=Certificate.RecipientType.RESOURCE_PERSON)),
            to_reprint=Count("pk", filter=certificate_services.reprint_q()),
            send_failed=Count("pk", filter=unsent & Q(send_failed_at__isnull=False)),
            unsent=Count("pk", filter=unsent & Q(send_failed_at__isnull=True)),
            outdated=Count("pk", filter=certificate_services.outdated_q()),  # ASSUMPTIONS 364 (informational)
        )
    )
    for row in certificates:
        facts[row.pop("activity_id")].certificates = row

    # Who signs (ASSUMPTIONS 365-367): the saved slots and whether each person is active.
    for activity_id, slot, active in ActivitySignatory.objects.filter(activity_id__in=ids).values_list(
            "activity_id", "slot", "signatory__is_active"):
        facts[activity_id].signatory_slots[slot] = active

    # Signatures of the certificates waiting for approval (ASSUMPTIONS 361, 363): one query (DISTINCT ON the signatory
    # pair per activity), then certificates.services' own check (it reads the files, no queries).
    pairs = (
        Certificate.objects.filter(activity_id__in=ids, status=Status.GENERATED)
        .select_related("left_signatory", "right_signatory")
        .order_by("activity_id", "left_signatory_id", "right_signatory_id")
        .distinct("activity_id", "left_signatory_id", "right_signatory_id")
    )
    signatories: dict[int, list] = {}
    for certificate in pairs:
        signatories.setdefault(certificate.activity_id, []).extend([certificate.left_signatory, certificate.right_signatory])
    for activity_id, people in signatories.items():
        facts[activity_id].signature_missing = certificate_services.missing_signature_reason(people) or ""
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
        items.append(AttentionItem(f"Still a Draft but it {verb} on {format_date(activity.start_date)}. Change the status.", "overview", "draft"))

    # Attendance (the automatic status rules: Ongoing -> Conducted needs attendance after the end). Nobody registered
    # and ended: there is no attendance to mark, so staff conduct it (or change the status) by hand.
    if status == ONGOING and not fact.attended:
        if now >= end and not fact.registered:
            items.append(AttentionItem(NO_REGISTRATIONS, "overview", "attendance"))
        else:
            items.append(AttentionItem(ENDED_NO_ATTENDANCE if now >= end else ONGOING_NO_ATTENDANCE, "registrations", "attendance"))
    if fact.attended_after_evaluation:
        items.append(AttentionItem(ATTENDANCE_AFTER_EVALUATION, "registrations"))
    # Participant mix (ASSUMPTIONS 424): metrics' check only; a warning while registration / attendance can still change,
    # informational (never counted) once Conducted. Registration is never blocked.
    check = fact.participant_mix.get("check")
    if check is not None and not check["within"]:
        items.append(AttentionItem(MIX_OUTSIDE.format(problems="; ".join(check["problems"])), "overview",
                                   informational=status == CONDUCTED))

    # Forms
    opens, _closes = registration_services.evaluation_window(activity)
    if status in (DRAFT, SCHEDULED) and FormType.REGISTRATION not in fact.forms:
        items.append(AttentionItem("Registration form not built yet.", "forms"))
    # ASSUMPTIONS 409 (registrations.services.registration_form_needs_ready): built but not marked ready.
    elif (status in (DRAFT, SCHEDULED) and activity.creation_documents_required
          and fact.registration_form_ready_at is None):
        items.append(AttentionItem(FORM_NOT_READY, "forms"))
    if FormType.EVALUATION not in fact.forms and not registration_services.evaluation_has_closed(activity, now):
        items.append(AttentionItem("Evaluation form not built yet.", "forms"))

    # Documents: the reminders' rule (overdue; required, not uploaded and due soon) and the review states.
    # Payment documents (ASSUMPTIONS 425) only count once Conducted (reminder_kind is None before) and get their own line.
    kinds = [(item.document_type.name, item.is_payment, document_services.reminder_kind(item, today)) for item in fact.required]
    for payment, label in ((False, "required documents"), (True, PAYMENT_LABEL)):
        overdue = [name for name, is_payment, kind in kinds if is_payment == payment and kind == Notification.DOCUMENT_OVERDUE]
        due_soon = [name for name, is_payment, kind in kinds if is_payment == payment and kind == Notification.DOCUMENT_DUE_SOON]
        if overdue:
            items.append(AttentionItem(f"Overdue {label}: {', '.join(overdue)}.", "documents"))
        if due_soon:
            items.append(AttentionItem(f"{label[0].upper()}{label[1:]} due soon and not uploaded: {', '.join(due_soon)}.", "documents"))
    if fact.waiting_review:
        items.append(AttentionItem(f"{_plural(fact.waiting_review, 'document', 'documents')} waiting for approval.", "documents"))
    if fact.returned:
        items.append(AttentionItem(f"{_plural(fact.returned, 'document', 'documents')} returned to the uploader.", "documents"))

    # Certificates
    certificates = fact.certificates
    if status == CONDUCTED and fact.attended and not certificates.get("total"):
        items.append(AttentionItem(
            f"Conducted with {_plural(fact.attended, 'attendee', 'attendees')} but no certificates yet: they are created "
            "automatically once the certificate design and signatories are set up.", "certificates"))
    # Who signs (ASSUMPTIONS 365-367): only while it still matters, i.e. no certificates yet or not all of them Issued.
    if not certificates.get("total") or certificates.get("issued", 0) < certificates["total"]:
        missing = [side for side in ("left", "right") if side not in fact.signatory_slots]
        if missing:
            items.append(AttentionItem(CHOOSE_SIGNATORIES.format(sides=" and ".join(missing)), "certificates"))
        if not all(fact.signatory_slots.values()):
            items.append(AttentionItem(INACTIVE_SIGNATORY, "certificates"))
    # Approval (ASSUMPTIONS 361, 369): one line. A missing signature first (it blocks the approval); otherwise the
    # activity needs certificate approval (one approval per activity).
    if fact.signature_missing:
        items.append(AttentionItem(fact.signature_missing, "certificates"))
    elif certificates.get("needs_approval"):
        items.append(AttentionItem(NEEDS_CERTIFICATE_APPROVAL, "certificates"))
    for key, text in (
        ("to_hand_over", "for resource persons approved but not handed over"),
        ("to_reprint", "for resource persons reissued: updated copy to print and hand over"),
        ("send_failed", "could not be emailed. Resend on the Certificates tab"),
        ("unsent", "issued but their email not sent yet"),
    ):
        if count := certificates.get(key):
            items.append(AttentionItem(f"{_plural(count, 'certificate', 'certificates')} {text}.", "certificates"))
    # Issued with an old signature (ASSUMPTIONS 364): informational, never counted on the list or the dashboard.
    if count := certificates.get("outdated"):
        items.append(AttentionItem(
            f"{_plural(count, 'issued certificate has', 'issued certificates have')} an old signature.", "certificates",
            informational=True))

    # Evaluation links: only while they can still be sent (Conducted, a form, the window open).
    if (status == CONDUCTED and FormType.EVALUATION in fact.forms and opens <= now
            and not registration_services.evaluation_has_closed(activity, now)):
        if fact.links_failed:
            items.append(AttentionItem(f"{_plural(fact.links_failed, 'evaluation link email', 'evaluation link emails')} failed. Resend on the Evaluation tab.", "evaluation"))
        if fact.links_not_sent:
            items.append(AttentionItem(f"{_plural(fact.links_not_sent, 'attendee has', 'attendees have')} not been sent an evaluation link yet.", "evaluation"))
    return items


def activity_attention(activity: Activity, now: datetime | None = None) -> list[AttentionItem]:
    """Everything that needs attention on one activity (the amber box below the title). Empty = "All set"."""
    now = now or timezone.now()
    return _items(_load_facts([activity], now)[activity.pk], now)


def attention_items(activities: list[Activity], now: datetime | None = None) -> dict[int, list[AttentionItem]]:
    """Every activity's items (the Activities list: the count, and the follow-up cards' note), in a fixed number
    of queries."""
    now = now or timezone.now()
    return {activity_id: _items(fact, now) for activity_id, fact in _load_facts(list(activities), now).items()}


def warning_count(items: list[AttentionItem]) -> int:
    """The number shown on the Activities list: informational items are not counted (ASSUMPTIONS 364)."""
    return sum(1 for item in items if not item.informational)


def attention_counts(activities: list[Activity], now: datetime | None = None) -> dict[int, int]:
    """How many items each activity has (the Activities list; informational ones not counted), in a fixed number of
    queries."""
    return {activity_id: warning_count(items) for activity_id, items in attention_items(activities, now).items()}
