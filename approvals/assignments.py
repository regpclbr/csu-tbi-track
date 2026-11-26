"""Assigning work (WORK_ASSIGNMENT, ASSUMPTIONS 437). Every rule about assignments lives here.

- Assign: a member with can_assign_work who ALSO holds the flag of the work (you only hand out work you could do) gives
  one piece of work to another active member, with an optional note and due date. Kinds: an activity (edit it, its
  forms, certificate text, attendance), its certificate approval, choosing its signatories, a document of a type that
  needs review, a submission, a report to prepare. One open assignment per piece of work.
- The assignee gets a notification and permission for THAT task only (accounts.permissions.has_permission_for checks
  open assignments; no role changes). Everything they do through it is written "on behalf of" the assigner (History
  ACTIVITY_AUDIT_LOG.on_behalf_of, SUBMISSION_EVENT.on_behalf_of, the audit lines).
- Done: automatically when the work is approved / returned (complete_for, called by the services), or "Mark done" by the
  assignee or the assigner (activities, signatories, reports). Removed: by the assigner (or another member who could
  assign it). Either way the permission ends at once.
- Separation of duties: never to yourself; nobody is assigned to review their own upload / submission or certificates
  they prepared or of an activity they created; a member whose role manages users is never given work that role may not
  have (check_role_flags: activities, signatories, documents, submissions, reports).
- ASSUMPTIONS 460: ALL the Director's work can be assigned (ASSIGNABLE_ACTIONS lists every Director-only action and its
  kind). Two kinds have no object: the default certificate text and the signatory list (one open assignment of each).
  A submission review may allow stamping the ASSIGNER's own linked signature ("May stamp my signature", allow_signing):
  logged "signed on behalf of". Never assignable: unapproving (documents, submissions, certificates), linking a
  signature to an account, changing a signature linked to an account, and assigning work itself.
"""

import logging
from datetime import date, datetime

from django.core.exceptions import PermissionDenied, ValidationError
from django.db import IntegrityError, transaction
from django.db.models import Q, QuerySet
from django.urls import reverse
from django.utils import timezone

from accounts.models import TBIMember
from accounts.permissions import Permission, has_permission, is_staff_member, require_permission
from notifications.models import Notification

from .models import WorkAssignment

audit_log = logging.getLogger("approvals.audit")

Kind = WorkAssignment.Kind
Status = WorkAssignment.Status

# The flag the work needs: the assigner must hold it; the assignee gets it for this one task.
KIND_PERMISSIONS = {
    Kind.ACTIVITY: Permission.CREATE_ACTIVITY,
    Kind.CERTIFICATE_APPROVAL: Permission.APPROVE_CERTIFICATES,
    Kind.SIGNATORY_CHOICE: Permission.APPROVE_CERTIFICATES,
    Kind.DOCUMENT_REVIEW: Permission.APPROVE_DOCUMENTS,
    Kind.SUBMISSION_REVIEW: Permission.APPROVE_DOCUMENTS,
    Kind.REPORT: Permission.CREATE_ACTIVITY,
    Kind.CERTIFICATE_TEXT: Permission.APPROVE_CERTIFICATES,
    Kind.SIGNATORIES: Permission.MANAGE_SIGNATORIES,
}
GLOBAL_KINDS = tuple(WorkAssignment.GLOBAL_KINDS)  # no target (ASSUMPTIONS 460)
TARGETLESS = (Kind.REPORT, *GLOBAL_KINDS)
# Work a role that manages users may never get (separation of duties, accounts.services.check_role_flags).
NOT_FOR_USER_MANAGERS = {Kind.ACTIVITY, Kind.SIGNATORY_CHOICE, Kind.DOCUMENT_REVIEW, Kind.SUBMISSION_REVIEW, Kind.REPORT,
                         Kind.SIGNATORIES}
# Kinds the assignee (or the assigner) closes with "Mark done"; the others close themselves when approved / returned.
MANUAL_DONE = {Kind.ACTIVITY, Kind.SIGNATORY_CHOICE, Kind.REPORT, Kind.CERTIFICATE_TEXT, Kind.SIGNATORIES,
               Kind.CERTIFICATE_APPROVAL}  # a certificate approval also closes itself when approved; reissuing: Mark done

