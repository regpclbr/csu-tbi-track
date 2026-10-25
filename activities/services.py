"""Business logic for activities, status changes, resource persons and the audit log.

Views (and the Django admin) stay thin and call the functions in this module, so every
change to an activity is validated the same way and always writes the audit log.
"""

from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal

from django.core.exceptions import ObjectDoesNotExist, ValidationError
from django.core.paginator import Paginator
from django.db import transaction
from django.db.models import Count, Exists, F, OuterRef, QuerySet
from django.db.models.functions import Lower, Trim
from django.utils import timezone

from accounts.models import TBIMember
from accounts.permissions import Permission, require_permission

from .formatting import format_audit_value
from .models import (
    Activity,
    ActivityAuditLog,
    ActivityParticipantCategory,
    ActivityResourcePerson,
    ActivityStatus,
    ParticipantCategory,
    ResourcePerson,
)

DRAFT, SCHEDULED, ONGOING, CONDUCTED = (
    ActivityStatus.DRAFT,
    ActivityStatus.SCHEDULED,
    ActivityStatus.ONGOING,
    ActivityStatus.CONDUCTED,
)

# Status changes are always manual. These are the only moves allowed; Conducted is final.
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
    "activity_type": "Activity Type",
    "participant_categories": "Participant Categories",
    "start_date": "Start Date",
    "end_date": "End Date",
    "time_schedule": "Time Schedule",
    "venue": "Venue",
    "max_slots": "Max Slots",
    "registration_deadline": "Registration Deadline",
    "evaluation_deadline": "Evaluation Deadline",
    "budget_amount": "Budget Amount",
}
EDITABLE_FIELDS = tuple(FIELD_LABELS)
CATEGORIES = "participant_categories"  # many-to-many (ACTIVITY_PARTICIPANT_CATEGORY), not a column
SCALAR_FIELDS = tuple(field for field in EDITABLE_FIELDS if field != CATEGORIES)  # columns on ACTIVITY
REQUIRED_FIELDS = tuple(field for field in SCALAR_FIELDS if field != "description")
TEXT_FIELDS = ("title", "description", "time_schedule", "venue")

STATUS_LABEL = "Status"
RESOURCE_PERSON_ADDED = "Resource Person Added"
RESOURCE_PERSON_REMOVED = "Resource Person Removed"

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
    """Open only when the activity is Scheduled, the deadline has not passed, and slots remain."""
    now = now or timezone.now()
    return (
        activity.status.name == SCHEDULED
        and now <= activity.registration_deadline
        and slots_remaining(activity) > 0
    )


# --- Validation ---------------------------------------------------------------------------


def _local_date(value: datetime) -> date:
    return timezone.localtime(value).date() if timezone.is_aware(value) else value.date()


def _clean_values(data: dict) -> dict:
    """Strip text fields and treat an empty description as no description."""
    unknown = set(data) - set(EDITABLE_FIELDS)
    if unknown:
        raise ActivityError(f"These fields cannot be set: {', '.join(sorted(unknown))}.")
    cleaned = dict(data)
    for field in TEXT_FIELDS:
        if isinstance(cleaned.get(field), str):
            cleaned[field] = cleaned[field].strip()
    if cleaned.get("description") == "":
        cleaned["description"] = None
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
    if _local_date(values["evaluation_deadline"]) < end:
        errors["evaluation_deadline"] = "Evaluation deadline must be on or after the end date."

    max_slots = values["max_slots"]
    if max_slots < 1:
        errors["max_slots"] = "Max slots must be at least 1."
    elif activity is not None and max_slots < (count := registered_count(activity)):
        errors["max_slots"] = f"Max slots cannot be lower than the {count} people already registered."

    if Decimal(values["budget_amount"]) < 0:
        errors["budget_amount"] = "Budget amount must be zero or more."

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


def _write_audit(activity: Activity, member: TBIMember, changes: list[tuple[str, object, object]]) -> None:
    """Insert one ACTIVITY_AUDIT_LOG row per (label, old value, new value)."""
    for label, old, new in changes:
        ActivityAuditLog.objects.create(
            activity=activity,
            changed_by=member,
            field_changed=label,
            old_value=format_audit_value(old),
            new_value=format_audit_value(new),
        )


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


@transaction.atomic
def save_new_activity(
    member: TBIMember, activity: Activity, participant_categories, now: datetime | None = None
) -> Activity:
    """Validate and save an unsaved Activity as Draft with its categories, and write its first audit row.

    Used by create_activity() and by the Django admin's add form. `now` is only for tests
    and the demo seed (to create activities "as of" an earlier day); real calls leave it out.
    """
    require_permission(member, Permission.CREATE_ACTIVITY)
    categories = _category_set(participant_categories)
    validate_activity_values({**activity_values(activity), CATEGORIES: categories}, now=now)
    activity.created_by = member
    activity.status = ActivityStatus.objects.get(name=DRAFT)
    activity.save()
    activity.participant_categories.set(categories)
    _write_audit(activity, member, [(STATUS_LABEL, "", activity.status)])
    return activity


