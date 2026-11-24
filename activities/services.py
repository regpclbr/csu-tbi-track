"""Business logic for activities, status changes, resource persons and the audit log.

Views (and the Django admin) stay thin and call the functions in this module, so every
change to an activity is validated the same way and always writes the audit log.
"""

from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from decimal import Decimal

from django.core.exceptions import ObjectDoesNotExist, ValidationError
from django.core.paginator import Paginator
from django.db import transaction
from django.db.models import Count, Exists, F, OuterRef, Q, QuerySet, Subquery
from django.db.models.functions import Coalesce, Lower, Trim
from django.utils import timezone

from accounts.models import TBIMember
from accounts.permissions import Permission, require_permission
from registrations.models import FormTemplate, FormType, Registration

from .formatting import format_audit_value, format_time_range
from .models import (
    Activity,
    ActivityAuditLog,
    ActivityParticipantCategory,
    ActivityResourcePerson,
    ActivityStatus,
    ParticipantCategory,
    ResourcePerson,
)

from . import budget  # ASSUMPTIONS 433 (budget lines; imports this module lazily)

DRAFT, SCHEDULED, ONGOING, CONDUCTED = (
    ActivityStatus.DRAFT,
    ActivityStatus.SCHEDULED,
    ActivityStatus.ONGOING,
    ActivityStatus.CONDUCTED,
)

# The only status moves; Conducted is final. Staff make them by hand (change_status); the system makes two of them
# by itself on time (apply_automatic_statuses / refresh_status): Scheduled -> Ongoing and Ongoing -> Conducted.
ALLOWED_STATUS_MOVES: dict[str, set[str]] = {
    DRAFT: {SCHEDULED},
    SCHEDULED: {ONGOING, DRAFT},
    ONGOING: {CONDUCTED},
    CONDUCTED: set(),
}

# Fields a member can set when creating or editing an activity, with the label used in the audit log.
FIELD_LABELS: dict[str, str] = {
    "title": "Title",
    "description": "Description",
    "objectives": "Objectives",
    "activity_type": "Activity Type",
    "participant_categories": "Participant Categories",
    "start_date": "Start Date",
    "end_date": "End Date",
    "start_time": "Start Time",
    "end_time": "End Time",
    "venue": "Venue",
    "max_slots": "Max Slots",
    "registration_deadline": "Registration Deadline",
    "evaluation_deadline": "Evaluation Deadline",
    # ASSUMPTIONS 433: budget_amount is no longer typed (the sum of the budget lines, activities.budget); the lines come
    # as the pseudo-field "budget_lines" (budget.LINES_KEY) next to these fields.
    "fund_source": "Fund Source",
    "fund_account": "Fund Account",
}
EDITABLE_FIELDS = tuple(FIELD_LABELS)
CATEGORIES = "participant_categories"  # many-to-many (ACTIVITY_PARTICIPANT_CATEGORY), not a column
SCALAR_FIELDS = tuple(field for field in EDITABLE_FIELDS if field != CATEGORIES)  # columns on ACTIVITY
# evaluation_deadline empty = stays open (ASSUMPTIONS 423); fund_source has its own rule (required when creating and never
# cleared; older activities may still be "Not set", ASSUMPTIONS 433); fund_account is optional.
OPTIONAL_FIELDS = ("description", "objectives", "evaluation_deadline", "fund_source", "fund_account")
REQUIRED_FIELDS = tuple(field for field in SCALAR_FIELDS if field not in OPTIONAL_FIELDS)
TEXT_FIELDS = ("title", "description", "venue", "fund_account")
BUDGET_TYPED = "The budget amount is calculated from the budget lines; it cannot be typed."
FUND_SOURCE_REQUIRED = "Choose the fund source."
OBJECTIVES_MAX = 5000  # characters, all lines together (ASSUMPTIONS 428)

STATUS_LABEL = "Status"
RESOURCE_PERSON_ADDED = "Resource Person Added"
RESOURCE_PERSON_REMOVED = "Resource Person Removed"
CERTIFICATES_LABEL = "Certificates"  # created automatically / deleted with an undone attendance (ASSUMPTIONS 362)
EVALUATION_LINKS_LABEL = "Evaluation Links"  # sent automatically by the 5-minute job (ASSUMPTIONS 419)
DOCUMENTS_LABEL = "Documents"  # uploaded as an approved copy, replaced, deleted (documents.services, ASSUMPTIONS 432)
# Close / reopen registration by hand (ASSUMPTIONS 427): the History row's label and values.
REGISTRATION_LABEL = "Registration"
REGISTRATION_CLOSED_VALUE = "Closed by the TBI"
REGISTRATION_REOPENED_VALUE = "Reopened by the TBI"

DEFAULT_PAGE_SIZE = 20


class ActivityError(Exception):
    """An action on an activity was refused. The message is safe to show to the member."""


# --- Calculated values (never stored) ---------------------------------------------------


def registered_count(activity: Activity) -> int:
    """How many people registered. Everyone who registers is in (no approval step)."""
    return activity.registrations.count()


def slots_remaining(activity: Activity) -> int:
    return max(activity.max_slots - registered_count(activity), 0)


def is_registration_open(activity: Activity, now: datetime | None = None) -> bool:
    """Open only when the activity is Scheduled, not closed by hand (ASSUMPTIONS 427), the deadline has not passed,
    and slots remain."""
    now = now or timezone.now()
    return (
        activity.status.name == SCHEDULED
        and not activity.registration_closed_by_hand
        and now <= activity.registration_deadline
        and slots_remaining(activity) > 0
    )


def activity_start_end(activity: Activity) -> tuple[datetime, datetime]:
    """When the activity starts and ends, in Manila time (TIME_ZONE): the start date at start_time and the end
    date at end_time. Without times (an old activity, migration 0005): 00:00 on the start date, and the end of
    the end date (00:00 of the next day). The automatic status moves use these."""
    if activity.start_time is None or activity.end_time is None:
        start = datetime.combine(activity.start_date, time(0, 0))
        end = datetime.combine(activity.end_date + timedelta(days=1), time(0, 0))
    else:
        start = datetime.combine(activity.start_date, activity.start_time)
        end = datetime.combine(activity.end_date, activity.end_time)
    return timezone.make_aware(start), timezone.make_aware(end)


def has_attendance(activity: Activity) -> bool:
    """At least one registration is marked as attended."""
    return activity.registrations.filter(attendance_confirmed=True).exists()


def attended_count(activity: Activity) -> int:
    """How many registrations are marked as attended (the has_attendance() rule, counted)."""
    return activity.registrations.filter(attendance_confirmed=True).count()


def _not_ended(now: datetime) -> Q:
    """activity_start_end()'s end is still ahead, as a query: the end date is later than today, or it is today and
    the end time is later than now (no end time = the end of the end date, so still ahead all day)."""
    local = timezone.localtime(now) if timezone.is_aware(now) else now
    today, clock = local.date(), local.time().replace(tzinfo=None)
    return Q(end_date__gt=today) | Q(end_date=today, end_time__isnull=True) | Q(end_date=today, end_time__gt=clock)


