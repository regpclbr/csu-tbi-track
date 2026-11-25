"""The "For your approval" inbox (ASSUMPTIONS 437): everything waiting for one member, each kind from its existing rule.

- Certificate approvals (can_approve_certificates): activities whose certificates wait for approval
  (certificates.services.needs_approval_q, the S5 rule) or that were prepared before Conducted and not approved yet
  (item 420, approval_state's "prepared").
- Submissions (can_approve_documents): waiting, never the member's own (approvals.services.waiting_for).
- Documents of types with needs_review (can_approve_documents): documents.services.review_queue (usually empty since
  item 432).
- Overdue assignments (can_assign_work): open WORK_ASSIGNMENTs this member gave whose due date has passed
  (approvals.assignments.overdue_assigned_by). Work assigned TO a member is in their "Assigned to me" list.
One query per kind, whatever the number of rows; the counts are one COUNT per kind.
"""

from dataclasses import dataclass, field
from datetime import date, datetime, time

from django.db.models import Count, Exists, IntegerField, OuterRef, Q, QuerySet, Subquery
from django.db.models.functions import Coalesce
from django.urls import reverse
from django.utils import timezone

from accounts.models import TBIMember
from accounts.permissions import Permission, has_permission
from activities.models import Activity, ActivityStatus
from certificates.models import Certificate
from documents.services import review_queue

from . import services

CERTIFICATES, SUBMISSION, DOCUMENT, ASSIGNMENT = "certificates", "submission", "document", "assignment"
KIND_LABELS = {CERTIFICATES: "Certificate approval", SUBMISSION: "Submitted to you", DOCUMENT: "Document review",
               ASSIGNMENT: "Overdue assignment"}
ITEM_LIMIT = 200  # per kind: an inbox is never this long; the page says so if it is


@dataclass
class InboxItem:
    kind: str
    title: str
    url: str
    since: datetime
    who: str = ""
    activity_id: int | None = None
    activity_title: str = ""
    detail: str = ""
    overdue: bool = False
    extra: dict = field(default_factory=dict)

    @property
    def kind_label(self) -> str:
        return KIND_LABELS.get(self.kind, self.kind)


def _moment(value: date | datetime | None) -> datetime:
    if value is None:
        return timezone.now()
    if isinstance(value, datetime):
        return value
    return timezone.make_aware(datetime.combine(value, time.min))


def certificate_activities(member: TBIMember) -> QuerySet:
    """Activities whose certificate approval waits for `member` (can_approve_certificates; none otherwise)."""
    from certificates.services import needs_approval_q

    if not has_permission(member, Permission.APPROVE_CERTIFICATES):
        return Activity.objects.none()
    waiting = Certificate.objects.filter(needs_approval_q(), activity=OuterRef("pk"))
    count = (Certificate.objects.filter(needs_approval_q(), activity=OuterRef("pk")).order_by()
             .values("activity").annotate(n=Count("pk")).values("n"))
    prepared = (Q(certificates_prepared_at__isnull=False, certificates_approved_at__isnull=True)
                & ~Q(status__name=ActivityStatus.CONDUCTED))
    return (Activity.objects.filter(Q(Exists(waiting)) | prepared)
            .annotate(waiting_count=Coalesce(Subquery(count, output_field=IntegerField()), 0))
            .select_related("status"))


def _certificate_items(member: TBIMember, filters: dict) -> list[InboxItem]:
    rows = certificate_activities(member)
    if filters.get("activity"):
        rows = rows.filter(pk=filters["activity"])
    if filters.get("q"):
        rows = rows.filter(title__icontains=filters["q"])
    items = []
    for activity in rows.order_by("end_date", "pk")[:ITEM_LIMIT]:
        count = activity.waiting_count
        detail = (f"{count} certificate{'' if count == 1 else 's'} waiting" if count
                  else "Prepared before the activity — approve them early")
        items.append(InboxItem(
            CERTIFICATES, activity.title, f"{reverse('activities:detail', args=[activity.pk])}?tab=certificates",
            _moment(activity.certificates_prepared_at if not count else activity.end_date),
            activity_id=activity.pk, activity_title=activity.title, detail=detail))
    return items