# ASSUMPTIONS 460: every Director-only action (the flags only the Director holds by default) and how it is assigned.
# None = never assignable. Shown in ASSUMPTIONS 460 and checked by approvals/tests/test_assign_all_work.py.
ASSIGNABLE_ACTIONS = [
    ("Approve or return a document that needs review", Kind.DOCUMENT_REVIEW),
    ("Rename, replace or delete another member's waiting document", Kind.DOCUMENT_REVIEW),
    ("Unapprove a document", None),
    ("Approve or return a submission (never your own)", Kind.SUBMISSION_REVIEW),
    ("Stamp the Director's signature on a submission (\"May stamp my signature\")", Kind.SUBMISSION_REVIEW),
    ("Unapprove a submission", None),
    ("Approve an activity's certificates", Kind.CERTIFICATE_APPROVAL),
    ("Reissue an activity's outdated certificates", Kind.CERTIFICATE_APPROVAL),
    ("Unapprove an activity's certificates", None),
    ("Choose an activity's signatories and their titles", Kind.SIGNATORY_CHOICE),
    ("Edit the default certificate text", Kind.CERTIFICATE_TEXT),
    ("Manage the signatory list (add, edit, images, deactivate, defaults)", Kind.SIGNATORIES),
    ("Link a signature to an account / change a signature linked to an account", None),
    ("Close or reopen registration and the evaluation, edit another member's activity", Kind.ACTIVITY),
    ("Prepare any report (figures report or report generator)", Kind.REPORT),
    ("Assign work", None),
]
NOTE_MAX = 1000
REPORT_TITLE_MAX = 255

NOT_TO_YOURSELF = "You cannot assign work to yourself."
INACTIVE_ASSIGNEE = "Choose an active member."
USER_MANAGER_REFUSED = ("{name} manages users, so they cannot be given this work (separation of duties). Choose "
                        "another member.")
OWN_WORK_REFUSED = "{name} cannot review their own work (separation of duties). Choose another member."
ALREADY_ASSIGNED = "This work is already assigned to {name}. Remove that assignment first."
NOT_OPEN = "This assignment is already {state}."
NOT_ASSIGNABLE = "This {what} is not waiting for approval, so there is nothing to assign."
CONDUCTED_READ_ONLY = "A Conducted activity is read-only: there is no work on it to assign."


class AssignmentError(Exception):
    """An assignment was refused. The message is safe to show."""


# --- Reading -----------------------------------------------------------------------------------------------------------


def open_assignment(member: TBIMember, kinds: tuple, obj=None) -> WorkAssignment | None:
    """The member's open assignment of one of `kinds` on `obj` (None for a report), or None. Used by
    accounts.permissions.has_permission_for: an inactive member or a closed assignment never counts."""
    if not is_staff_member(member):
        return None
    queryset = WorkAssignment.objects.filter(assigned_to=member, status=Status.OPEN, kind__in=kinds)
    name = obj._meta.model_name if obj is not None else None
    if name in ("activity", "document", "submission"):
        queryset = queryset.filter(**{name: obj})
    elif obj is not None:
        return None
    return queryset.select_related("assigned_by").first()


def open_for(kind: str, obj) -> WorkAssignment | None:
    """The open assignment of `kind` on `obj` (anyone's), or None. A global kind (ASSUMPTIONS 460) has no object."""
    if kind in GLOBAL_KINDS:
        return (WorkAssignment.objects.filter(kind=kind, status=Status.OPEN)
                .select_related("assigned_to", "assigned_by").first())
    field = target_field(kind)
    if field is None:
        return None
    return (WorkAssignment.objects.filter(kind=kind, status=Status.OPEN, **{field: obj})
            .select_related("assigned_to", "assigned_by").first())