def happening_now(activity_type: int | None = None, now: datetime | None = None) -> QuerySet:
    """Ongoing activities running today (Manila): start_date <= today <= end_date and the end
    (activity_start_end) not passed yet, the one that started first first (start date, then start time; no time =
    00:00). The automatic moves put an activity in Ongoing when its start arrives, so callers run
    apply_automatic_statuses() first. An Ongoing activity whose end has passed (attendance not marked yet) is not
    "happening now": it is in the Activities list's "Needs follow-up" group and the Needs attention box
    (ASSUMPTIONS 351, 353). The list's group uses is_happening_now(), the same rule (parity test).
    Draft and Scheduled are never included."""
    now = now or timezone.now()
    today = _local_date(now)
    activities = Activity.objects.filter(status__name=ONGOING, start_date__lte=today, end_date__gte=today).filter(_not_ended(now))
    if activity_type:
        activities = activities.filter(activity_type_id=activity_type)
    return activities.order_by("start_date", F("start_time").asc(nulls_first=True), "title", "pk")


def is_happening_now(activity: Activity, now: datetime) -> bool:
    """happening_now()'s rule for one loaded activity (the Activities list's "Happening now" group)."""
    _start, end = activity_start_end(activity)
    return activity.status.name == ONGOING and activity.start_date <= _local_date(now) <= activity.end_date and now < end


# --- Validation ---------------------------------------------------------------------------


def _local_date(value: datetime) -> date:
    return timezone.localtime(value).date() if timezone.is_aware(value) else value.date()


def clean_objectives(value) -> str | None:
    """Objectives (ASSUMPTIONS 428): one per line; each line trimmed, empty lines dropped; nothing left = None."""
    if not isinstance(value, str):
        return value
    lines = [" ".join(line.split()) for line in value.splitlines()]
    return "\n".join(line for line in lines if line) or None


def objective_lines(activity: Activity) -> list[str]:
    """The activity's objectives as a list (the Overview and, later, the reports)."""
    return [line for line in (activity.objectives or "").splitlines() if line.strip()]


def _clean_values(data: dict) -> dict:
    """Strip text fields and treat an empty description as no description. The budget lines (budget.LINES_KEY) pass
    through for create / update to check; a typed budget_amount is refused (ASSUMPTIONS 433)."""
    if "budget_amount" in data:
        raise ValidationError({"budget_amount": [BUDGET_TYPED]})
    unknown = set(data) - set(EDITABLE_FIELDS) - {budget.LINES_KEY}
    if unknown:
        raise ActivityError(f"These fields cannot be set: {', '.join(sorted(unknown))}.")
    cleaned = dict(data)
    for field in TEXT_FIELDS:
        if isinstance(cleaned.get(field), str):
            cleaned[field] = cleaned[field].strip()
    if cleaned.get("description") == "":
        cleaned["description"] = None
    if cleaned.get("fund_account") is None and "fund_account" in cleaned:
        cleaned["fund_account"] = ""
    if "objectives" in cleaned:
        cleaned["objectives"] = clean_objectives(cleaned["objectives"])
    if CATEGORIES in cleaned:
        cleaned[CATEGORIES] = _category_set(cleaned[CATEGORIES])
    return cleaned


def _category_set(categories) -> frozenset[ParticipantCategory]:
    """Categories as a set (order and repeats do not matter)."""
    return frozenset(categories or ())


def activity_values(activity: Activity) -> dict:
    """The editable field values of an activity (None for a foreign key that is not set yet).

    `participant_categories` is a set of categories (empty for an unsaved activity).
    """
    values = {}
    for field in SCALAR_FIELDS:
        try:
            values[field] = getattr(activity, field)
        except ObjectDoesNotExist:
            values[field] = None
    values[CATEGORIES] = _category_set(activity.participant_categories.all()) if activity.pk else frozenset()
    return values


def validate_activity_values(
    values: dict,
    activity: Activity | None = None,
    changed_fields: set[str] | None = None,
    now: datetime | None = None,
) -> None:
    """Check a full set of activity values. Raises ValidationError with one message per field.

    `activity` is the saved activity when editing (None when creating).
    `changed_fields`: when editing, the fields being changed. The "not in the past" rules
    apply only to these, so an activity that already started can still be edited.
    `now` defaults to the current time; "today" is its date in Manila time (settings.TIME_ZONE).
    """
    now = now or timezone.now()
    today = timezone.localdate(now)
    check_past = (lambda field: True) if activity is None else (lambda field: field in (changed_fields or set()))
    errors: dict[str, str] = {}
    for field in REQUIRED_FIELDS:
        if values.get(field) in (None, ""):
            errors[field] = "This field is required."
    if not values.get(CATEGORIES):
        errors[CATEGORIES] = "Choose at least one participant category."
    elif activity is not None and activity.pk:
        # A category cannot be removed while registrations of this activity use it.
        removed = set(activity.participant_categories.all()) - set(values[CATEGORIES])
        in_use = sorted(
            category.name for category in removed if activity.registrations.filter(participant_category=category).exists()
        )
        if in_use:
            errors[CATEGORIES] = f"Cannot remove {', '.join(in_use)}: registrations of this activity use it."
    if errors:
        raise ValidationError(errors)

    start, end = values["start_date"], values["end_date"]
    if check_past("start_date") and start < today:
        errors["start_date"] = "Start date cannot be in the past."
    if check_past("registration_deadline") and values["registration_deadline"] < now:
        errors["registration_deadline"] = "Registration deadline cannot be in the past."
    if end < start:
        errors["end_date"] = "End date must be on or after the start date."
    if _local_date(values["registration_deadline"]) > start:
        errors.setdefault("registration_deadline", "Registration deadline must be on or before the start date.")
    if values.get("evaluation_deadline") is not None and _local_date(values["evaluation_deadline"]) < end:
        errors["evaluation_deadline"] = "Evaluation deadline must be on or after the end date."
    if start == end and values["end_time"] <= values["start_time"]:
        errors["end_time"] = "End time must be after the start time."

    max_slots = values["max_slots"]
    if max_slots < 1:
        errors["max_slots"] = "Max slots must be at least 1."
    elif activity is not None and max_slots < (count := registered_count(activity)):
        errors["max_slots"] = f"Max slots cannot be lower than the {count} people already registered."

    if values.get("objectives") and len(values["objectives"]) > OBJECTIVES_MAX:
        errors["objectives"] = f"The objectives can be at most {OBJECTIVES_MAX} characters."

    # ASSUMPTIONS 433: one fund source, required when creating and never cleared; an inactive one only if it stays.
    fund_source = values.get("fund_source")
    fund_changed = activity is None or (fund_source.pk if fund_source else None) != activity.fund_source_id
    if fund_source is None and (activity is None or activity.fund_source_id is not None):
        errors["fund_source"] = FUND_SOURCE_REQUIRED
    elif fund_source is not None and fund_changed and not fund_source.is_active:
        errors["fund_source"] = f'Fund source "{fund_source}" is inactive. Choose an active one.'
    if len(values.get("fund_account") or "") > 255:
        errors["fund_account"] = "The account / charging can be at most 255 characters."

    activity_type = values["activity_type"]
    type_changed = activity is None or activity_type.pk != activity.activity_type_id
    if type_changed and not activity_type.is_active:
        errors["activity_type"] = f'Activity type "{activity_type}" is inactive. Choose an active type.'

    if errors:
        raise ValidationError(errors)