def _submission_items(member: TBIMember, filters: dict) -> list[InboxItem]:
    rows = services.waiting_for(member)
    if filters.get("activity"):
        rows = rows.filter(activity_id=filters["activity"])
    if filters.get("q"):
        rows = rows.filter(Q(title__icontains=filters["q"]) | Q(submitted_by__name__icontains=filters["q"]))
    return [InboxItem(SUBMISSION, row.title, reverse("approvals:detail", args=[row.pk]), row.submitted_at,
                      who=f"Submitted by {row.submitted_by.name}", activity_id=row.activity_id,
                      activity_title=row.activity.title if row.activity else "",
                      detail=row.file_extension.upper())
            for row in rows.order_by("submitted_at", "pk")[:ITEM_LIMIT]]


def _document_items(member: TBIMember, filters: dict) -> list[InboxItem]:
    rows = review_queue(member)
    if filters.get("activity"):
        rows = rows.filter(activity_id=filters["activity"])
    if filters.get("q"):
        rows = rows.filter(Q(title__icontains=filters["q"]) | Q(activity__title__icontains=filters["q"]))
    return [InboxItem(DOCUMENT, row.title, reverse("documents:detail", args=[row.pk]), row.uploaded_at,
                      who=f"Uploaded by {row.uploaded_by.name}", activity_id=row.activity_id,
                      activity_title=row.activity.title, detail=row.document_type.name)
            for row in rows[:ITEM_LIMIT]]


def _assignment_items(member: TBIMember, filters: dict) -> list[InboxItem]:
    from .assignments import overdue_assigned_by, target_title, target_url
    from .models import WorkAssignment

    rows = overdue_assigned_by(member)
    if filters.get("activity"):
        rows = rows.filter(Q(activity_id=filters["activity"]) | Q(document__activity_id=filters["activity"])
                           | Q(submission__activity_id=filters["activity"]))
    items = []
    for row in rows[:ITEM_LIMIT]:
        title = target_title(row)
        if filters.get("q") and filters["q"].lower() not in f"{title} {row.assigned_to.name}".lower():
            continue
        items.append(InboxItem(
            ASSIGNMENT, title, target_url(row), _moment(row.due_date), who=f"Assigned to {row.assigned_to.name}",
            activity_id=row.activity_id, activity_title=row.activity.title if row.activity else "",
            detail=f"{WorkAssignment.Kind(row.kind).label} · due {row.due_date:%b} {row.due_date.day}", overdue=True,
            extra={"assignment": row}))
    return items


SOURCES = {CERTIFICATES: _certificate_items, SUBMISSION: _submission_items, DOCUMENT: _document_items,
           ASSIGNMENT: _assignment_items}


def kinds_for(member: TBIMember) -> list[str]:
    """The kinds this member can have in the inbox (for the filter and the empty state)."""
    kinds = []
    if has_permission(member, Permission.APPROVE_CERTIFICATES):
        kinds.append(CERTIFICATES)
    if has_permission(member, Permission.APPROVE_DOCUMENTS):
        kinds += [SUBMISSION, DOCUMENT]
    if has_permission(member, Permission.ASSIGN_WORK):
        kinds.append(ASSIGNMENT)
    return kinds


def has_inbox(member: TBIMember) -> bool:
    return bool(kinds_for(member))


def inbox(member: TBIMember, filters: dict | None = None) -> list[InboxItem]:
    """Everything waiting for `member`, oldest first. filters: kind (a KIND_LABELS key), activity (ID), q (text)."""
    filters = filters or {}
    kinds = kinds_for(member)
    if filters.get("kind") in SOURCES:
        kinds = [kind for kind in kinds if kind == filters["kind"]]
    items = [item for kind in kinds for item in SOURCES[kind](member, filters)]
    return sorted(items, key=lambda item: (item.since, item.kind, item.title))


def inbox_counts(member: TBIMember) -> dict[str, int]:
    """{kind: n, "total": n} (one COUNT per kind the member has). The sidebar badge and the Dashboard card."""
    counts = {kind: 0 for kind in KIND_LABELS}
    if has_permission(member, Permission.APPROVE_CERTIFICATES):
        counts[CERTIFICATES] = certificate_activities(member).count()
    if has_permission(member, Permission.APPROVE_DOCUMENTS):
        counts[SUBMISSION] = services.waiting_for(member).count()
        counts[DOCUMENT] = review_queue(member).count()
    if has_permission(member, Permission.ASSIGN_WORK):
        from .assignments import overdue_assigned_by

        counts[ASSIGNMENT] = overdue_assigned_by(member).count()
    counts["total"] = sum(counts.values())
    return counts