def target_field(kind: str) -> str | None:
    if kind in WorkAssignment.ACTIVITY_KINDS:
        return "activity"
    return {Kind.DOCUMENT_REVIEW: "document", Kind.SUBMISSION_REVIEW: "submission"}.get(kind)


def shows_assigned_to_me(member) -> bool:
    """Show "Assigned to me" (ASSUMPTIONS 456): hidden for a member who gives the work (can_assign_work, the Director)
    and has never been given any; shown to everyone else and whenever something was ever assigned to the member."""
    from accounts.permissions import Permission, has_permission

    if not has_permission(member, Permission.ASSIGN_WORK):
        return True
    return WorkAssignment.objects.filter(assigned_to=member).exists()


def assigned_to_me(member: TBIMember, include_closed: bool = False) -> QuerySet:
    """The member's assignments, due first (no due date last), open only unless include_closed."""
    queryset = (WorkAssignment.objects.filter(assigned_to=member)
                .select_related("activity", "document", "document__activity", "submission", "assigned_by"))
    if not include_closed:
        queryset = queryset.filter(status=Status.OPEN)
    return queryset.order_by("status", "due_date", "created_at", "pk")


def overdue_assigned_by(member: TBIMember, today: date | None = None) -> QuerySet:
    """Open assignments `member` gave whose due date has passed (Manila): the Director's inbox."""
    today = today or timezone.localdate()
    return (WorkAssignment.objects.filter(assigned_by=member, status=Status.OPEN, due_date__lt=today)
            .select_related("activity", "document", "submission", "assigned_to").order_by("due_date", "pk"))


def is_overdue(assignment: WorkAssignment, today: date | None = None) -> bool:
    today = today or timezone.localdate()
    return assignment.status == Status.OPEN and assignment.due_date is not None and assignment.due_date < today


def target_title(assignment: WorkAssignment) -> str:
    if assignment.kind == Kind.REPORT:
        return assignment.report_title
    if assignment.kind in GLOBAL_KINDS:
        return Kind(assignment.kind).label
    target = assignment.activity or assignment.document or assignment.submission
    return target.title if target is not None else ""


def target_url(assignment: WorkAssignment) -> str:
    """Where the work is done (one click from the inbox, the notification and "Assigned to me")."""
    if assignment.kind == Kind.REPORT:
        return reverse("reports:list")
    if assignment.kind == Kind.CERTIFICATE_TEXT:
        return reverse("certificates:text")
    if assignment.kind == Kind.SIGNATORIES:
        return reverse("certificates:signatories")
    if assignment.kind == Kind.DOCUMENT_REVIEW:
        return reverse("documents:detail", args=[assignment.document_id])
    if assignment.kind == Kind.SUBMISSION_REVIEW:
        return reverse("approvals:detail", args=[assignment.submission_id])
    url = reverse("activities:detail", args=[assignment.activity_id])
    return f"{url}?tab=certificates" if assignment.kind in (Kind.CERTIFICATE_APPROVAL, Kind.SIGNATORY_CHOICE) else url


def can_assign(member: TBIMember, kind: str) -> bool:
    """can_assign_work AND the flag of the work (for the dialog; assign_work checks again)."""
    return has_permission(member, Permission.ASSIGN_WORK) and has_permission(member, KIND_PERMISSIONS[kind])


def assignable_kinds(member: TBIMember, kinds) -> list[tuple[str, str]]:
    return [(kind, Kind(kind).label) for kind in kinds if can_assign(member, kind)]


# ASSUMPTIONS 468: the general "Assign work" dialog says what each kind is in plain words, in this order.
KIND_DESCRIPTIONS = {
    Kind.DOCUMENT_REVIEW: "Review a document waiting for approval",
    Kind.SUBMISSION_REVIEW: "Review a submission sent to you",
    Kind.CERTIFICATE_APPROVAL: "Approve an activity's certificates",
    Kind.SIGNATORY_CHOICE: "Choose who signs an activity's certificates",
    Kind.ACTIVITY: "Work on an activity (edit it, registration, evaluation, attendance forms, photos)",
    Kind.REPORT: "Prepare a report",
    Kind.CERTIFICATE_TEXT: "Edit the default certificate text",
    Kind.SIGNATORIES: "Manage the signatory list and signature images",
}
GENERAL_TARGET_LIMIT = 200  # targets per kind in the general dialog (an office never has this many open)