def _ensure_not_conducted(activity: Activity, action: str) -> None:
    if activity.status.name == CONDUCTED:
        raise ActivityError(f"A Conducted activity is read-only: {action} is not allowed.")


# --- Audit log (append-only: insert only, never update or delete) ------------------------


def _write_audit(activity: Activity, member: TBIMember | None, changes: list[tuple[str, object, object]]) -> None:
    """Insert one ACTIVITY_AUDIT_LOG row per (label, old value, new value). member None = System
    (an automatic status change)."""
    for label, old, new in changes:
        ActivityAuditLog.objects.create(
            activity=activity,
            changed_by=member,
            field_changed=label,
            old_value=format_audit_value(old),
            new_value=format_audit_value(new),
        )


def write_certificate_history(activity: Activity, member: TBIMember | None, old: str, new: str) -> None:
    """One History row about the activity's certificates (certificates.services): "3 created automatically (...)" by
    System (member None), or a certificate deleted because its attendance was undone (by the member)."""
    _write_audit(activity, member, [(CERTIFICATES_LABEL, old, new)])


def write_evaluation_link_history(activity: Activity, sent: int) -> None:
    """One History row by System when the automatic job emailed evaluation links (registrations.services,
    ASSUMPTIONS 419): "Evaluation Links": "" -> "Sent automatically to 12 attendees". The Evaluation tab reads these rows."""
    _write_audit(activity, None, [(EVALUATION_LINKS_LABEL, "", f"Sent automatically to {sent} attendee{'' if sent == 1 else 's'}")])


def write_registration_form_history(activity: Activity, member: TBIMember | None, old: str, new: str) -> None:
    """One History row about the registration form (registrations.services.mark_registration_form_ready, ASSUMPTIONS 409)."""
    from registrations.services import REGISTRATION_FORM_HISTORY_LABEL  # registrations.services imports this module

    _write_audit(activity, member, [(REGISTRATION_FORM_HISTORY_LABEL, old, new)])


def write_certificate_approval_history(activity: Activity, member: TBIMember | None, old: str, new: str) -> None:
    """One History row about the approval of the activity's certificates (certificates.services, ASSUMPTIONS 369):
    "Certificate approval": "Needs approval" -> "Approved by Ana Cruz", or the approval cancelled by a change."""
    from certificates.services import APPROVAL_HISTORY_LABEL

    _write_audit(activity, member, [(APPROVAL_HISTORY_LABEL, old, new)])


def write_certificate_text_history(activity: Activity, member: TBIMember | None, old: str, new: str) -> None:
    """One History row about the activity's certificate wording (ASSUMPTIONS 370): "Default text" -> "Custom text"."""
    from certificates.services import TEXT_HISTORY_LABEL

    _write_audit(activity, member, [(TEXT_HISTORY_LABEL, old, new)])


def write_document_history(activity: Activity, member: TBIMember | None, old: str, new: str) -> None:
    """One History row about one of the activity's documents (documents.services, ASSUMPTIONS 432): "Documents":
    "" -> 'Activity Design "Design v1" uploaded as an approved copy', or a replace / delete."""
    _write_audit(activity, member, [(DOCUMENTS_LABEL, old, new)])


def write_signatory_history(activity: Activity, member: TBIMember | None, label: str, old: str, new: str) -> None:
    """One History row about the activity's certificate signatories (certificates.services, ASSUMPTIONS 363):
    "Certificate signatory (left)": "<name> (<position>)" -> "<name> (<position>)"."""
    _write_audit(activity, member, [(label, old, new)])


@dataclass
class PageResult:
    """One page of results, plus what a pager needs."""

    items: list
    total: int
    page: int
    num_pages: int


def _paginate(queryset: QuerySet, page: int | str | None, per_page: int) -> PageResult:
    paginator = Paginator(queryset, per_page)
    current = paginator.get_page(page)  # bad or out-of-range page numbers give the nearest valid page
    return PageResult(list(current.object_list), paginator.count, current.number, paginator.num_pages)


def get_audit_log(
    activity: Activity,
    field: str | None = None,
    date_from: date | None = None,
    date_to: date | None = None,
    page: int | str | None = 1,
    per_page: int = DEFAULT_PAGE_SIZE,
) -> PageResult:
    """The Audit Log tab: newest first, optionally filtered by field and date (Manila time)."""
    rows = ActivityAuditLog.objects.filter(activity=activity).select_related("changed_by")
    if field:
        rows = rows.filter(field_changed=field)
    if date_from:
        rows = rows.filter(changed_at__date__gte=date_from)
    if date_to:
        rows = rows.filter(changed_at__date__lte=date_to)
    return _paginate(rows.order_by("-changed_at", "-log_id"), page, per_page)


RECENT_CHANGES_LIMIT = 5


def recent_audit_entries(limit: int = RECENT_CHANGES_LIMIT) -> list[ActivityAuditLog]:
    """The newest audit rows across all activities (the dashboard's "Recent changes"), with the
    member and the activity. Read only."""
    rows = ActivityAuditLog.objects.select_related("changed_by", "activity").order_by("-changed_at", "-log_id")
    return list(rows[:limit])


# --- Create and edit ----------------------------------------------------------------------

# Activity fields printed on the certificates (certificates.wording placeholders {activity}, {date}, {given_date},
# {venue}): changing one before Conducted cancels an early certificate approval (ASSUMPTIONS 420).
PRINTED_FIELDS = frozenset({"title", "start_date", "end_date", "venue"})


def _cancel_early_approval(activity: Activity, member: TBIMember, now: datetime | None, reason: str) -> None:
    """Cancel the activity's certificate approval when it was given before Conducted (ASSUMPTIONS 420; edits and
    resource person changes are refused after Conducted, so any approval here is an early one). Same transaction."""
    if activity.certificates_approved_at is None:
        return
    from certificates.services import cancel_certificate_approval  # certificates.services imports this module

    cancel_certificate_approval(activity, member, now, reason)


