"""Display helpers for the Approvals pages (ASSUMPTIONS 437). Display and parsing only: every rule is in
approvals.services / approvals.inbox."""

from dataclasses import dataclass
from urllib.parse import urlencode

from activities.models import Activity
from documents.models import DocumentType

from . import inbox, signing
from .models import Submission

STATUS_TONES = {Submission.Status.SUBMITTED: "info", Submission.Status.APPROVED: "success",
                Submission.Status.RETURNED: "warning"}
KIND_TONES = {inbox.CERTIFICATES: "warning", inbox.SUBMISSION: "info", inbox.DOCUMENT: "neutral"}
KIND_ICONS = {inbox.CERTIFICATES: "award", inbox.SUBMISSION: "send", inbox.DOCUMENT: "file-check"}
STAMP_NOTE = ("The signature is a stamped image of the approver's signature from the signatory list, not a "
              "cryptographic digital signature.")


@dataclass
class InboxFilters:
    kind: str = ""
    activity: int | None = None
    q: str = ""

    @property
    def any_set(self) -> bool:
        return bool(self.kind or self.activity or self.q)

    def as_dict(self) -> dict:
        return {"kind": self.kind, "activity": self.activity, "q": self.q}

    @property
    def query(self) -> str:
        values = {key: value for key, value in self.as_dict().items() if value}
        return urlencode(values)


def parse_inbox_filters(params) -> InboxFilters:
    kind = params.get("kind", "")
    raw = params.get("activity", "")
    return InboxFilters(kind=kind if kind in inbox.KIND_LABELS else "",
                        activity=int(raw) if raw.isdigit() else None, q=(params.get("q") or "").strip()[:100])


def status_badge(submission: Submission) -> dict:
    return {"label": submission.get_status_display(), "tone": STATUS_TONES.get(submission.status, "neutral")}


def activity_choices() -> list[Activity]:
    """Activities to link a submission to (newest first; at most 300)."""
    return list(Activity.objects.order_by("-start_date", "-pk").only("pk", "title", "start_date")[:300])


def type_choices() -> list[DocumentType]:
    return list(DocumentType.objects.filter(is_active=True).order_by("name"))


def position_choices() -> list[tuple[str, str]]:
    return list(signing.POSITION_LABELS.items())


# --- Assignments (ASSUMPTIONS 437) -------------------------------------------------------------------------------------


def assign_panel(member, *, activity=None, document=None, submission=None, report: bool = False,
                 task: str | None = None) -> dict | None:
    """The "Assign to" dialog for one piece of work: the kinds this member may assign here (approvals.assignments
    .can_assign), the members to choose from, and the open assignments of this work (with Remove). None when the
    member may assign none of them. Display only: assign_work checks everything again. `task`: a work without an
    object (ASSUMPTIONS 460: "certificate_text" or "signatories"). A submission also offers "May stamp my signature"
    when the member has a linked signature with an image."""
    from . import assignments
    from .models import WorkAssignment

    Kind = WorkAssignment.Kind
    if activity is not None:
        candidates = [Kind.ACTIVITY, Kind.CERTIFICATE_APPROVAL, Kind.SIGNATORY_CHOICE]
        current = WorkAssignment.objects.filter(activity=activity, status="open")
    elif document is not None:
        candidates = [Kind.DOCUMENT_REVIEW]
        current = WorkAssignment.objects.filter(document=document, status="open")
    elif submission is not None:
        candidates = [Kind.SUBMISSION_REVIEW]
        current = WorkAssignment.objects.filter(submission=submission, status="open")
    elif report:
        candidates, current = [Kind.REPORT], WorkAssignment.objects.filter(kind=Kind.REPORT, status="open", assigned_by=member)
    elif task in WorkAssignment.GLOBAL_KINDS:
        candidates, current = [task], WorkAssignment.objects.filter(kind=task, status="open")
    else:
        return None
    kinds = assignments.assignable_kinds(member, candidates)
    if not kinds:
        return None
    taken = {row.kind for row in current} if not report else set()
    return {
        "kinds": [(kind, label) for kind, label in kinds if kind not in taken],
        "current": [{"assignment": row, "overdue": assignments.is_overdue(row), "label": Kind(row.kind).label,
                     "title": assignments.target_title(row)}
                    for row in current.select_related("assigned_to").order_by("pk")],
        "members": assignments.assignee_choices(member),
        "activity": activity, "document": document, "submission": submission, "report": report,
        "note_max": assignments.NOTE_MAX, "offers_signing": submission is not None and _has_stamp(member),
    }


def _has_stamp(member) -> bool:
    from certificates.services import signatory_for

    own = signatory_for(member)
    return own is not None and bool(own.signature_image_path)


def my_assignments_on(member, *, activity=None, document=None, submission=None) -> list[dict]:
    """The member's own open assignments on this work, for the notice "Assigned to you by …" on its page."""
    from . import assignments
    from .models import WorkAssignment

    filters = {"activity": activity} if activity is not None else {"document": document} if document is not None \
        else {"submission": submission} if submission is not None else None
    if filters is None:
        return []
    rows = WorkAssignment.objects.filter(assigned_to=member, status="open", **filters).select_related("assigned_by")
    return [{"assignment": row, "label": WorkAssignment.Kind(row.kind).label, "overdue": assignments.is_overdue(row),
             "manual_done": row.kind in assignments.MANUAL_DONE} for row in rows]


def assignment_rows(queryset) -> list[dict]:
    """Rows for "Assigned to me" and the Dashboard card."""
    from . import assignments
    from .models import WorkAssignment

    return [{"assignment": row, "label": WorkAssignment.Kind(row.kind).label, "title": assignments.target_title(row),
             "url": assignments.target_url(row), "overdue": assignments.is_overdue(row),
             "manual_done": row.kind in assignments.MANUAL_DONE}
            for row in queryset]