def refused_now(kind: str, target) -> str | None:
    """Why this work cannot be assigned right now (the same checks assign_work makes before the member is looked at:
    the work must still be open and not assigned already), or None."""
    if kind in GLOBAL_KINDS:
        existing = open_for(kind, None)
    elif kind == Kind.REPORT:
        return None
    else:
        if refused := _target_refused(kind, target):
            return refused
        existing = open_for(kind, target)
    return ALREADY_ASSIGNED.format(name=existing.assigned_to.name) if existing else None


def _candidate_targets(member: TBIMember, kind: str):
    """The objects one kind could be about, before refused_now: waiting documents of a type that needs review, waiting
    submissions (never the member's own), activities that are not Conducted (work on it), not Conducted or with
    certificates not issued yet (signatories), with certificates or prepared ones (certificate approval)."""
    from activities.models import Activity, ActivityStatus
    from approvals.services import waiting_for
    from documents.services import review_queue

    if kind == Kind.DOCUMENT_REVIEW:
        return review_queue(member).select_related("activity")
    if kind == Kind.SUBMISSION_REVIEW:
        return waiting_for(member).select_related("submitted_by")
    activities = Activity.objects.select_related("status").order_by("-start_date", "-pk")
    if kind == Kind.CERTIFICATE_APPROVAL:
        return activities.filter(Q(certificates__isnull=False) | Q(certificates_prepared_at__isnull=False)).distinct()
    if kind == Kind.SIGNATORY_CHOICE:
        return activities.filter(~Q(status__name=ActivityStatus.CONDUCTED)
                                 | Q(certificates__status__in=("generated", "approved"))).distinct()
    return activities.exclude(status__name=ActivityStatus.CONDUCTED)


def _target_label(kind: str, target) -> str:
    if kind == Kind.DOCUMENT_REVIEW:
        return f"{target.title} · {target.activity.title}"[:150]
    if kind == Kind.SUBMISSION_REVIEW:
        return f"{target.title} · from {target.submitted_by.name}"[:150]
    return f"{target.title} ({target.status.name}, {target.start_date:%b} {target.start_date.day}, {target.start_date.year})"[:150]


def general_choices(member: TBIMember) -> list[dict]:
    """The general "Assign work" dialog (ASSUMPTIONS 468): every kind this member may assign (can_assign), in plain
    words, with the targets that can be assigned NOW (refused_now is None) or, for a kind without a target, whether it
    is free. A kind with nothing to assign stays in the list with the reason, so the Director sees why. Display only:
    assign_work checks everything again."""
    choices = []
    for kind, description in KIND_DESCRIPTIONS.items():
        if not can_assign(member, kind):
            continue
        field = target_field(kind)
        entry = {"kind": kind, "label": description, "field": field, "targets": [], "reason": ""}
        if field is None:
            entry["reason"] = refused_now(kind, None) or ""
        else:
            entry["targets"] = [(target.pk, _target_label(kind, target))
                                for target in _candidate_targets(member, kind)[:GENERAL_TARGET_LIMIT]
                                if refused_now(kind, target) is None]
            if not entry["targets"]:
                entry["reason"] = {Kind.DOCUMENT_REVIEW: "No document is waiting for review.",
                                   Kind.SUBMISSION_REVIEW: "No submission is waiting for you.",
                                   Kind.CERTIFICATE_APPROVAL: "No activity has certificates waiting for approval."}.get(
                    kind, "No activity is open for this work.")
        choices.append(entry)
    return choices


def assignee_choices(member: TBIMember) -> list[TBIMember]:
    """Active members other than `member` (the service refuses the ones a rule excludes, with the reason)."""
    return list(TBIMember.objects.filter(is_active=True).exclude(pk=member.pk).select_related("role").order_by("name"))


# --- Rules --------------------------------------------------------------------------------------------------------------