@transaction.atomic
def save_new_activity(
    member: TBIMember, activity: Activity, participant_categories, now: datetime | None = None,
    signatories: dict | None = None, creation_documents_required: bool = False,
    prepare_certificates: bool = False, certificate_texts: dict | None = None, budget_lines: list | None = None,
) -> Activity:
    """Validate and save an unsaved Activity as Draft with its categories, write its first audit row and notify the
    Director (notifications.services.notify_directors_of_new_activity).

    Used by create_activity() and by the Django admin's add form. `now` is only for tests
    and the demo seed (to create activities "as of" an earlier day); real calls leave it out.
    `signatories`: the New activity form's "Who signs the certificates" ({slot: (person or None, title)}); empty slots
    take the defaults (certificates.services.assign_default_signatories, ASSUMPTIONS 365, 366).
    `creation_documents_required` (ASSUMPTIONS 405): True from the New activity form
    (documents.services.create_activity_with_documents) and the JSON API; the admin, the demo seed and tests keep False.
    `prepare_certificates` (ASSUMPTIONS 420, the New activity form's "Prepare the certificates now"): True = the chosen
    `signatories` are saved, `certificate_texts` ({type: {field: text}}, None = the default text) become the activity's
    custom text (certificates.services.save_activity_text) and the certificates are prepared
    (prepare_activity_certificates: the reviewers are told). False = the default signatories and the default text,
    whatever `signatories` / `certificate_texts` hold (the flow before item 420).
    `budget_lines` (ASSUMPTIONS 433): the budget table (budget.clean_lines); budget_amount = their grand total (0.00
    without lines). Line problems are raised together with the fields' ones ("budget-<row>-<field>").
    A signatory or title choice needs can_approve_certificates (ASSUMPTIONS 431: only the Director chooses who signs;
    PermissionDenied otherwise); without a choice the defaults are used, so anyone who creates activities can still
    prepare the certificates and write their text.
    """
    require_permission(member, Permission.CREATE_ACTIVITY)
    if not prepare_certificates:
        signatories, certificate_texts = None, None
    if activity.budget_amount is None:  # the Django admin's add form: no lines yet
        activity.budget_amount = Decimal("0.00")
    from certificates.services import has_signatory_choice, require_signatory_choice  # imports this module

    if has_signatory_choice(signatories):
        require_signatory_choice(member)
    categories = _category_set(participant_categories)
    errors: dict = {}
    try:
        lines = budget.clean_lines(budget_lines)
    except ValidationError as error:
        errors.update(error.message_dict)
        lines = []
    try:
        validate_activity_values({**activity_values(activity), CATEGORIES: categories}, now=now)
    except ValidationError as error:
        errors = {**error.message_dict, **errors}
    if errors:
        raise ValidationError(errors)  # the fields and the budget rows together
    activity.budget_amount = budget.grand_total(lines)
    activity.created_by = member
    activity.status = ActivityStatus.objects.get(name=DRAFT)
    activity.creation_documents_required = creation_documents_required
    activity.save()
    activity.participant_categories.set(categories)
    _write_audit(activity, member, [(STATUS_LABEL, "", activity.status)])
    budget.save_lines(activity, lines, member, write_history=False)  # ASSUMPTIONS 433
    # Tell the Director (in-app, same transaction; not when a Director creates it; ASSUMPTIONS 361).
    from notifications.services import notify_directors_of_new_activity  # notifications.services imports this module

    notify_directors_of_new_activity(activity, member)
    # The certificate signatories: the form's choice, else the defaults (ASSUMPTIONS 363, 365), same transaction.
    from certificates.services import assign_default_signatories  # certificates.services imports this module

    assign_default_signatories(activity, signatories)
    if prepare_certificates:  # ASSUMPTIONS 420, same transaction
        from certificates.services import prepare_activity_certificates, save_activity_text

        if certificate_texts:
            save_activity_text(member, activity, certificate_texts, now=now)
        prepare_activity_certificates(member, activity, now=now)
    return activity


def create_activity(member: TBIMember, data: dict, now: datetime | None = None,
                    signatories: dict | None = None, creation_documents_required: bool = False,
                    prepare_certificates: bool = False, certificate_texts: dict | None = None) -> Activity:
    """Create a Draft activity. `data` holds Python values (model instances, dates, Decimal);
    `participant_categories` is a list of one or more ParticipantCategory.

    start_date and registration_deadline must not be in the past (see save_new_activity for `now`).
    `creation_documents_required`: see save_new_activity (ASSUMPTIONS 405); `prepare_certificates`,
    `certificate_texts`: see save_new_activity (ASSUMPTIONS 420).
    """
    require_permission(member, Permission.CREATE_ACTIVITY)
    values = _clean_values(data)
    categories = values.pop(CATEGORIES, frozenset())
    lines = values.pop(budget.LINES_KEY, None)
    return save_new_activity(member, Activity(**values), categories, now=now, signatories=signatories, budget_lines=lines,
                             creation_documents_required=creation_documents_required,
                             prepare_certificates=prepare_certificates, certificate_texts=certificate_texts)


@transaction.atomic
def update_activity(member: TBIMember, activity: Activity, data: dict, now: datetime | None = None,
                    signatories: dict | None = None) -> Activity:
    """Edit an activity. Saves only the fields that changed and writes one audit row for each.

    The edit and its audit rows are saved in one transaction: if anything fails, nothing is saved.
    `signatories`: the edit form's "Who signs the certificates" ({slot: (person or None, title)}), saved in the same
    transaction by certificates.services.set_activity_signatories (its History rows, certificates refreshed). Only
    with can_approve_certificates (ASSUMPTIONS 431); others pass None (the form shows the signatories read only).
    """
    require_permission(member, Permission.CREATE_ACTIVITY)
    changes = _clean_values(data)
    new_lines = changes.pop(budget.LINES_KEY, None)  # None = the budget lines stay as they are (ASSUMPTIONS 433)
    # Lock the row so two people editing at once cannot overwrite each other's old values.
    current = Activity.objects.select_for_update().select_related("status").get(pk=activity.pk)
    _ensure_not_conducted(current, "editing")

    old_values = activity_values(current)
    changed = [field for field, new in changes.items() if new != old_values[field]]
    errors: dict = {}
    lines = None
    if new_lines is not None:
        try:
            lines = budget.clean_lines(new_lines, allowed_inactive=budget.used_category_ids(current))
        except ValidationError as error:
            errors.update(error.message_dict)
    try:
        validate_activity_values({**old_values, **changes}, current, changed_fields=set(changed), now=now)
    except ValidationError as error:
        errors = {**error.message_dict, **errors}
    if errors:
        raise ValidationError(errors)

    if changed:
        columns = [field for field in changed if field != CATEGORIES]
        for field in columns:
            setattr(current, field, changes[field])
        current.save(update_fields=[*columns, "updated_at"])  # updated_at changes for categories too
        if CATEGORIES in changed:
            current.participant_categories.set(changes[CATEGORIES])
        _write_audit(
            current, member, [(FIELD_LABELS[field], old_values[field], changes[field]) for field in changed]
        )
        if PRINTED_FIELDS.intersection(changed):
            # Printed on the certificates ({activity}, {date}, {given_date}, {venue}): an approval given before
            # Conducted no longer matches what will print (ASSUMPTIONS 420).
            _cancel_early_approval(current, member, now, "activity details changed")
    if lines is not None:
        budget.save_lines(current, lines, member)  # History "Budget" / "Budget Amount" (ASSUMPTIONS 433)
    if signatories is not None:
        from certificates.services import set_activity_signatories  # certificates.services imports this module

        set_activity_signatories(member, current, signatories, now=now)
    activity.refresh_from_db()
    return activity