def create_activity(member: TBIMember, data: dict, now: datetime | None = None) -> Activity:
    """Create a Draft activity. `data` holds Python values (model instances, dates, Decimal);
    `participant_categories` is a list of one or more ParticipantCategory.

    start_date and registration_deadline must not be in the past (see save_new_activity for `now`).
    """
    require_permission(member, Permission.CREATE_ACTIVITY)
    values = _clean_values(data)
    categories = values.pop(CATEGORIES, frozenset())
    return save_new_activity(member, Activity(**values), categories, now=now)


@transaction.atomic
def update_activity(member: TBIMember, activity: Activity, data: dict, now: datetime | None = None) -> Activity:
    """Edit an activity. Saves only the fields that changed and writes one audit row for each.

    The edit and its audit rows are saved in one transaction: if anything fails, nothing is saved.
    """
    require_permission(member, Permission.CREATE_ACTIVITY)
    changes = _clean_values(data)
    # Lock the row so two people editing at once cannot overwrite each other's old values.
    current = Activity.objects.select_for_update().select_related("status").get(pk=activity.pk)
    _ensure_not_conducted(current, "editing")

    old_values = activity_values(current)
    changed = [field for field, new in changes.items() if new != old_values[field]]
    validate_activity_values({**old_values, **changes}, current, changed_fields=set(changed), now=now)

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
    activity.refresh_from_db()
    return activity


# --- Status changes (always manual) -------------------------------------------------------


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


@transaction.atomic
def change_status(member: TBIMember, activity: Activity, new_status: str) -> Activity:
    """Move an activity to `new_status` (a status name). Only ALLOWED_STATUS_MOVES are possible."""
    require_permission(member, Permission.CREATE_ACTIVITY)
    current = Activity.objects.select_for_update().select_related("status").get(pk=activity.pk)
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

    old_status = current.status
    current.status = target
    current.save(update_fields=["status", "updated_at"])
    _write_audit(current, member, [(STATUS_LABEL, old_status, target)])
    activity.refresh_from_db()
    return activity


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


# --- Read services (any signed-in member) -------------------------------------------------

SORT_OPTIONS = {
    "title": ("title",),
    "-title": ("-title",),
    "start_date": ("start_date",),
    "-start_date": ("-start_date",),
    "registered": ("registered",),
    "-registered": ("-registered",),
    "created_at": ("created_at",),
    "-created_at": ("-created_at",),
}
DEFAULT_SORT = "-start_date"


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


def list_activities(filters: dict | None = None, page: int | str | None = 1, per_page: int = DEFAULT_PAGE_SIZE) -> PageResult:
    """The Activities list. Each item has `.registered` (count) to show with max_slots, e.g. 18/30.

    filters: search (title), activity_type, status, participant_category (IDs; an activity
             matches if the category is one of its categories),
             date_from, date_to (dates), sort (one of SORT_OPTIONS).
    """
    filters = filters or {}
    sort = filters.get("sort") or DEFAULT_SORT
    if sort not in SORT_OPTIONS:
        raise ActivityError(f"Unknown sort \"{sort}\". Use one of: {', '.join(SORT_OPTIONS)}.")

    activities = _with_registered_count(Activity.objects.all())
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
    activities = _overlapping(activities, filters.get("date_from"), filters.get("date_to"))
    return _paginate(activities.order_by(*SORT_OPTIONS[sort], "activity_id"), page, per_page)


def _open_for_registration(queryset: QuerySet, now: datetime) -> QuerySet:
    """Same rule as is_registration_open(), as a database query."""
    return queryset.annotate(registered=Count("registrations", distinct=True)).filter(
        status__name=SCHEDULED, registration_deadline__gte=now, registered__lt=F("max_slots")
    )


def activity_stats(now: datetime | None = None) -> dict[str, int]:
    """Numbers for the Activities list cards."""
    now = now or timezone.now()
    today = timezone.localdate(now)
    conducted = Activity.objects.filter(status__name=CONDUCTED)
    return {
        "total": Activity.objects.count(),
        "conducted_this_month": conducted.filter(end_date__year=today.year, end_date__month=today.month).count(),
        "open_for_registration": _open_for_registration(Activity.objects.all(), now).count(),
        "conducted_total": conducted.count(),
    }


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
        "status": activity.status.name,
        "activity_type_id": activity.activity_type_id,
        "activity_type": activity.activity_type.name,
        "registration_form_id": form_ids.get("registration"),
        "evaluation_form_id": form_ids.get("evaluation"),
        "participant_category_ids": [category.pk for category in categories],
        "participant_categories": [category.name for category in categories],
        "start_date": activity.start_date,
        "end_date": activity.end_date,
        "time_schedule": activity.time_schedule,
        "venue": activity.venue,
        "max_slots": activity.max_slots,
        "registered_count": count,
        "slots_remaining": max(activity.max_slots - count, 0),
        "is_registration_open": is_registration_open(activity),
        "registration_deadline": activity.registration_deadline,
        "evaluation_deadline": activity.evaluation_deadline,
        "budget_amount": activity.budget_amount,
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