def own_work_reason(assignee: TBIMember, kind: str, target) -> str | None:
    """Separation of duties: why `assignee` may not take this review (their own work), or None."""
    owner_ids = set()
    if kind == Kind.DOCUMENT_REVIEW:
        owner_ids = {target.uploaded_by_id}
    elif kind == Kind.SUBMISSION_REVIEW:
        owner_ids = {target.submitted_by_id}
    elif kind == Kind.CERTIFICATE_APPROVAL:
        owner_ids = {target.created_by_id, target.certificates_prepared_by_id}
    return OWN_WORK_REFUSED.format(name=assignee.name) if assignee.pk in owner_ids else None


def _target_refused(kind: str, target) -> str | None:
    """The work must still be open for it to be assigned."""
    from activities.models import ActivityStatus
    from approvals.models import Submission
    from certificates import services as certificate_services
    from documents import services as document_services

    if kind == Kind.ACTIVITY and target.status.name == ActivityStatus.CONDUCTED:
        return CONDUCTED_READ_ONLY
    if (kind == Kind.CERTIFICATE_APPROVAL and certificate_services.is_approved(target)
            and not certificate_services.outdated_certificates(target).exists()):  # approved and nothing to reissue (460)
        return certificate_services.ALREADY_APPROVED
    if kind == Kind.DOCUMENT_REVIEW and not (target.review_state == target.WAITING_FOR_REVIEW
                                             and document_services.needs_review(target.document_type,
                                                                                target.related_certificate)):
        return NOT_ASSIGNABLE.format(what="document")
    if kind == Kind.SUBMISSION_REVIEW and target.status != Submission.Status.SUBMITTED:
        return NOT_ASSIGNABLE.format(what="submission")
    return None


def _clean(note: str, due_date: date | None, report_title: str, kind: str) -> tuple[str, str]:
    problems = {}
    note = (note or "").strip()
    report_title = (report_title or "").strip()
    if len(note) > NOTE_MAX:
        problems["note"] = [f"The note can be at most {NOTE_MAX} characters."]
    if kind == Kind.REPORT and not report_title:
        problems["report_title"] = ["Say which report to prepare, for example: Accomplishment Report - Q4 2026."]
    if len(report_title) > REPORT_TITLE_MAX:
        problems["report_title"] = [f"At most {REPORT_TITLE_MAX} characters."]
    if due_date is not None and due_date < timezone.localdate():
        problems["due_date"] = ["The due date cannot be in the past."]
    if problems:
        raise ValidationError(problems)
    return note, report_title


def _history(assignment: WorkAssignment, member: TBIMember, old: str, new: str) -> None:
    """A History row on the activity of the work (activity kinds, documents), never "on behalf of" (the assigner acts)."""
    from activities.services import write_assignment_history

    activity = assignment.activity or (assignment.document.activity if assignment.document_id else None) \
        or (assignment.submission.activity if assignment.submission_id else None)
    if activity is not None:
        write_assignment_history(activity, member, old, new)


def _describe(assignment: WorkAssignment) -> str:
    what = Kind(assignment.kind).label
    title = target_title(assignment)
    if assignment.kind not in (*WorkAssignment.ACTIVITY_KINDS, *GLOBAL_KINDS):
        what = f'{what} "{title[:120]}"'
    due = f", due {assignment.due_date:%b} {assignment.due_date.day}, {assignment.due_date.year}" if assignment.due_date else ""
    return f"{what} assigned to {assignment.assigned_to.name}{due}"


def _notify(member: TBIMember, assignment: WorkAssignment, kind: str, message: str) -> None:
    if member.is_active:
        Notification.objects.create(member=member, activity=assignment.activity, work_assignment=assignment,
                                    submission=assignment.submission, type=kind, message=message[:500])


# --- Assign, mark done, remove ---------------------------------------------------------------------------------------


SIGNING_ONLY_FOR_SUBMISSIONS = "Only a submission review can allow stamping your signature."
SIGNING_NEEDS_YOUR_SIGNATURE = ("Your account is not linked to a signature with an image, so there is nothing to stamp. "
                                "Link it on the Signatories page first.")