# --- Registration closed by hand (ASSUMPTIONS 427) -------------------------------------------


def registration_switch(activity: Activity) -> str | None:
    """Which hand switch the Share card offers: "close", "reopen" or None (not Scheduled). The permission is checked
    by the view and again by set_registration_closed_by_hand."""
    if activity.status.name != SCHEDULED:
        return None
    return "reopen" if activity.registration_closed_by_hand else "close"


@transaction.atomic
def set_registration_closed_by_hand(member: TBIMember, activity: Activity, closed: bool) -> Activity:
    """Close registration by hand (closed=True) or reopen it (False) on a Scheduled activity; one History row
    "Registration". Reopening only removes the hand closing: the deadline, the slots, Scheduled and a ready
    registration form still decide (registrations.services.registration_closed_reason)."""
    require_permission(member, Permission.CREATE_ACTIVITY)
    current = Activity.objects.select_for_update(of=("self",)).select_related("status").get(pk=activity.pk)
    if current.status.name != SCHEDULED:
        raise ActivityError("Registration can be closed or reopened by hand only while the activity is Scheduled.")
    if current.registration_closed_by_hand == closed:
        raise ActivityError("Registration is already closed by the TBI." if closed
                            else "Registration was not closed by hand.")
    current.registration_closed_by_hand = closed
    current.save(update_fields=["registration_closed_by_hand", "updated_at"])
    change = ("", REGISTRATION_CLOSED_VALUE) if closed else (REGISTRATION_CLOSED_VALUE, REGISTRATION_REOPENED_VALUE)
    _write_audit(current, member, [(REGISTRATION_LABEL, *change)])
    activity.refresh_from_db()
    return activity


# --- Status changes ------------------------------------------------------------------------


def allowed_next_statuses(activity: Activity) -> list[str]:
    """The statuses this activity can move to right now (what a "Change status" form offers).

    Same rules as change_status(): Draft is left out once anyone has registered.
    """
    current = activity.status.name
    allowed = set(ALLOWED_STATUS_MOVES[current])
    if current == SCHEDULED and registered_count(activity):
        allowed.discard(DRAFT)
    order = [DRAFT, SCHEDULED, ONGOING, CONDUCTED]
    return [name for name in order if name in allowed]


def _move_status(current: Activity, new_status: str, member: TBIMember | None, note: str = "") -> None:
    """The one status transition, for staff (change_status) and the system (member None). `current` must be
    locked (select_for_update). Checks ALLOWED_STATUS_MOVES, saves, and writes the audit row. `note`: why the System
    moved it, added to the History value ("Scheduled (both documents uploaded)", ASSUMPTIONS 431)."""
    target = ActivityStatus.objects.filter(name=new_status).first()
    if target is None:
        raise ActivityError(f'Unknown status "{new_status}".')
    old_name = current.status.name
    if old_name == CONDUCTED:
        raise ActivityError("A Conducted activity is final: its status cannot change.")
    if target.name not in ALLOWED_STATUS_MOVES[old_name]:
        allowed = " or ".join(sorted(ALLOWED_STATUS_MOVES[old_name]))
        raise ActivityError(f"Cannot change status from {old_name} to {target.name}. From {old_name} you can go to {allowed}.")
    if old_name == SCHEDULED and target.name == DRAFT and (count := registered_count(current)):
        raise ActivityError(
            f"Cannot move back to Draft: {count} {'person has' if count == 1 else 'people have'} already registered."
        )
    if old_name == DRAFT and target.name == SCHEDULED and current.creation_documents_required:
        # ASSUMPTIONS 405: uploaded is enough (waiting, returned or approved); approval is the Director's pace.
        from documents.services import scheduling_refused_reason  # documents.services imports this module

        if reason := scheduling_refused_reason(current):
            raise ActivityError(reason)
    old_status = current.status
    current.status = target
    current.save(update_fields=["status", "updated_at"])
    _write_audit(current, member, [(STATUS_LABEL, old_status, f"{target.name} ({note})" if note else target)])
    if target.name == CONDUCTED:  # the System creates the certificates (ASSUMPTIONS 362); never blocks the move
        from certificates.services import auto_generate_certificates

        auto_generate_certificates(current)


@transaction.atomic
def change_status(member: TBIMember, activity: Activity, new_status: str) -> Activity:
    """Move an activity to `new_status` (a status name) by hand. Only ALLOWED_STATUS_MOVES are possible;
    staff may also move Ongoing -> Conducted when no attendance was recorded."""
    require_permission(member, Permission.CREATE_ACTIVITY)
    current = Activity.objects.select_for_update(of=("self",)).select_related("status").get(pk=activity.pk)
    _move_status(current, new_status, member)
    activity.refresh_from_db()
    return activity


def schedule_automatically(activity: Activity, note: str) -> bool:
    """Draft -> Scheduled by the System (ASSUMPTIONS 431: both creation documents uploaded; the caller,
    documents.services.schedule_when_documents_complete, decides when). The same transition rule as change_status
    (_move_status, incl. scheduling_refused_reason), in a savepoint: a refusal raises ActivityError and changes nothing.
    False when the activity is no longer a Draft. History: "Status: Draft -> Scheduled (<note>)" by System."""
    with transaction.atomic():
        current = Activity.objects.select_for_update(of=("self",)).select_related("status").get(pk=activity.pk)
        if current.status.name != DRAFT:
            return False
        _move_status(current, SCHEDULED, None, note=note)
    activity.refresh_from_db()
    return True


def automatic_next_status(activity: Activity, now: datetime | None = None) -> str | None:
    """The status the system moves this activity to now, or None (ASSUMPTIONS 66):
    - Scheduled -> Ongoing once the start (activity_start_end) has arrived;
    - Ongoing -> Conducted once the end has passed AND at least one attendance is recorded. Without attendance it
      stays Ongoing (staff mark attendance, or change the status by hand).
    Draft and Conducted are never touched."""
    now = now or timezone.now()
    status = activity.status.name
    start, end = activity_start_end(activity)
    if status == SCHEDULED and now >= start:
        return ONGOING
    if status == ONGOING and now >= end and has_attendance(activity):
        return CONDUCTED
    return None