def assign_work(member: TBIMember, kind: str, assignee: TBIMember | None, *, activity=None, document=None,
                submission=None, report_title: str = "", note: str = "", due_date: date | None = None,
                allow_signing: bool = False) -> WorkAssignment:
    """"Assign to": one piece of work to one member. Refused (AssignmentError / ValidationError / PermissionDenied) by
    the rules above. The assignee is notified; a History row on the activity of the work; approvals.audit.
    `allow_signing` (submission reviews only, ASSUMPTIONS 460): the assignee may stamp the ASSIGNER's own linked
    signature while approving; the assigner must have one with an image."""
    if kind not in KIND_PERMISSIONS:
        raise AssignmentError("Choose what to assign.")
    require_permission(member, Permission.ASSIGN_WORK)
    require_permission(member, KIND_PERMISSIONS[kind])
    note, report_title = _clean(note, due_date, report_title, kind)
    if assignee is None or not is_staff_member(assignee):
        raise ValidationError({"assigned_to": [INACTIVE_ASSIGNEE]})
    if assignee.pk == member.pk:
        raise ValidationError({"assigned_to": [NOT_TO_YOURSELF]})
    if kind in NOT_FOR_USER_MANAGERS and has_permission(assignee, Permission.MANAGE_USERS):
        raise ValidationError({"assigned_to": [USER_MANAGER_REFUSED.format(name=assignee.name)]})
    if allow_signing:
        from certificates.services import signatory_for

        if kind != Kind.SUBMISSION_REVIEW:
            raise ValidationError({"allow_signing": [SIGNING_ONLY_FOR_SUBMISSIONS]})
        own = signatory_for(member)
        if own is None or not own.signature_image_path:
            raise ValidationError({"allow_signing": [SIGNING_NEEDS_YOUR_SIGNATURE]})
    target = {Kind.DOCUMENT_REVIEW: document, Kind.SUBMISSION_REVIEW: submission}.get(kind, activity)
    if kind not in TARGETLESS and target is None:
        raise AssignmentError("Choose the work to assign.")
    if kind in GLOBAL_KINDS:
        if existing := open_for(kind, None):
            raise AssignmentError(ALREADY_ASSIGNED.format(name=existing.assigned_to.name))
    elif kind != Kind.REPORT:
        if refused := _target_refused(kind, target) or own_work_reason(assignee, kind, target):
            raise AssignmentError(refused)
        if existing := open_for(kind, target):
            raise AssignmentError(ALREADY_ASSIGNED.format(name=existing.assigned_to.name))
    assignment = WorkAssignment(
        kind=kind, activity=activity if kind in WorkAssignment.ACTIVITY_KINDS else None,
        document=document if kind == Kind.DOCUMENT_REVIEW else None,
        submission=submission if kind == Kind.SUBMISSION_REVIEW else None,
        report_title=report_title if kind == Kind.REPORT else "", assigned_to=assignee, assigned_by=member,
        note=note, due_date=due_date, allow_signing=bool(allow_signing))
    try:
        with transaction.atomic():
            assignment.save()
            _history(assignment, member, "", _describe(assignment))
            _notify(assignee, assignment, Notification.WORK_ASSIGNED,
                    f"{member.name[:60]} assigned you: {_describe(assignment).replace(' assigned to ' + assignee.name, '')}"
                    + (f". Note: {note}" if note else ""))
            audit_log.info("Work assigned: assignment_id=%s kind=%s to member_id=%s by member_id=%s signing=%s",
                           assignment.pk, kind, assignee.pk, member.pk, assignment.allow_signing)
    except IntegrityError as error:  # two clicks at once: the unique constraint keeps one open assignment
        raise AssignmentError("This work was just assigned by someone else. Reload the page.") from error
    return assignment


def _close(assignment: WorkAssignment, status: str, member: TBIMember | None, now: datetime) -> None:
    assignment.status = status
    if status == Status.DONE:
        assignment.completed_at, assignment.completed_by = now, member
    else:
        assignment.removed_at, assignment.removed_by = now, member
    assignment.save()


@transaction.atomic
def mark_done(member: TBIMember, assignment: WorkAssignment, generated_report=None,
              now: datetime | None = None) -> WorkAssignment:
    """"Mark done": the assignee or the assigner, on an open assignment. A report to prepare may name the report the
    assignee generated (their own). The assigner is told when the assignee marks it done."""
    assignment = WorkAssignment.objects.select_for_update(of=("self",)).select_related(
        "assigned_to", "assigned_by", "activity", "document", "submission").get(pk=assignment.pk)
    if member.pk not in (assignment.assigned_to_id, assignment.assigned_by_id):
        raise PermissionDenied("Only the member it is assigned to, or the member who assigned it, can mark it done.")
    if assignment.status != Status.OPEN:
        raise AssignmentError(NOT_OPEN.format(state=assignment.get_status_display().lower()))
    if generated_report is not None:
        if assignment.kind != Kind.REPORT or generated_report.generated_by_id != assignment.assigned_to_id:
            raise AssignmentError("Choose a report generated by the member it is assigned to.")
        assignment.generated_report = generated_report
    _close(assignment, Status.DONE, member, now or timezone.now())
    _history(assignment, member, _describe(assignment), "Done")
    if member.pk == assignment.assigned_to_id:
        _notify(assignment.assigned_by, assignment, Notification.WORK_ASSIGNMENT_DONE,
                f"{member.name[:60]} marked done: {Kind(assignment.kind).label} - {target_title(assignment)[:150]}")
    audit_log.info("Work assignment done: assignment_id=%s by member_id=%s", assignment.pk, member.pk)
    return assignment


@transaction.atomic
def remove_assignment(member: TBIMember, assignment: WorkAssignment, now: datetime | None = None) -> WorkAssignment:
    """"Remove the assignment": the assigner, or another member who could assign this kind of work. The permission it
    gave ends at once; the assignee is told."""
    assignment = WorkAssignment.objects.select_for_update(of=("self",)).select_related(
        "assigned_to", "assigned_by", "activity", "document", "submission").get(pk=assignment.pk)
    if member.pk != assignment.assigned_by_id and not can_assign(member, assignment.kind):
        raise PermissionDenied("Only the member who assigned it (or another member who can assign it) can remove it.")
    if assignment.status != Status.OPEN:
        raise AssignmentError(NOT_OPEN.format(state=assignment.get_status_display().lower()))
    _close(assignment, Status.REMOVED, member, now or timezone.now())
    _history(assignment, member, _describe(assignment), "Assignment removed")
    _notify(assignment.assigned_to, assignment, Notification.WORK_ASSIGNMENT_REMOVED,
            f"{member.name[:60]} removed your assignment: {Kind(assignment.kind).label} - {target_title(assignment)[:150]}")
    audit_log.info("Work assignment removed: assignment_id=%s by member_id=%s", assignment.pk, member.pk)
    return assignment


def complete_for(kind: str, obj, member: TBIMember | None, now: datetime | None = None) -> int:
    """The work was done (approved / returned): close its open assignment as done, by whoever did it (the assignee,
    or the Director themselves). Called inside the services' transactions. Returns how many were closed (0 or 1)."""
    field = target_field(kind)
    if field is None or obj is None:
        return 0
    rows = list(WorkAssignment.objects.select_for_update().filter(kind=kind, status=Status.OPEN, **{field: obj}))
    for assignment in rows:
        _close(assignment, Status.DONE, member, now or timezone.now())
        audit_log.info("Work assignment done with the work: assignment_id=%s by member_id=%s", assignment.pk,
                       member.pk if member else None)
    return len(rows)


def acting_assignment(member: TBIMember, kind: str, obj) -> WorkAssignment | None:
    """The open assignment `member` would act through for this work (None when their role flag allows it)."""
    if has_permission(member, KIND_PERMISSIONS[kind]):
        return None
    return open_assignment(member, (kind,), obj)