def _apply_automatic(activity_id: int, now: datetime) -> list[str]:
    """Make every automatic move one activity is due for (Scheduled -> Ongoing -> Conducted can happen in one run),
    each with its own audit row by "System". The row is locked with SKIP LOCKED: if staff (or another run) are
    changing it right now, it is left for the next run. Returns the statuses it moved to."""
    moved: list[str] = []
    with transaction.atomic():
        current = (
            Activity.objects.select_for_update(skip_locked=True, of=("self",)).select_related("status")
            .filter(pk=activity_id).first()
        )
        while current is not None and (target := automatic_next_status(current, now)):
            _move_status(current, target, None)
            moved.append(target)
    return moved


def apply_automatic_statuses(now: datetime | None = None) -> int:
    """Run the automatic status moves for every activity that may be due (the 5-minute Django-Q2 schedule and the
    Activities list). Only changes statuses: it never sends emails. Returns how many moves were made."""
    now = now or timezone.now()
    today = timezone.localdate(now)
    candidates = (
        Activity.objects.filter(start_date__lte=today)
        .filter(Q(status__name=SCHEDULED) | Q(status__name=ONGOING, end_date__lte=today))
        .order_by("pk")
        .values_list("pk", flat=True)
    )
    return sum(len(_apply_automatic(activity_id, now)) for activity_id in list(candidates))


def refresh_status(activity: Activity, now: datetime | None = None) -> bool:
    """The automatic status moves for one activity (the activity page, on every load). Reloads `activity` when
    its status changed. Never sends emails. Returns True if the status changed."""
    now = now or timezone.now()
    if activity.status.name not in (SCHEDULED, ONGOING) or automatic_next_status(activity, now) is None:
        return False
    if not _apply_automatic(activity.pk, now):
        return False
    activity.refresh_from_db()
    return True


# --- Resource persons --------------------------------------------------------------------


def create_resource_person(member: TBIMember, name: str, email: str = "", contact_number: str = "") -> ResourcePerson:
    """Add a resource person who is not in the system yet."""
    require_permission(member, Permission.CREATE_ACTIVITY)
    name, email = name.strip(), email.strip().lower()
    if not name:
        raise ValidationError({"name": "This field is required."})
    if email and ResourcePerson.objects.filter(email__iexact=email).exists():
        raise ActivityError("A resource person with this email already exists. Pick them from the list instead.")
    return ResourcePerson.objects.create(name=name, email=email, contact_number=contact_number.strip())


@transaction.atomic
def add_resource_person(
    member: TBIMember, activity: Activity, resource_person: ResourcePerson, topic: str = ""
) -> ActivityResourcePerson:
    """Link a resource person to an activity (once per activity) and write an audit row."""
    require_permission(member, Permission.CREATE_ACTIVITY)
    current = Activity.objects.select_for_update().select_related("status").get(pk=activity.pk)
    _ensure_not_conducted(current, "changing resource persons")
    if ActivityResourcePerson.objects.filter(activity=current, resource_person=resource_person).exists():
        raise ActivityError(f"{resource_person.name} is already a resource person for this activity.")
    link = ActivityResourcePerson.objects.create(activity=current, resource_person=resource_person, topic=topic.strip())
    _write_audit(current, member, [(RESOURCE_PERSON_ADDED, "", resource_person.name)])
    _cancel_early_approval(current, member, None, "resource persons changed")  # ASSUMPTIONS 420
    return link


@transaction.atomic
def remove_resource_person(member: TBIMember, activity: Activity, resource_person: ResourcePerson) -> None:
    """Unlink a resource person from an activity and write an audit row."""
    require_permission(member, Permission.CREATE_ACTIVITY)
    current = Activity.objects.select_for_update().select_related("status").get(pk=activity.pk)
    _ensure_not_conducted(current, "changing resource persons")
    link = ActivityResourcePerson.objects.filter(activity=current, resource_person=resource_person).first()
    if link is None:
        raise ActivityError(f"{resource_person.name} is not a resource person for this activity.")
    link.delete()
    _write_audit(current, member, [(RESOURCE_PERSON_REMOVED, resource_person.name, "")])
    _cancel_early_approval(current, member, None, "resource persons changed")  # ASSUMPTIONS 420


# --- Read services (any signed-in member) -------------------------------------------------

# Plain-list sorts. Every option ends with tie-breakers in the SAME direction (start time, then ID), so activities on
# the same date keep a stable order that matches the option (before, ties were always by ID ascending).
_START_ASC = ("start_date", F("start_time").asc(nulls_first=True))
_START_DESC = ("-start_date", F("start_time").desc(nulls_last=True))
SORT_OPTIONS = {
    "title": ("title", *_START_ASC, "activity_id"),
    "-title": ("-title", *_START_DESC, "-activity_id"),
    "start_date": (*_START_ASC, "activity_id"),
    "-start_date": (*_START_DESC, "-activity_id"),
    "registered": ("registered", *_START_ASC, "activity_id"),
    "-registered": ("-registered", *_START_DESC, "-activity_id"),
    "created_at": ("created_at", "activity_id"),
    "-created_at": ("-created_at", "-activity_id"),
}
DEFAULT_SORT = "-start_date"  # the JSON API's default; the Activities page defaults to GROUPED_SORT
GROUPED_SORT = "grouped"  # the Activities page only: grouped_activities() (sections), not a plain order


def _with_registered_count(queryset: QuerySet) -> QuerySet:
    return (
        queryset.select_related("status", "activity_type", "created_by")
        .prefetch_related("participant_categories")
        .annotate(registered=Count("registrations", distinct=True))
    )


def _overlapping(queryset: QuerySet, start: date | None, end: date | None) -> QuerySet:
    """Activities that touch the range [start, end] (either end may be open)."""
    if start:
        queryset = queryset.filter(end_date__gte=start)
    if end:
        queryset = queryset.filter(start_date__lte=end)
    return queryset


def list_activities(
    filters: dict | None = None, page: int | str | None = 1, per_page: int = DEFAULT_PAGE_SIZE, now: datetime | None = None
) -> PageResult:
    """The Activities list. Each item has `.registered` (count) to show with max_slots, e.g. 18/30.

    filters: search (title), activity_type, status, participant_category (IDs; an activity
             matches if the category is one of its categories),
             date_from, date_to (dates), sort (one of SORT_OPTIONS),
             preset (one of LIST_PRESETS: exactly the rows a list card counts; `now` is for tests).
    """
    filters = filters or {}
    sort = filters.get("sort") or DEFAULT_SORT
    if sort not in SORT_OPTIONS:
        raise ActivityError(f"Unknown sort \"{sort}\". Use one of: {', '.join(SORT_OPTIONS)}.")
    return _paginate(_filtered_activities(filters, now).order_by(*SORT_OPTIONS[sort]), page, per_page)


def activities_in_range(
    start: date | None, end: date | None, *, statuses: list[str] | None = None, activity_type: int | None = None
) -> QuerySet:
    """Activities that touch [start, end] (the Activities list's date filter, `_overlapping`), optionally only these
    status names and this activity type, earliest first (the list's "start_date" sort). The AI Assistant's activity
    answers use it (ASSUMPTIONS 381): the same rows the list shows for these dates."""
    activities = Activity.objects.select_related("status", "activity_type")
    if statuses is not None:
        activities = activities.filter(status__name__in=statuses)
    if activity_type:
        activities = activities.filter(activity_type_id=activity_type)
    return _overlapping(activities, start, end).order_by(*SORT_OPTIONS["start_date"])


def _filtered_activities(filters: dict, now: datetime | None = None) -> QuerySet:
    """The list filters (list_activities and grouped_activities share them), with `.registered`, unordered."""
    activities = Activity.objects.all()
    if filters.get("preset"):
        activities = preset_activities(filters["preset"], now)
    activities = _with_registered_count(activities)
    if filters.get("search"):
        activities = activities.filter(title__icontains=filters["search"].strip())
    for field in ("activity_type", "status"):
        if filters.get(field):
            activities = activities.filter(**{f"{field}_id": filters[field]})
    if filters.get("participant_category"):
        # An activity matches if it has the chosen category among its categories.
        # Exists() avoids a join, so each activity still appears once.
        activities = activities.filter(
            Exists(
                ActivityParticipantCategory.objects.filter(
                    activity=OuterRef("pk"), participant_category_id=filters["participant_category"]
                )
            )
        )
    return _overlapping(activities, filters.get("date_from"), filters.get("date_to"))


# --- The grouped Activities list (the page's default sort, ASSUMPTIONS 353) -----------------------------------

NOW, FOLLOW_UP, UPCOMING, DRAFTS, PAST = "now", "follow_up", "upcoming", "drafts", "past"
ACTIVITY_GROUPS = [
    (NOW, "Happening now"),
    (FOLLOW_UP, "Needs follow-up"),
    (UPCOMING, "Upcoming"),
    (DRAFTS, "Drafts"),
    (PAST, "Past"),
]


def activity_group(activity: Activity, now: datetime) -> str:
    """Which section of the grouped list an activity is in (activity_start_end and the automatic job's rules):
    - Conducted -> Past.
    - Ongoing: Happening now while is_happening_now() (= happening_now()); Needs follow-up once its end has passed
      (exactly the activities automatic_next_status() cannot conduct: no attendance yet); otherwise it starts on a
      later day (moved to Ongoing early by hand) -> Upcoming.
    - Draft: Needs follow-up once its start has arrived (it should be Scheduled or Ongoing by now); otherwise Drafts.
    - Scheduled -> Upcoming (the automatic move makes it Ongoing when the start arrives)."""
    status = activity.status.name
    start, end = activity_start_end(activity)
    if status == CONDUCTED:
        return PAST
    if status == ONGOING:
        if now >= end:
            return FOLLOW_UP
        return NOW if is_happening_now(activity, now) else UPCOMING
    if status == DRAFT:
        return FOLLOW_UP if now >= start else DRAFTS
    return UPCOMING


@dataclass
class ActivityGroup:
    """One section of the grouped list. `items` = the activities shown (with `.registered`); `total` = how many
    are in the section; `page` = the pager (Past only: the other sections show every activity)."""

    key: str
    label: str
    items: list
    total: int
    page: PageResult | None = None


def grouped_activities(
    filters: dict | None = None, page: int | str | None = 1, per_page: int = DEFAULT_PAGE_SIZE, now: datetime | None = None
) -> list[ActivityGroup]:
    """The Activities page's default view: the list filters (search, type, status, category, dates, preset) applied,
    then the activities split into ACTIVITY_GROUPS by activity_group(). Order inside a section: soonest start first
    (Happening now, Needs follow-up, Upcoming, Drafts); Past (Conducted) newest first, `per_page` a page. Empty
    sections are left out. Callers run apply_automatic_statuses() first (the list page does)."""
    now = now or timezone.now()
    filters = filters or {}
    activities = _filtered_activities(filters, now)
    sections: dict[str, list[Activity]] = {key: [] for key, _ in ACTIVITY_GROUPS if key != PAST}
    for activity in activities.exclude(status__name=CONDUCTED):
        sections[activity_group(activity, now)].append(activity)
    groups = []
    for key, label in ACTIVITY_GROUPS:
        if key == PAST:
            past = _paginate(activities.filter(status__name=CONDUCTED).order_by(*SORT_OPTIONS["-start_date"]), page, per_page)
            if past.total:
                groups.append(ActivityGroup(key, label, past.items, past.total, past))
            continue
        items = sorted(sections[key], key=lambda a: (activity_start_end(a)[0], a.title, a.pk))
        if items:
            groups.append(ActivityGroup(key, label, items, len(items)))
    return groups


def open_for_registration(queryset: QuerySet | None = None, now: datetime | None = None) -> QuerySet:
    """Activities open for registration now, as a database query: the ONE rule behind the Activities card and
    preset, the dashboard KPI, /register/ and the startup portal (registrations.services.open_activities).

    Scheduled, deadline not passed, slots left (= is_registration_open()) AND a registration form that counts
    (= registrations.services.registration_form_ready: exists, and marked ready on activities created with their
    documents, ASSUMPTIONS 409) (= registration_closed_reason() is None). A parity test keeps the two in step.
    """
    now = now or timezone.now()
    queryset = Activity.objects.all() if queryset is None else queryset
    has_form = FormTemplate.objects.filter(activity=OuterRef("pk"), form_type=FormType.REGISTRATION)
    ready_form = Exists(has_form.filter(ready_at__isnull=False)) | (Q(creation_documents_required=False) & Exists(has_form))
    registered = Registration.objects.filter(activity=OuterRef("pk")).values("activity").annotate(n=Count("pk")).values("n")
    return queryset.filter(
        ready_form,
        status__name=SCHEDULED,
        registration_closed_by_hand=False,
        registration_deadline__gte=now,
        max_slots__gt=Coalesce(Subquery(registered), 0),
    )


# The Activities list cards that are also list filters (?preset=...): key -> label. Each card's number and its
# list come from preset_activities(), so they always agree. "Activities in total" is the list without filters.
LIST_PRESETS = {
    "open": "Open for registration",
    "conducted_month": "Conducted this month",
    "conducted": "Conducted in total",
}


def preset_activities(preset: str, now: datetime | None = None) -> QuerySet:
    """The activities behind one list card (LIST_PRESETS)."""
    now = now or timezone.now()
    if preset == "open":
        return open_for_registration(now=now)
    conducted = Activity.objects.filter(status__name=CONDUCTED)
    if preset == "conducted":
        return conducted
    if preset == "conducted_month":
        # End date in the current Manila month (ASSUMPTIONS 77), not the date-range overlap filter.
        today = timezone.localdate(now)
        return conducted.filter(end_date__year=today.year, end_date__month=today.month)
    raise ActivityError(f"Unknown list preset \"{preset}\". Use one of: {', '.join(LIST_PRESETS)}.")


PRESET_LIST_LIMIT = 50


def preset_list(preset: str, limit: int = PRESET_LIST_LIMIT, now: datetime | None = None) -> tuple[int, list[Activity]]:
    """A list card's activities for its dialog: (count, at most `limit` rows with `.registered`), from
    preset_activities(), so the count is the card's number. Open: soonest start first; conducted: newest first."""
    activities = preset_activities(preset, now)
    order = ("start_date", "activity_id") if preset == "open" else ("-start_date", "-activity_id")
    return activities.count(), list(_with_registered_count(activities).order_by(*order)[:limit])


def activity_stats(now: datetime | None = None) -> dict[str, int]:
    """Numbers for the Activities list cards (the same queries as the list presets)."""
    now = now or timezone.now()
    return {
        "total": Activity.objects.count(),
        "conducted_this_month": preset_activities("conducted_month", now).count(),
        "open_for_registration": preset_activities("open", now).count(),
        "conducted_total": preset_activities("conducted", now).count(),
    }


RESOURCE_PERSON_SEARCH_LIMIT = 100


def search_resource_persons(search: str = "", limit: int = RESOURCE_PERSON_SEARCH_LIMIT) -> list[ResourcePerson]:
    """Resource persons whose name contains `search` (ignoring case), by name: the pick list for
    "add an existing person"."""
    people = ResourcePerson.objects.order_by("name", "resource_person_id")
    if search := search.strip():
        people = people.filter(name__icontains=search)
    return list(people[:limit])


def activities_between(start: date, end: date) -> list[Activity]:
    """Activities that touch [start, end], for the calendar view (all statuses)."""
    activities = Activity.objects.select_related("status", "activity_type")
    return list(_overlapping(activities, start, end).order_by("start_date", "activity_id"))


def get_activity_detail(activity: Activity) -> dict:
    """Everything the Overview tab needs, plus the IDs of its registration and evaluation forms
    (None until created) for the Forms tab."""
    count = registered_count(activity)
    categories = sorted(activity.participant_categories.all(), key=lambda category: category.name)
    links = activity.resource_person_links.select_related("resource_person").order_by("resource_person__name")
    form_ids = dict(activity.form_templates.values_list("form_type", "template_id"))  # one per type (constraint)
    return {
        "activity_id": activity.pk,
        "title": activity.title,
        "description": activity.description or "",
        "objectives": objective_lines(activity),  # ASSUMPTIONS 428
        "status": activity.status.name,
        "registration_closed_by_hand": activity.registration_closed_by_hand,  # ASSUMPTIONS 427
        "activity_type_id": activity.activity_type_id,
        "activity_type": activity.activity_type.name,
        "registration_form_id": form_ids.get("registration"),
        "evaluation_form_id": form_ids.get("evaluation"),
        "participant_category_ids": [category.pk for category in categories],
        "participant_categories": [category.name for category in categories],
        "start_date": activity.start_date,
        "end_date": activity.end_date,
        "start_time": activity.start_time,
        "end_time": activity.end_time,
        "time_text": format_time_range(activity.start_time, activity.end_time),
        "venue": activity.venue,
        "max_slots": activity.max_slots,
        "registered_count": count,
        "slots_remaining": max(activity.max_slots - count, 0),
        "is_registration_open": is_registration_open(activity),
        "registration_deadline": activity.registration_deadline,
        "evaluation_deadline": activity.evaluation_deadline,
        "budget_amount": activity.budget_amount,
        "budget": budget.budget_json(activity),  # ASSUMPTIONS 433: fund source, account, lines, totals
        "created_by": activity.created_by.name,
        "created_at": activity.created_at,
        "updated_at": activity.updated_at,
        "allowed_status_moves": allowed_next_statuses(activity),
        "resource_persons": [
            {
                "resource_person_id": link.resource_person.pk,
                "name": link.resource_person.name,
                "email": link.resource_person.email,
                "contact_number": link.resource_person.contact_number,
                "topic": link.topic,
            }
            for link in links
        ],
    }


# --- Participant categories (Admin Console master data, needs can_manage_users) -----------


def _name_key(name: str) -> str:
    """How category names are compared: ignoring upper/lower case and outer spaces."""
    return name.strip().casefold()


def is_seeded_category(category: ParticipantCategory) -> bool:
    """True for the standard categories loaded by seed_data (decided by name, not by ID)."""
    from accounts.seed import PARTICIPANT_CATEGORIES  # the seed list is the single source of these names

    return _name_key(category.name) in {_name_key(name) for name in PARTICIPANT_CATEGORIES}


def find_category_by_name(name: str) -> ParticipantCategory | None:
    """The category whose name matches, ignoring case and outer spaces."""
    return (
        ParticipantCategory.objects.annotate(key=Lower(Trim("name"))).filter(key=name.strip().lower()).first()
    )


def validate_category_name(name: str, category: ParticipantCategory | None = None) -> str:
    """Return the trimmed name, or raise ValidationError({"name": ...})."""
    name = (name or "").strip()
    if not name:
        raise ValidationError({"name": "This field is required."})
    if category is not None and category.pk and is_seeded_category(category):
        saved_name = ParticipantCategory.objects.get(pk=category.pk).name
        if name != saved_name:
            raise ValidationError({"name": f'"{saved_name}" is a standard category and cannot be renamed.'})
    existing = find_category_by_name(name)
    if existing is not None and (category is None or existing.pk != category.pk):
        raise ValidationError({"name": f'A participant category named "{existing.name}" already exists.'})
    return name


def save_participant_category(
    member: TBIMember, category: ParticipantCategory, name: str, description: str = ""
) -> ParticipantCategory:
    """Add a category (pass an unsaved one) or edit one. Names are trimmed and unique."""
    require_permission(member, Permission.MANAGE_USERS)
    category.name = validate_category_name(name, category)
    category.description = (description or "").strip()
    category.save()
    return category


def why_category_cannot_be_deleted(category: ParticipantCategory) -> str | None:
    """The reason a category cannot be deleted, or None if it can."""
    if is_seeded_category(category):
        return f'"{category.name}" is a standard category and cannot be deleted.'
    used_by = category.activities.count()
    if used_by:
        return (
            f'Cannot delete "{category.name}": it is used by {used_by} '
            f"{'activity' if used_by == 1 else 'activities'}. Change those activities to another category first."
        )
    registrations = category.registrations.count()
    if registrations:
        return (
            f'Cannot delete "{category.name}": {registrations} '
            f"{'registration uses' if registrations == 1 else 'registrations use'} it."
        )
    return None


def delete_participant_category(member: TBIMember, category: ParticipantCategory) -> None:
    """Delete an Admin-added category that no activity uses (for example, a typo)."""
    require_permission(member, Permission.MANAGE_USERS)
    if reason := why_category_cannot_be_deleted(category):
        raise ActivityError(reason)
    category.delete()
