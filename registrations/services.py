"""Business logic for forms, public registration, attendance and evaluations.

Views stay thin and call the functions in this module.
"""

import json
import logging
import re
from dataclasses import dataclass
from datetime import datetime, time, timedelta

from django.conf import settings
from django.core import signing
from django.core.exceptions import ValidationError
from django.core.validators import validate_email
from django.db import transaction
from django.db.models import Max, Q, QuerySet
from django.urls import reverse
from django.utils import timezone

from accounts.models import TBIMember
from accounts.permissions import Permission, has_permission, require_permission, require_permission_for
from activities.formatting import format_date, format_datetime, format_time, format_time_range
from activities.models import Activity, ActivityStatus, ParticipantCategory
from activities.services import activity_start_end, is_registration_open, open_for_registration, slots_remaining
from config.mail import is_blocked_address, prints_emails
from config.parsing import parse_bool
from config.ratelimit import count_attempt

from .models import (
    DefaultFormField,
    EvaluationSubmission,
    FieldType,
    FormAnswer,
    FormField,
    FormTemplate,
    FormType,
    Registration,
)

security_log = logging.getLogger("accounts.security")  # the same logger as the login lockout
logger = logging.getLogger("registrations")
# Every attendance mark and undo: activity, registration and member IDs only (never names or emails).
audit_log = logging.getLogger("registrations.audit")

CHOICE_TYPES = (FieldType.MULTIPLE_CHOICE, FieldType.CHECKBOX)
SHORT_TEXT_MAX = 255
CERTIFICATE_NAME_MAX = 150  # EVALUATION_SUBMISSION.certificate_name
CERTIFICATE_EMAIL_MAX = 254
LONG_TEXT_MAX = 5000
RATING_RANGE = range(1, 6)  # 1 to 5

# Name, email, contact number and category are REGISTRATION columns, never form fields.
RESERVED_LABELS = {"name", "full name", "email", "email address", "contact number", "participant category", "category",
                   # the fixed REGISTRATION columns of ASSUMPTIONS 426
                   "designation", "position", "designation / position", "office", "agency", "office / agency"}
# The two fixed fields every new registration needs (ASSUMPTIONS 426): REGISTRATION.designation / office_agency.
FIXED_TEXT_MAX = 255
DESIGNATION_LABEL = "Designation / Position"
OFFICE_AGENCY_LABEL = "Office / Agency"

ATTENDANCE_STATUSES = (ActivityStatus.ONGOING, ActivityStatus.CONDUCTED)
EVALUATION_SALT = "registrations.evaluation-link"

# The public registration form has this extra field, hidden from people by the frontend (CSS, not
# type="hidden"). People leave it empty; simple bots fill in every field.
HONEYPOT_FIELD = "website"
HONEYPOT_MESSAGE = "Your registration could not be accepted."

CONFIRMATION_TASK = "registrations.tasks.run_send_registration_confirmation"
EVALUATION_LINK_TASK = "registrations.tasks.run_send_evaluation_link"


class RegistrationError(Exception):
    """An action was refused. The message is safe to show (it never reveals other registrants)."""


class RegistrationRateLimited(RegistrationError):
    """Too many public registration attempts from one IP address or for one email (HTTP 429)."""


# --- Background emails (Django-Q2 tasks in registrations/tasks.py) -------------------------------


def queue_confirmation(registration_id: int) -> None:
    from django_q.tasks import async_task

    async_task(CONFIRMATION_TASK, registration_id, task_name=f"Registration confirmation {registration_id}")


def queue_evaluation_link(registration_id: int) -> None:
    from django_q.tasks import async_task

    async_task(EVALUATION_LINK_TASK, registration_id, task_name=f"Evaluation link {registration_id}")


# --- Public registration guard: rate limit and honeypot ------------------------------------------


def _count_attempt(kind: str, value: str, limit: int) -> bool:
    """One public registration attempt, counted by the shared limiter (config/ratelimit.py)."""
    return count_attempt("registration", kind, value, limit, settings.REGISTRATION_RATE_WINDOW_MINUTES)


def guard_public_registration(ip: str, email, honeypot) -> None:
    """Checks for a public registration POST, before registering (never for the startup portal).

    - Rate limit: at most REGISTRATION_MAX_ATTEMPTS_PER_IP attempts per IP address and
      REGISTRATION_MAX_ATTEMPTS_PER_EMAIL per email (any activity) in REGISTRATION_RATE_WINDOW_MINUTES.
      Every attempt counts, also refused ones. Counted in the same cache as the login lockout.
    - Honeypot: a filled-in HONEYPOT_FIELD is refused with a neutral message and nothing is saved.
    Both are logged with the IP address only (never the email).
    """
    email = str(email or "").strip().lower()
    ip_ok = _count_attempt("ip", ip, settings.REGISTRATION_MAX_ATTEMPTS_PER_IP)
    email_ok = _count_attempt("email", email, settings.REGISTRATION_MAX_ATTEMPTS_PER_EMAIL) if email else True
    if not (ip_ok and email_ok):
        security_log.warning("Public registration rate limit reached (ip=%s, by %s).", ip, "ip" if not ip_ok else "email")
        raise RegistrationRateLimited(
            f"Too many registration attempts. Please try again in {settings.REGISTRATION_RATE_WINDOW_MINUTES} minutes."
        )
    if str(honeypot or "").strip():
        security_log.warning("Public registration refused: the hidden field was filled in (ip=%s).", ip)
        raise RegistrationError(HONEYPOT_MESSAGE)


# --- Privacy notice consent (public registration and evaluation, ASSUMPTIONS 394) -----------------
# A required checkbox, checked here so the HTML pages, the JSON endpoints and the startup portal all refuse the same way.
# Nothing is stored (no column, no ERD change): a registration or evaluation can only exist when it was ticked.

PRIVACY_CONSENT_FIELD = "privacy_consent"
PRIVACY_CONSENT_REQUIRED = "Please confirm that you have read the privacy notice and agree to it."
_CONSENT_VALUES = {"yes", "true", "on", "1"}


def has_privacy_consent(value) -> bool:
    """True for a ticked checkbox ("yes" / "on") or JSON true; anything else (missing, empty, false) is no consent."""
    if value is True:
        return True
    return isinstance(value, str) and value.strip().lower() in _CONSENT_VALUES


def check_privacy_consent(value) -> None:
    """Refuse (ValidationError on PRIVACY_CONSENT_FIELD) unless the privacy notice checkbox was ticked. Call it before
    `register` (public page, /api/register/, startup portal) and `submit_evaluation` (evaluation page, /api/evaluate/)."""
    if not has_privacy_consent(value):
        raise ValidationError({PRIVACY_CONSENT_FIELD: [PRIVACY_CONSENT_REQUIRED]})


# --- Field options (stored as a JSON list in FORM_FIELD.options) --------------------------


def parse_options(raw: str) -> list[str]:
    """The options stored in a field ('' means no options)."""
    return json.loads(raw) if raw else []


# --- One participant's evaluation (ASSUMPTIONS 402) -------------------------------------------------------------------
# Answers shown WITH the participant's name only to staff who can create activities (owner decision 2026-11-17: the
# Director, both Project Heads, Admin Staff). Everyone else, the System Administrator included, sees only the
# evaluation summary without names (reports.services.evaluation_summary_file).

INDIVIDUAL_EVALUATION_PERMISSION = Permission.CREATE_ACTIVITY


def can_view_individual_evaluations(member) -> bool:
    return has_permission(member, INDIVIDUAL_EVALUATION_PERMISSION)


def evaluation_answers(member: TBIMember, registration: Registration) -> dict:
    """One registration's submitted evaluation (ASSUMPTIONS 402, 404): name, category, name on the certificate, when,
    the rating questions in form order ("ratings": question, value 1-5 or None when unanswered, maximum), the
    participant's average rating ("average", 2 decimals via reports.metrics.participant_average; None without ratings)
    and the other answers in form order with the Comments last ("answers": label, answer; empty ones left out,
    checkbox answers joined). Never the email or the contact number. PermissionDenied without can_create_activity;
    EvaluationSubmission.DoesNotExist when the registration has not evaluated (the views answer 404)."""
    from reports.metrics import participant_average

    require_permission(member, INDIVIDUAL_EVALUATION_PERMISSION)
    submission = EvaluationSubmission.objects.select_related("registration__participant_category").get(registration=registration)
    answers = sorted(submission.answers.select_related("field"), key=lambda answer: (answer.field.sort_order, answer.field_id))
    ratings, others = [], []
    for answer in answers:
        value = (answer.answer_value or "").strip()
        if answer.field.field_type == FieldType.RATING:
            number = int(value) if value.isdigit() and int(value) in RATING_RANGE else None
            ratings.append({"question": answer.field.field_label, "value": number, "maximum": RATING_RANGE[-1]})
            continue
        if answer.field.field_type == FieldType.CHECKBOX:
            value = ", ".join(parse_options(value))
        if value:
            others.append({"label": answer.field.field_label, "answer": value})
    if comments := submission.comments.strip():
        others.append({"label": "Comments", "answer": comments})
    return {
        "registration": registration,
        "name": registration.name,
        "category": submission.registration.participant_category.name,
        "certificate_name": submission.certificate_name,
        "submitted_at": submission.submitted_at,
        "ratings": ratings,
        "average": participant_average([row["value"] for row in ratings if row["value"] is not None]),
        "answers": others,
    }


def clean_options(field_type: str, options) -> str:
    """Validate options for a field type and return them as stored text (a JSON list or '')."""
    if field_type not in CHOICE_TYPES:
        if options:
            raise ValidationError({"options": "Only multiple choice and checkbox fields have options."})
        return ""
    if isinstance(options, str):
        try:
            options = json.loads(options) if options.strip() else []
        except json.JSONDecodeError as error:
            raise ValidationError({"options": 'Options must be a JSON list, e.g. ["Yes", "No"].'}) from error
    if not isinstance(options, list) or not all(isinstance(option, str) for option in options):
        raise ValidationError({"options": 'Options must be a list of text, e.g. ["Yes", "No"].'})
    options = [option.strip() for option in options]
    if not options or any(not option for option in options):
        raise ValidationError({"options": "Give at least one option, and no empty options."})
    if len({option.casefold() for option in options}) != len(options):
        raise ValidationError({"options": "Options must not repeat."})
    return json.dumps(options, ensure_ascii=False)


# --- Answer validation (the single place answers are checked) ----------------------------


def _answer_key(field: FormField) -> str:
    return str(field.pk)


def validate_answers(form: FormTemplate, raw_answers: dict | None) -> dict[FormField, str]:
    """Check answers for every field of `form` and return {field: stored value}.

    `raw_answers` maps field IDs (as text or numbers) to values. Stored values:
    text as typed (trimmed), rating "1".."5", multiple choice the chosen option,
    checkbox a JSON list of the chosen options (in the form's order). Unanswered
    optional fields are left out. Raises ValidationError keyed "field_<id>".
    """
    raw_answers = {str(key): value for key, value in (raw_answers or {}).items()}
    fields = list(form.fields.order_by("sort_order", "field_id"))
    unknown = set(raw_answers) - {_answer_key(field) for field in fields}
    if unknown:
        raise ValidationError({"answers": f"Unknown field IDs for this form: {', '.join(sorted(unknown))}."})

    errors: dict[str, str] = {}
    stored: dict[FormField, str] = {}
    for field in fields:
        value = raw_answers.get(_answer_key(field))
        try:
            result = _check_answer(field, value)
        except ValidationError as error:
            errors[f"field_{field.pk}"] = error.messages[0]
            continue
        if result is not None:
            stored[field] = result
    if errors:
        raise ValidationError(errors)
    return stored


def _is_empty(value) -> bool:
    return value is None or (isinstance(value, str) and not value.strip()) or (isinstance(value, list) and not value)


def _check_answer(field: FormField, value) -> str | None:
    if _is_empty(value):
        if field.is_required:
            raise ValidationError("This field is required.")
        return None

    if field.field_type in (FieldType.SHORT_TEXT, FieldType.LONG_TEXT):
        if not isinstance(value, str):
            raise ValidationError("Enter text.")
        limit = SHORT_TEXT_MAX if field.field_type == FieldType.SHORT_TEXT else LONG_TEXT_MAX
        if len(value.strip()) > limit:
            raise ValidationError(f"Keep this under {limit} characters.")
        return value.strip()

    if field.field_type == FieldType.RATING:
        number = value if isinstance(value, int) and not isinstance(value, bool) else None
        if isinstance(value, str) and value.strip().isdigit():
            number = int(value.strip())
        if number not in RATING_RANGE:
            raise ValidationError("Choose a rating from 1 to 5.")
        return str(number)

    options = parse_options(field.options)
    if field.field_type == FieldType.MULTIPLE_CHOICE:
        if value not in options:
            raise ValidationError("Choose one of the given options.")
        return value

    # Checkbox: a list of chosen options.
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        raise ValidationError("Choose from the given options.")
    if any(item not in options for item in value):
        raise ValidationError("Choose only from the given options.")
    return json.dumps([option for option in options if option in value], ensure_ascii=False)


# --- Forms (needs can_create_activity) ---------------------------------------------------


def get_form(activity: Activity, form_type: str) -> FormTemplate | None:
    return activity.form_templates.filter(form_type=form_type).first()


def form_is_locked(form: FormTemplate) -> bool:
    """A form is locked once anyone has submitted it (a registration or an evaluation)."""
    return form.registrations.exists() or form.evaluation_submissions.exists() or FormAnswer.objects.filter(
        field__template=form
    ).exists()


FORM_LOCKED = "This form already has answers, so its fields are locked: they cannot be added, edited or removed."


FORM_HISTORY_LABELS = {FormType.REGISTRATION: "Registration form", FormType.EVALUATION: "Evaluation form"}


def _form_history(form: FormTemplate, member: TBIMember, old: str, new: str) -> None:
    """One History row on the form's activity for a field change (ASSUMPTIONS 437: who and when, also when the Director
    edits a form another member made)."""
    from activities.services import write_form_history

    write_form_history(form.activity, member, FORM_HISTORY_LABELS.get(form.form_type, "Form"), old, new)


def _field_text(field: FormField) -> str:
    required = "required" if field.is_required else "optional"
    options = f", options: {', '.join(field.options)}" if field.options else ""
    return f'"{field.field_label}" ({FieldType(field.field_type).label.lower()}, {required}{options})'


def _ensure_unlocked(form: FormTemplate) -> None:
    if form_is_locked(form):
        raise RegistrationError(FORM_LOCKED)


@transaction.atomic
def create_form(member: TBIMember, activity: Activity, form_type: str, title: str = "") -> FormTemplate:
    """Create the activity's registration or evaluation form, copying the default fields
    for the activity's type (generated_from_default is true when any were copied)."""
    require_permission_for(member, Permission.CREATE_ACTIVITY, activity)  # or its assignment (437)
    if form_type not in FormType.values:
        raise RegistrationError(f'Unknown form type "{form_type}". Use registration or evaluation.')
    Activity.objects.select_for_update().get(pk=activity.pk)  # one form per type, even if two clicks race
    if get_form(activity, form_type):
        raise RegistrationError(f"This activity already has a {FormType(form_type).label.lower()} form.")

    defaults = list(
        DefaultFormField.objects.filter(activity_type=activity.activity_type, form_type=form_type).order_by(
            "sort_order", "default_field_id"
        )
    )
    form = FormTemplate.objects.create(
        activity=activity,
        form_type=form_type,
        title=title.strip() or f"{activity.title} - {FormType(form_type).label}",
        generated_from_default=bool(defaults),
        created_by=member,
    )
    for number, default in enumerate(defaults, start=1):
        FormField.objects.create(
            template=form,
            field_label=default.field_label,
            field_type=default.field_type,
            options=default.options,
            is_required=default.is_required,
            sort_order=number,
        )
    _form_history(form, member, "", f"Created ({len(defaults)} default field{'' if len(defaults) == 1 else 's'})")
    return form


def _clean_field(form: FormTemplate, label: str, field_type: str, options, exclude: FormField | None = None) -> tuple[str, str]:
    label = (label or "").strip()
    if not label:
        raise ValidationError({"field_label": "This field is required."})
    if len(label) > 255:
        raise ValidationError({"field_label": "Keep the label under 255 characters."})
    if field_type not in FieldType.values:
        raise ValidationError({"field_type": f"Choose one of: {', '.join(FieldType.values)}."})
    if form.form_type == FormType.REGISTRATION and label.casefold() in RESERVED_LABELS:
        raise ValidationError(
            {"field_label": f'"{label}" is already asked on every registration (name, email, contact number, category).'}
        )
    return label, clean_options(field_type, options)


@transaction.atomic
def add_field(
    member: TBIMember, form: FormTemplate, *, field_label: str, field_type: str, options=None, is_required: bool = True
) -> FormField:
    require_permission_for(member, Permission.CREATE_ACTIVITY, form.activity)
    _ensure_unlocked(form)
    label, stored_options = _clean_field(form, field_label, field_type, options)
    last = form.fields.aggregate(last=Max("sort_order"))["last"] or 0
    field = FormField.objects.create(
        template=form, field_label=label, field_type=field_type, options=stored_options,
        is_required=parse_bool(is_required, "is_required", default=True), sort_order=last + 1,
    )
    _form_history(form, member, "", f"Field {_field_text(field)} added")
    return field


@transaction.atomic
def update_field(member: TBIMember, field: FormField, **changes) -> FormField:
    """Change a field's label, type, options or is_required (refused once the form has answers)."""
    require_permission_for(member, Permission.CREATE_ACTIVITY, field.template.activity)
    allowed = {"field_label", "field_type", "options", "is_required"}
    if unknown := set(changes) - allowed:
        raise RegistrationError(f"These cannot be changed: {', '.join(sorted(unknown))}.")
    _ensure_unlocked(field.template)
    before = _field_text(field)
    label = changes.get("field_label", field.field_label)
    field_type = changes.get("field_type", field.field_type)
    if "options" in changes:
        options = changes["options"]
    elif field_type in CHOICE_TYPES:
        options = field.options  # keep the current options (already a JSON list)
    else:
        options = None  # e.g. changing a checkbox into short text drops its options
    field.field_label, field.options = _clean_field(field.template, label, field_type, options)
    field.field_type = field_type
    field.is_required = parse_bool(changes.get("is_required"), "is_required", default=field.is_required)
    field.save()
    if (after := _field_text(field)) != before:
        _form_history(field.template, member, f"Field {before}", f"Field {after}")
    return field


@transaction.atomic
def remove_field(member: TBIMember, field: FormField) -> None:
    require_permission_for(member, Permission.CREATE_ACTIVITY, field.template.activity)
    _ensure_unlocked(field.template)
    text = _field_text(field)
    field.delete()
    _form_history(field.template, member, f"Field {text}", "Removed")


@transaction.atomic
def reorder_fields(member: TBIMember, form: FormTemplate, field_ids: list[int]) -> None:
    """Put the fields in the given order. Allowed even after answers (order does not change meaning)."""
    require_permission_for(member, Permission.CREATE_ACTIVITY, form.activity)
    fields = {field.pk: field for field in form.fields.all()}
    try:
        ids = [int(field_id) for field_id in field_ids]
    except (TypeError, ValueError) as error:
        raise RegistrationError("field_ids must be a list of numbers.") from error
    if sorted(ids) != sorted(fields):
        raise RegistrationError("Give every field of this form exactly once.")
    old_order = [fields[pk].field_label for pk in sorted(fields, key=lambda pk: (fields[pk].sort_order, pk))]
    for number, field_id in enumerate(ids, start=1):
        FormField.objects.filter(pk=field_id).update(sort_order=number)
    new_order = [fields[pk].field_label for pk in ids]
    if new_order != old_order:
        _form_history(form, member, "Order: " + ", ".join(old_order), "Order: " + ", ".join(new_order))


# --- Public registration (no login) ------------------------------------------------------


def get_public_activity(activity_id: int) -> Activity | None:
    """Only Scheduled activities are public. Anything else (including Draft) is 'not found'."""
    return (
        Activity.objects.select_related("status", "activity_type")
        .filter(pk=activity_id, status__name=ActivityStatus.SCHEDULED)
        .first()
    )


# The registration page of an Ongoing or Conducted activity (phase F3): the title, dates and venue with this note.
REGISTRATION_ENDED = "Registration for this activity has closed."


def registration_page_activity(activity_id: int) -> Activity | None:
    """The activity the public registration PAGE may show (phase F3, read only): Scheduled ones as before
    (get_public_activity), and Ongoing / Conducted ones, whose page shows only the title, dates and venue
    with REGISTRATION_ENDED. Draft is never public (None, like an unknown ID). The JSON endpoint keeps
    get_public_activity (Scheduled only)."""
    return (
        Activity.objects.select_related("status", "activity_type")
        .filter(pk=activity_id)
        .exclude(status__name=ActivityStatus.DRAFT)
        .first()
    )


REGISTRATION_FORM_NOT_READY = "Registration is not open yet: the registration form is not ready."
REGISTRATION_CLOSED_BY_TBI = "Registration was closed by the TBI."  # closed by hand (ASSUMPTIONS 427)
REGISTRATION_FORM_HISTORY_LABEL = "Registration form"
REGISTRATION_FORM_READY_VALUE = "Marked ready (registration can open)"
FORM_ALREADY_READY = "This registration form is already marked as ready."


def registration_form_ready(activity: Activity, form: FormTemplate | None) -> bool:
    """The registration form counts (ASSUMPTIONS 409): it exists and, on an activity created with its documents
    (creation_documents_required, ASSUMPTIONS 405), staff marked it ready (ready_at). Older activities: it exists (the
    rule before). activities.services.open_for_registration is the same rule as a query (parity test)."""
    if form is None:
        return False
    return form.ready_at is not None or not activity.creation_documents_required


def registration_form_needs_ready(activity: Activity, form: FormTemplate | None) -> bool:
    """The form exists but still waits for "Open registration" (the Forms tab button, Needs attention, the Share card)."""
    return form is not None and not registration_form_ready(activity, form)


@transaction.atomic
def mark_registration_form_ready(member: TBIMember, activity: Activity, now: datetime | None = None) -> FormTemplate:
    """"Open registration" on the Forms tab (ASSUMPTIONS 409): staff checked the registration form's fields; from now on
    it counts for registration (the activity still has to be Scheduled, before the deadline and with slots left).
    can_create_activity. One History row ("Registration form": "" -> "Marked ready …"). Not undone: the fields stay
    editable until the first registration, and Scheduled -> Draft closes registration while nobody has registered."""
    require_permission_for(member, Permission.CREATE_ACTIVITY, activity)
    Activity.objects.select_for_update().get(pk=activity.pk)
    form = get_form(activity, FormType.REGISTRATION)
    if form is None:
        raise RegistrationError("Create the registration form first.")
    if form.ready_at is not None:
        raise RegistrationError(FORM_ALREADY_READY)
    form.ready_at = now or timezone.now()
    form.save(update_fields=["ready_at", "updated_at"])
    from activities.services import write_registration_form_history

    write_registration_form_history(activity, member, "", REGISTRATION_FORM_READY_VALUE)
    return form


def registration_closed_reason(activity: Activity, now: datetime | None = None) -> str | None:
    """None when registration is open; otherwise why it is closed.

    The open/closed rule itself is activities.services.is_registration_open() (one place);
    this adds the "form exists (and is marked ready, ASSUMPTIONS 409)" check and explains which part failed.
    """
    now = now or timezone.now()
    has_form = registration_form_ready(activity, get_form(activity, FormType.REGISTRATION))
    if is_registration_open(activity, now) and has_form:
        return None
    if activity.status.name != ActivityStatus.SCHEDULED:
        return "Registration is not open for this activity."
    if activity.registration_closed_by_hand:  # ASSUMPTIONS 427 (activities.services.set_registration_closed_by_hand)
        return REGISTRATION_CLOSED_BY_TBI
    if now > activity.registration_deadline:
        return f"Registration is closed: the deadline ({format_datetime(activity.registration_deadline)}) has passed."
    if slots_remaining(activity) <= 0:
        return "Registration is closed: all slots are taken."
    return REGISTRATION_FORM_NOT_READY


def _field_json(field: FormField) -> dict:
    return {
        "field_id": field.pk,
        "field_label": field.field_label,
        "field_type": field.field_type,
        "options": parse_options(field.options),
        "is_required": field.is_required,
        "sort_order": field.sort_order,
    }


def public_activity_summary(activity: Activity) -> dict:
    """The public facts of an activity: the same fields the registration page shows, without the
    form. Never who registered."""
    return {
        "activity_id": activity.pk,
        "title": activity.title,
        "description": activity.description or "",
        "activity_type": activity.activity_type.name,
        "start_date": activity.start_date,
        "end_date": activity.end_date,
        "start_time": activity.start_time,
        "end_time": activity.end_time,
        "time_text": format_time_range(activity.start_time, activity.end_time),
        "venue": activity.venue,
        "registration_deadline": activity.registration_deadline,
        "slots_remaining": slots_remaining(activity),
        "participant_categories": [
            {"participant_category_id": c.pk, "name": c.name} for c in activity.participant_categories.order_by("name")
        ],
    }


def public_activity_info(activity: Activity, now: datetime | None = None) -> dict:
    """What the public registration page shows. Never who registered."""
    form = get_form(activity, FormType.REGISTRATION)
    reason = registration_closed_reason(activity, now)
    return {
        **public_activity_summary(activity),
        "is_open": reason is None,
        "closed_reason": reason,
        "fields": [_field_json(field) for field in form.fields.order_by("sort_order", "field_id")] if form else [],
        # Render this field hidden from people (CSS, not type="hidden") and send it empty.
        "honeypot_field": HONEYPOT_FIELD,
    }


def open_activities(now: datetime | None = None) -> list[Activity]:
    """Activities open for registration right now, soonest first: Scheduled, deadline not passed,
    slots left and a registration form (registration_closed_reason is None). The query is
    activities.services.open_for_registration(), the one rule shared with the Activities card and
    preset and the dashboard KPI. Used by the public list and the startup portal."""
    return list(
        open_for_registration(now=now).select_related("status", "activity_type").order_by("start_date", "activity_id")
    )


def _choose_category(activity: Activity, category_id) -> ParticipantCategory:
    """The registrant's category: one of the activity's categories (auto-picked if there is only one)."""
    categories = list(activity.participant_categories.order_by("name"))
    if category_id in (None, ""):
        if len(categories) == 1:
            return categories[0]
        raise ValidationError({"participant_category": "Choose the category you are attending as."})
    chosen = next((c for c in categories if str(c.pk) == str(category_id)), None)
    if chosen is None:
        names = ", ".join(c.name for c in categories)
        raise ValidationError({"participant_category": f"Choose one of this activity's categories: {names}."})
    return chosen


def clean_registrant(
    activity: Activity, *, name, email, contact_number="", designation="", office_agency="", participant_category=None,
) -> dict:
    """The person's fixed fields, checked the same way for a registration and a walk-in (registrations.attendance):
    name and email required, Designation / Position and Office / Agency required (ASSUMPTIONS 426), one of the
    activity's categories. Returns the Registration column values; raises one ValidationError with every problem."""
    errors: dict[str, str] = {}
    name, email, contact_number = (name or "").strip(), (email or "").strip().lower(), (contact_number or "").strip()
    if not name:
        errors["name"] = "This field is required."
    designation, office_agency = _fixed_text(designation), _fixed_text(office_agency)
    for key, value in (("designation", designation), ("office_agency", office_agency)):
        if not value:
            errors[key] = "This field is required."
        elif len(value) > FIXED_TEXT_MAX:
            errors[key] = f"At most {FIXED_TEXT_MAX} characters."
    try:
        validate_email(email)
    except ValidationError:
        errors["email"] = "Enter a valid email address."
    category = None
    try:
        category = _choose_category(activity, participant_category)
    except ValidationError as error:
        errors.update({key: messages[0] for key, messages in error.message_dict.items()})
    if errors:
        raise ValidationError(errors)
    return {"name": name, "email": email, "contact_number": contact_number, "designation": designation,
            "office_agency": office_agency, "participant_category": category}


@transaction.atomic
def register(
    activity: Activity,
    *,
    name: str,
    email: str,
    contact_number: str = "",
    designation: str = "",
    office_agency: str = "",
    participant_category=None,
    answers: dict | None = None,
    now: datetime | None = None,
    startup=None,
    signature: dict | None = None,
) -> Registration:
    """Public registration ("Will attend", ASSUMPTIONS 434). Everyone who registers is in (no approval step).

    `designation` (Designation / Position) and `office_agency` (Office / Agency) are required fixed fields of every
    registration (ASSUMPTIONS 426): the public page and API, the startup portal and any later entry point.

    `signature` (ASSUMPTIONS 434): the raw signature fields (registrations.signatures.from_post / from_json). Every public
    entry point (registration page and API, startup portal page and API) passes it, even empty, so it is REQUIRED there
    and checked together with the other fields; None = not asked (Django admin, demo seed, tests), like the privacy
    consent, which the entry points check before calling this.

    `startup` is set only by the startup portal (a logged-in startup registering; source "portal"); every
    other rule is exactly the same as for a public registration.

    The activity row is locked first, so two people cannot take the last slot at the same time. The registration, its
    answers and its signature are saved together; the confirmation email is sent only after the save succeeds.
    """
    from . import signatures

    locked = Activity.objects.select_for_update().select_related("status").get(pk=activity.pk)
    if reason := registration_closed_reason(locked, now):
        raise RegistrationError(reason)

    errors: dict[str, str] = {}
    values: dict = {}
    try:
        values = clean_registrant(locked, name=name, email=email, contact_number=contact_number,
                                  designation=designation, office_agency=office_agency,
                                  participant_category=participant_category)
    except ValidationError as error:
        errors.update({key: messages[0] for key, messages in error.message_dict.items()})
    form = get_form(locked, FormType.REGISTRATION)
    try:
        stored_answers = validate_answers(form, answers)
    except ValidationError as error:
        errors.update({key: messages[0] for key, messages in error.message_dict.items()})
    clean_signature = None
    if signature is not None:
        try:
            clean_signature = signatures.clean_signature(signature)
        except ValidationError as error:
            errors.update({key: messages[0] for key, messages in error.message_dict.items()})
    if errors:
        raise ValidationError(errors)
    if locked.registrations.filter(email__iexact=values["email"]).exists():
        raise RegistrationError("This email is already registered for this activity.")

    source = Registration.Source.PORTAL if startup is not None else Registration.Source.ONLINE
    registration = Registration.objects.create(
        activity=locked, template=form, startup=startup, registration_source=source, **values,
    )
    for field, value in stored_answers.items():
        FormAnswer.objects.create(
            field=field, answer_source=FormAnswer.AnswerSource.REGISTRATION, registration=registration, answer_value=value
        )
    if clean_signature is not None:
        signatures.save_signature(registration, clean_signature, now)
    # The confirmation email is a background task queued after the commit. robust=True: a problem
    # queueing it never turns the saved registration into an error (the task itself never raises).
    registration_id = registration.pk
    transaction.on_commit(lambda: queue_confirmation(registration_id), robust=True)
    return registration


def _fixed_text(value) -> str:
    """A fixed text field (designation, office / agency): inner spaces collapsed, ends trimmed."""
    return " ".join(str(value or "").split())


# --- Attendance (staff, needs can_create_activity) -----------------------------------------

def attendance_opens_text(activity: Activity) -> str:
    """"Nov 6, 2026, 8:00 AM" (or only the date when the activity has no times)."""
    if activity.start_time is None:
        return format_date(activity.start_date)
    return f"{format_date(activity.start_date)}, {format_time(activity.start_time)}"


def attendance_closed_reason(activity: Activity) -> str | None:
    """None when attendance can be recorded now (Ongoing or Conducted); otherwise why not. The Registrations tab
    shows this same text instead of the buttons, so the page and the service always agree."""
    if activity.status.name in ATTENDANCE_STATUSES:
        return None
    return f"Attendance opens when the activity starts on {attendance_opens_text(activity)}."


@transaction.atomic
def set_attendance(
    member: TBIMember,
    activity: Activity,
    registration_ids: list[int],
    attended: bool,
    method: str | None = None,
    now: datetime | None = None,
    *,
    day=None,
    signature: dict | None = None,
) -> int:
    """Staff mark (or undo) ONE DAY's attendance for one or many registrations (ASSUMPTIONS 434; replaces item 111's
    one-off mark). Returns how many registrations were handled. Needs can_create_activity; Ongoing or Conducted only.

    `day`: a date (or "YYYY-MM-DD") of the activity up to today; default registrations.attendance.default_staff_day.
    `method`: None or "staff" (email / wet_signature exist only on rows copied by migration registrations/0012).
    `signature`: draw or typed, collected on the staff device for one person who has none (owner decision b).
    Undoing someone's LAST day: refused after an evaluation or an approved certificate; a certificate still waiting is
    deleted (History row). Marking someone on a Conducted activity creates their certificate (ASSUMPTIONS 362).
    All or nothing. Every mark and undo is logged on registrations.audit (IDs only). The rules live in
    registrations.attendance.mark_by_staff."""
    from .attendance import mark_by_staff

    return mark_by_staff(member, activity, registration_ids, attended, day=day, method=method, signature=signature,
                         now=now)


UNDO_REFUSED_EVALUATED = "Already evaluated: attendance can no longer be undone."
UNDO_REFUSED_CERTIFICATE = "Certificate approved: attendance can no longer be undone."


def undo_refused_reason(registration: Registration) -> str | None:
    """Why this person's attendance cannot be undone (the row's Undo button is hidden with this text), or None.
    The same rules set_attendance checks: a submitted evaluation, or a certificate that is no longer waiting for
    approval (a waiting one is deleted with the undo; evidence documents are checked there too)."""
    if not registration.attendance_confirmed:
        return None
    try:
        registration.evaluation_submission  # no query when list_registrations loaded it (select_related)
    except EvaluationSubmission.DoesNotExist:
        pass
    else:
        return UNDO_REFUSED_EVALUATED
    # .all(): no query when list_registrations prefetched them
    if any(c.status != c.Status.GENERATED for c in registration.certificates.all()):
        return UNDO_REFUSED_CERTIFICATE
    return None


def list_registrations(
    activity: Activity, attended: bool | None = None, participant_category_id: int | None = None, search: str = ""
) -> QuerySet:
    """Staff list of an activity's registrations by name, with category, startup, evaluation and answers.
    `search` matches the name, the email or the office / agency (ignoring case)."""
    registrations = (
        activity.registrations.select_related(
            "participant_category", "startup", "evaluation_submission", "signature"
        )
        .prefetch_related("answers__field", "certificates", "attendance_days__marked_by")
        .order_by("name", "registration_id")
    )
    if search := (search or "").strip():
        registrations = registrations.filter(Q(name__icontains=search) | Q(email__icontains=search) | Q(office_agency__icontains=search))
    if attended is not None:
        registrations = registrations.filter(attendance_confirmed=attended)
    if participant_category_id:
        registrations = registrations.filter(participant_category_id=participant_category_id)
    return registrations


# --- Evaluation (personal signed link, no login) -----------------------------------------


def _signer() -> signing.Signer:
    return signing.Signer(salt=EVALUATION_SALT)


def evaluation_token(registration: Registration) -> str:
    """A link token signed with SECRET_KEY. It cannot be guessed or changed to another person's.
    The same person always gets the same link (no timestamp); the evaluation window decides
    when it works."""
    return _signer().sign_object(registration.pk)


def evaluation_link(registration: Registration) -> str:
    """One person's personal evaluation link (SITE_URL, never the request's Host)."""
    return settings.SITE_URL + reverse("evaluate", args=[evaluation_token(registration)])


def registration_from_token(token: str) -> Registration:
    try:
        registration_id = _signer().unsign_object(token)
        return Registration.objects.select_related("activity__status").get(pk=registration_id)
    except (signing.BadSignature, Registration.DoesNotExist, TypeError, ValueError) as error:
        raise RegistrationError("This evaluation link is not valid.") from error


def evaluation_window(activity: Activity) -> tuple[datetime, datetime | None]:
    """(opens, closes): opens at 00:00 Manila time on the end date, closes at the deadline. `closes` is None when the
    activity has no evaluation deadline: the evaluation then stays open (ASSUMPTIONS 423)."""
    opens = timezone.make_aware(datetime.combine(activity.end_date, time(0, 0)))
    return opens, activity.evaluation_deadline


def evaluation_has_closed(activity: Activity, now: datetime | None = None) -> bool:
    """True once the evaluation deadline has passed; never without a deadline (ASSUMPTIONS 423). The one check every
    "closed" rule uses (links, Needs attention, certificate readiness)."""
    deadline = activity.evaluation_deadline
    return deadline is not None and (now or timezone.now()) > deadline


def evaluation_open_q(now: datetime) -> Q:
    """Activities whose evaluation has not closed at `now` (no deadline, or the deadline not passed): the query
    version of evaluation_has_closed."""
    return Q(evaluation_deadline__isnull=True) | Q(evaluation_deadline__gte=now)


def _window_reason(activity: Activity, now: datetime) -> str | None:
    opens, closes = evaluation_window(activity)
    if now < opens:
        return f"The evaluation opens on {format_datetime(opens)}."
    if evaluation_has_closed(activity, now):
        return f"The evaluation closed on {format_datetime(closes)}."
    return None


NOT_ATTENDED = "Only people marked as attended can evaluate this activity."
ALREADY_SUBMITTED = "You have already submitted your evaluation. Thank you!"
EVALUATION_FORM_NOT_READY = "The evaluation form is not ready yet."


@dataclass(frozen=True)
class EvaluationState:
    """Where one person's evaluation link stands (evaluation_state).

    kind: "open", "not_attended", "submitted", "no_form", "not_yet_open" or "closed".
    reason: None when open; otherwise the message to show.
    shows_person: whether the link may still show the registrant's name and email (not after the
    evaluation is submitted or the window has closed: a forwarded or old email should not reveal them).
    """

    kind: str
    reason: str | None
    shows_person: bool


def evaluation_state(registration: Registration, now: datetime | None = None) -> EvaluationState:
    """The one rule for an evaluation link, checked in this order: attended, not submitted yet,
    a form exists, inside the window. evaluation_closed_reason, evaluation_page and the HTML page use it."""
    now = now or timezone.now()
    activity = registration.activity
    submitted = EvaluationSubmission.objects.filter(registration=registration).exists()
    shows_person = not submitted and not evaluation_has_closed(activity, now)
    if not registration.attendance_confirmed:
        return EvaluationState("not_attended", NOT_ATTENDED, shows_person)
    if submitted:
        return EvaluationState("submitted", ALREADY_SUBMITTED, shows_person)
    if get_form(activity, FormType.EVALUATION) is None:
        return EvaluationState("no_form", EVALUATION_FORM_NOT_READY, shows_person)
    reason = _window_reason(activity, now)
    if reason is None:
        return EvaluationState("open", None, shows_person)
    kind = "not_yet_open" if now < evaluation_window(activity)[0] else "closed"
    return EvaluationState(kind, reason, shows_person)


def evaluation_closed_reason(registration: Registration, now: datetime | None = None) -> str | None:
    """None when this person can submit the evaluation now; otherwise why not."""
    return evaluation_state(registration, now).reason


def evaluation_page(token: str, now: datetime | None = None) -> dict:
    """What the evaluation page shows for a personal link.

    Once the evaluation is submitted or the window has closed, the link is of no more use: it then
    shows only why it is closed, never the registrant's name or email (a forwarded or old email
    should not reveal them).
    """
    registration = registration_from_token(token)
    form = get_form(registration.activity, FormType.EVALUATION)
    state = evaluation_state(registration, now)
    if not state.shows_person:
        return {"is_open": False, "closed_reason": state.reason}
    return {
        "activity": registration.activity.title,
        "name": registration.name,
        # Suggested defaults for the certificate name and email fields (the participant may change them).
        "suggested_certificate_name": registration.name,
        "suggested_certificate_email": registration.email,
        "is_open": state.reason is None,
        "closed_reason": state.reason,
        "fields": [_field_json(field) for field in form.fields.order_by("sort_order", "field_id")] if form else [],
    }


def clean_certificate_details(certificate_name, certificate_email) -> tuple[str, str]:
    """The name printed on the certificate and the address it is emailed to.

    Name: outer spaces trimmed, required, at most 150 characters, capitalization kept as typed.
    Email: trimmed, valid format, at most 254 characters, stored in lower case.
    """
    errors: dict[str, str] = {}
    name = str(certificate_name or "").strip()
    email = str(certificate_email or "").strip().lower()
    if not name:
        errors["certificate_name"] = "Enter the name to print on your certificate."
    elif len(name) > CERTIFICATE_NAME_MAX:
        errors["certificate_name"] = f"The name can be at most {CERTIFICATE_NAME_MAX} characters."
    if not email:
        errors["certificate_email"] = "Enter the email address to send your certificate to."
    elif len(email) > CERTIFICATE_EMAIL_MAX:
        errors["certificate_email"] = f"The email address can be at most {CERTIFICATE_EMAIL_MAX} characters."
    else:
        try:
            validate_email(email)
        except ValidationError:
            errors["certificate_email"] = "Enter a valid email address."
    if errors:
        raise ValidationError(errors)
    return name, email


def check_evaluation(
    form: FormTemplate, certificate_name, certificate_email, answers: dict | None
) -> tuple[str, str, dict[FormField, str]]:
    """Check an evaluation without saving anything: (certificate name, certificate email, stored answers).

    The only check of an evaluation's contents: submit_evaluation calls it, and so does the review step of
    the evaluation page (phase F3), which shows the result before the participant confirms. Raises one
    ValidationError with the certificate errors and the answer errors together.
    """
    errors: dict[str, str] = {}
    name = email = ""
    stored_answers: dict[FormField, str] = {}
    try:
        name, email = clean_certificate_details(certificate_name, certificate_email)
    except ValidationError as error:
        errors.update({key: messages[0] for key, messages in error.message_dict.items()})
    try:
        stored_answers = validate_answers(form, answers)
    except ValidationError as error:
        errors.update({key: messages[0] for key, messages in error.message_dict.items()})
    if errors:
        raise ValidationError(errors)
    return name, email, stored_answers


def submit_evaluation(
    token: str,
    *,
    certificate_name: str,
    certificate_email: str,
    comments: str = "",
    answers: dict | None = None,
    attachments=None,
    now: datetime | None = None,
) -> EvaluationSubmission:
    """Save the evaluation and its answers together. Never anonymous: tied to the registration.

    The participant also gives the name printed on their certificate and the address it is
    emailed to (Step 9). After the save, an Approved certificate is issued and emailed.

    attachments (ASSUMPTIONS 435): the optional photos / PDFs chosen on the review step (uploaded files; None or [] =
    none). Checked by registrations.evaluation_photos.clean_attachments (ValidationError key "photos") before anything
    is saved, stored in the same transaction; if it fails, the files already written are removed.
    """
    from . import evaluation_photos

    written: list[str] = []
    try:
        return _submit_evaluation(token, certificate_name, certificate_email, comments, answers, attachments, now, written)
    except BaseException:
        evaluation_photos.remove_files(written)
        raise


@transaction.atomic
def _submit_evaluation(token, certificate_name, certificate_email, comments, answers, attachments, now, written):
    from . import evaluation_photos

    registration = registration_from_token(token)
    registration = Registration.objects.select_for_update().get(pk=registration.pk)  # one submission, even on double click
    if reason := evaluation_closed_reason(registration, now):
        raise RegistrationError(reason)
    form = get_form(registration.activity, FormType.EVALUATION)
    name, email, stored_answers = check_evaluation(form, certificate_name, certificate_email, answers)
    cleaned_files = evaluation_photos.clean_attachments(attachments)
    submission = EvaluationSubmission.objects.create(
        template=form,
        registration=registration,
        comments=(comments or "").strip(),
        certificate_name=name,
        certificate_email=email,
    )
    for field, value in stored_answers.items():
        FormAnswer.objects.create(
            field=field, answer_source=FormAnswer.AnswerSource.EVALUATION, evaluation_submission=submission, answer_value=value
        )
    evaluation_photos.store_attachments(submission, cleaned_files, written, now)
    # Step 9: if the certificate is already Approved, issue and email it now (background task).
    # robust=True: a problem queueing the task never undoes the evaluation (the daily retry catches it).
    from certificates.services import evaluation_submitted

    registration_id = registration.pk
    transaction.on_commit(lambda: evaluation_submitted(registration_id), robust=True)
    return submission


def evaluation_links_closed_reason(activity: Activity, now: datetime | None = None) -> str | None:
    """None when evaluation links can be sent now (an evaluation form exists and the window is open)."""
    if get_form(activity, FormType.EVALUATION) is None:
        return "This activity has no evaluation form yet."
    if reason := _window_reason(activity, now or timezone.now()):
        return f"Links cannot be sent now. {reason}"
    return None


def evaluation_link_recipients(activity: Activity) -> QuerySet:
    """Who gets an evaluation link: attendees who have not evaluated yet."""
    return activity.registrations.filter(attendance_confirmed=True, evaluation_submission__isnull=True)


def send_evaluation_links(
    member: TBIMember, activity: Activity, registration_ids: list[int] | None = None, now: datetime | None = None
) -> int:
    """Resend: queue one email task per attendee who has not evaluated yet (their personal link), or only for
    `registration_ids` (the Evaluation tab's per-person "Resend link"). Returns how many were queued; the
    emails go out in the background (deliver_evaluation_link marks each one sent or failed).

    Refused if the activity has no evaluation form or the evaluation window is not open, and (with
    registration_ids) if one of them is not an attendee who still has to evaluate.
    """
    require_permission(member, Permission.CREATE_ACTIVITY)
    if reason := evaluation_links_closed_reason(activity, now):
        raise RegistrationError(reason)
    recipients = evaluation_link_recipients(activity)
    if registration_ids is not None:
        wanted = {int(registration_id) for registration_id in registration_ids}
        recipients = recipients.filter(pk__in=wanted)
        if not wanted or recipients.count() != len(wanted):
            raise RegistrationError("A link can be resent only to attendees who have not evaluated yet.")
    ids = list(recipients.order_by("pk").values_list("pk", flat=True))
    for registration_id in ids:
        queue_evaluation_link(registration_id)
    return len(ids)


def deliver_evaluation_link(registration_id: int, first_time_only: bool = False, now: datetime | None = None) -> str:
    """Email one attendee their personal evaluation link and record it: evaluation_link_sent_at, or
    evaluation_link_failed_at when sending fails (setting one clears the other). Runs inside qcluster (the
    queued task and the automatic job). Never raises: a failure is logged with the registration ID and the
    error type only (never the address or the link).

    The row is locked while sending, so two runs never email the same person at the same time.
    first_time_only (the automatic job): skip anyone already sent a link or whose link failed, and skip a row
    another run is busy with (SKIP LOCKED). Returns "sent", "failed", "already submitted", "already sent" or
    "skipped" (not found, or locked by another run).

    evaluation_link_delivery records whether a "sent" email really left (SENT) or was only printed in the terminal:
    PRINTED_DEV while the mailer is the console one (config.mail.prints_emails), PRINTED_DEMO for a blocked demo
    address (config.mail.is_blocked_address, which GuardedSMTPBackend prints instead of sending)."""
    from . import emails

    with transaction.atomic():
        rows = Registration.objects.select_for_update(of=("self",), skip_locked=first_time_only)
        registration = rows.select_related("activity").filter(pk=registration_id).first()
        if registration is None:
            return "skipped"
        if EvaluationSubmission.objects.filter(registration=registration).exists():
            return "already submitted"
        if first_time_only and (registration.evaluation_link_sent_at or registration.evaluation_link_failed_at):
            return "already sent"
        try:
            emails.send_evaluation_link(registration, evaluation_link(registration))
        except Exception as error:  # the mail server said no: record it, staff can resend
            logger.error("Evaluation link for registration %s was not sent: %s.", registration_id, type(error).__name__)
            registration.evaluation_link_sent_at, registration.evaluation_link_failed_at = None, now or timezone.now()
            registration.evaluation_link_delivery = None
            result = "failed"
        else:
            registration.evaluation_link_sent_at, registration.evaluation_link_failed_at = now or timezone.now(), None
            registration.evaluation_link_delivery = _delivery(registration.email)
            result = "sent"
        registration.save(update_fields=[
            "evaluation_link_sent_at", "evaluation_link_failed_at", "evaluation_link_delivery", "updated_at",
        ])
    return result


def _delivery(address: str) -> str:
    """How an email that the mailer accepted went out (Registration.LinkDelivery)."""
    Delivery = Registration.LinkDelivery
    if prints_emails():
        return Delivery.PRINTED_DEV
    return Delivery.PRINTED_DEMO if is_blocked_address(address) else Delivery.SENT


def automatic_link_moment(activity: Activity) -> datetime:
    """From when an Ongoing activity's attendees get their first evaluation link automatically (ASSUMPTIONS 419):
    the end minus EVALUATION_LINK_HOURS_BEFORE_END hours. Without an end time the activity counts as ending at 23:59
    of the end date (NOT activity_start_end()'s 00:00 of the next day, which the status moves use). The evaluation
    window still applies on top: nothing is sent before 00:00 on the end date."""
    end_time = activity.end_time if activity.end_time is not None else time(23, 59)
    end = timezone.make_aware(datetime.combine(activity.end_date, end_time))
    return end - timedelta(hours=settings.EVALUATION_LINK_HOURS_BEFORE_END)


def automatic_link_activities(now: datetime | None = None) -> list[Activity]:
    """Activities whose attendees get their first evaluation link automatically now: Conducted, or Ongoing from
    automatic_link_moment() on (ASSUMPTIONS 419); in both cases an evaluation form exists and the evaluation window
    is open (evaluation_links_closed_reason is None)."""
    now = now or timezone.now()
    candidates = (
        Activity.objects.filter(
            evaluation_open_q(now), status__name__in=(ActivityStatus.ONGOING, ActivityStatus.CONDUCTED)
        )
        .filter(form_templates__form_type=FormType.EVALUATION)
        .select_related("status")
        .order_by("pk")
    )
    return [
        activity for activity in candidates
        if (activity.status.name == ActivityStatus.CONDUCTED or now >= automatic_link_moment(activity))
        and evaluation_links_closed_reason(activity, now) is None
    ]


def send_automatic_evaluation_links(now: datetime | None = None) -> dict[str, int]:
    """The 5-minute job (activities.tasks): send the link ONCE to each attendee of automatic_link_activities() who
    has not evaluated, has not been sent a link and whose link has not failed. A failed send is not retried
    automatically (staff resend it on the Evaluation tab). Each activity with at least one link sent gets one
    History row by System ("Sent automatically to N attendees", ASSUMPTIONS 419). Returns the counts by result."""
    from activities.services import write_evaluation_link_history

    now = now or timezone.now()
    counts = {"sent": 0, "failed": 0, "skipped": 0}
    for activity in automatic_link_activities(now):
        never_sent = evaluation_link_recipients(activity).filter(
            evaluation_link_sent_at__isnull=True, evaluation_link_failed_at__isnull=True
        )
        sent_here = 0
        for registration_id in list(never_sent.order_by("pk").values_list("pk", flat=True)):
            result = deliver_evaluation_link(registration_id, first_time_only=True, now=now)
            counts[result if result in counts else "skipped"] += 1
            sent_here += result == "sent"
        if sent_here:
            write_evaluation_link_history(activity, sent_here)
    return counts


AUTOMATIC_SEND_PATTERN = re.compile(r"^Sent automatically to (\d+) attendees?$")


def automatic_link_sends(activity: Activity) -> list[tuple[datetime, int]]:
    """The automatic sends of this activity's evaluation links, oldest first: (when, how many), read from the History
    rows send_automatic_evaluation_links writes (by System). Staff resends are not in it (ASSUMPTIONS 419)."""
    from activities.services import EVALUATION_LINKS_LABEL

    rows = activity.audit_logs.filter(field_changed=EVALUATION_LINKS_LABEL, changed_by__isnull=True).order_by(
        "changed_at", "pk").values_list("changed_at", "new_value")
    return [(changed_at, int(match[1])) for changed_at, value in rows
            if (match := AUTOMATIC_SEND_PATTERN.match(value or ""))]


def evaluation_link_counts(activity: Activity) -> dict[str, int]:
    """Attendees who have not evaluated yet, by the state of their link: sent, failed, not sent yet."""
    recipients = evaluation_link_recipients(activity)
    return {
        "sent": recipients.filter(evaluation_link_sent_at__isnull=False).count(),
        "failed": recipients.filter(evaluation_link_failed_at__isnull=False).count(),
        "not_sent": recipients.filter(evaluation_link_sent_at__isnull=True, evaluation_link_failed_at__isnull=True).count(),
    }


# --- Shareable links and the general evaluation link (one per activity) --------------------------
# Links always start with settings.SITE_URL (the public address), never the Host of the request.


def registration_page_url(activity: Activity) -> str:
    """The full public registration page URL (shared by staff, printed as a QR code)."""
    return settings.SITE_URL + reverse("register", args=[activity.pk])


def evaluation_request_url(activity: Activity) -> str:
    """The full URL of the activity's general evaluation page (one link for everyone, printed as a QR code)."""
    return settings.SITE_URL + reverse("evaluation_request", args=[activity.pk])


# Always the same answer to a request, whoever typed the email: it never reveals who attended.
EVALUATION_REQUEST_SENT = "If you attended this activity, we've sent your evaluation link to that email."
EVALUATION_REQUEST_NOT_READY = "The evaluation is not open yet."


@dataclass(frozen=True)
class EvaluationRequestState:
    """Whether the general evaluation page takes requests now. kind: "open", "not_yet_open" or "closed"."""

    kind: str
    reason: str | None


def evaluation_request_state(activity: Activity, now: datetime | None = None) -> EvaluationRequestState:
    """The one rule for the general evaluation link: the evaluation window (00:00 Manila on the end date until
    the evaluation deadline, the same as the personal links) and an evaluation form. Nothing about people."""
    now = now or timezone.now()
    opens, _closes = evaluation_window(activity)
    if reason := _window_reason(activity, now):
        return EvaluationRequestState("not_yet_open" if now < opens else "closed", reason)
    if get_form(activity, FormType.EVALUATION) is None:
        return EvaluationRequestState("not_yet_open", EVALUATION_REQUEST_NOT_READY)
    return EvaluationRequestState("open", None)


def _count_evaluation_request(kind: str, value: str, limit: int) -> bool:
    return count_attempt("evaluation_request", kind, value, limit, settings.EVALUATION_REQUEST_RATE_WINDOW_MINUTES)


def guard_evaluation_request(ip: str, email, honeypot) -> bool:
    """Checks for a POST to the general evaluation page, before request_evaluation_link.

    - Rate limit: at most EVALUATION_REQUEST_MAX_PER_IP requests per IP address and EVALUATION_REQUEST_MAX_PER_EMAIL
      per email (lowercased, any activity) in EVALUATION_REQUEST_RATE_WINDOW_MINUTES. Every request counts.
    - Honeypot: HONEYPOT_FIELD filled in.
    Returns False when the request must go no further. The page then shows the same neutral message as always
    (EVALUATION_REQUEST_SENT) and sends nothing, so it reveals neither the limit nor who attended.
    Logged as a WARNING with the IP address only, never the email.
    """
    email = str(email or "").strip().lower()
    ip_ok = _count_evaluation_request("ip", ip, settings.EVALUATION_REQUEST_MAX_PER_IP)
    email_ok = _count_evaluation_request("email", email, settings.EVALUATION_REQUEST_MAX_PER_EMAIL) if email else True
    if not (ip_ok and email_ok):
        security_log.warning("Evaluation link request rate limit reached (ip=%s, by %s).", ip, "ip" if not ip_ok else "email")
        return False
    if str(honeypot or "").strip():
        security_log.warning("Evaluation link request refused: the hidden field was filled in (ip=%s).", ip)
        return False
    return True


def _is_valid_email(email: str) -> bool:
    try:
        validate_email(email)
    except ValidationError:
        return False
    return True


def _request_result(registration: Registration, page_open: bool) -> str:
    """Registration.LinkRequestResult of one request, in this order: the page took no requests, not attended,
    already evaluated, otherwise queued (= evaluation_link_recipients())."""
    Result = Registration.LinkRequestResult
    if not page_open:
        return Result.WINDOW_CLOSED
    if not registration.attendance_confirmed:
        return Result.NOT_ATTENDED
    if EvaluationSubmission.objects.filter(registration=registration).exists():
        return Result.ALREADY_EVALUATED
    return Result.QUEUED


def registrations_with_email(activity: Activity, email: str) -> list[Registration]:
    """This activity's registrations with that email, ignoring case (the database allows only one,
    unique_registration_per_activity_email, but callers handle every match)."""
    return list(activity.registrations.filter(email__iexact=email).order_by("pk"))


def _record_request(activity: Activity, email: str, page_open: bool, now: datetime) -> list[int]:
    """Store the request on every registration of this activity with that email (evaluation_link_requested_at and
    _request_result: the latest request only) and return the IDs to queue. An email that matches no registration
    is stored nowhere."""
    to_queue = []
    for registration in registrations_with_email(activity, email):
        result = _request_result(registration, page_open)
        Registration.objects.filter(pk=registration.pk).update(
            evaluation_link_requested_at=now, evaluation_link_request_result=result,
        )
        if result == Registration.LinkRequestResult.QUEUED:
            to_queue.append(registration.pk)
    return to_queue


def request_evaluation_link(activity: Activity, email, now: datetime | None = None) -> None:
    """Someone typed an email on the general evaluation page: email the personal evaluation link (the same
    Django-Q2 task as send_evaluation_links) to every registration of this activity with that email that
    attended and has not evaluated yet. The database allows one registration per email per activity
    (unique_registration_per_activity_email), but every match is handled anyway.

    Every registration with that email also gets the request recorded (evaluation_link_requested_at and
    evaluation_link_request_result: queued / not_attended / already_evaluated / window_closed), so staff see on the
    Evaluation tab why no link went out (ASSUMPTIONS 354). The delivery itself is recorded by deliver_evaluation_link.

    Returns nothing on purpose: the caller always shows EVALUATION_REQUEST_SENT. The link goes only to the
    registered address, so typing someone's email never shows their link. Raises RegistrationError when the
    page takes no requests now (evaluation_request_state; a valid email is still recorded as window_closed) and
    ValidationError for an invalid email address.
    """
    now = now or timezone.now()
    state = evaluation_request_state(activity, now)
    email = str(email or "").strip().lower()
    if state.reason:
        if _is_valid_email(email):
            _record_request(activity, email, page_open=False, now=now)
        raise RegistrationError(state.reason)
    if not _is_valid_email(email):
        raise ValidationError({"email": "Enter a valid email address."})
    for registration_id in _record_request(activity, email, page_open=True, now=now):
        transaction.on_commit(lambda registration_id=registration_id: queue_evaluation_link(registration_id), robust=True)



# --- "I can no longer attend" (ASSUMPTIONS 434) -----------------------------------------------------------------------
# The confirmation email carries a signed link (/registration/cancel/<token>/). Opening it only SHOWS the page; only a
# POST cancels (mail scanners open links). A cancelled registration is kept ("Will not attend" on the Confirmation of
# Attendance) and no longer takes a slot. "I can attend after all" undoes it while registration is open and a slot is free.

CANCEL_SALT = "registrations.cancel-link"
CANCEL_LINK_INVALID = "This link is not valid."
CANCEL_STARTED = "The activity has already started, so the registration can no longer be cancelled."
CANCEL_ATTENDED = "You were already checked in, so the registration can no longer be cancelled."
CANCELLED_TEXT = "Your registration is cancelled: you are listed as “Will not attend”."


def cancel_token(registration: Registration) -> str:
    return signing.Signer(salt=CANCEL_SALT).sign_object(registration.pk)


def cancel_link(registration: Registration) -> str:
    """The "I can no longer attend" page of one registration (SITE_URL, never the request's Host)."""
    return settings.SITE_URL + reverse("cancel_registration", args=[cancel_token(registration)])


def registration_from_cancel_token(token: str) -> Registration:
    try:
        registration_id = signing.Signer(salt=CANCEL_SALT).unsign_object(token)
        return Registration.objects.select_related("activity__status").get(pk=registration_id)
    except (signing.BadSignature, Registration.DoesNotExist, TypeError, ValueError) as error:
        raise RegistrationError(CANCEL_LINK_INVALID) from error


@dataclass(frozen=True)
class CancellationState:
    """kind: "can_cancel", "cancelled" (can_restore: registration is open and a slot is free) or "closed" (reason)."""

    kind: str
    reason: str | None = None
    can_restore: bool = False


def cancellation_state(registration: Registration, now: datetime | None = None) -> CancellationState:
    """The one rule of the cancel link: cancelling is possible while the activity is Scheduled and has not started
    (activity_start_end) and the person has not checked in; a cancelled registration can be restored while
    registration_closed_reason() is None (open, deadline, a free slot, form ready)."""
    now = now or timezone.now()
    activity = registration.activity
    started = activity.status.name != ActivityStatus.SCHEDULED or now >= activity_start_end(activity)[0]
    if registration.cancelled_at is not None:
        restorable = not started and registration_closed_reason(activity, now) is None
        return CancellationState("cancelled", CANCELLED_TEXT, can_restore=restorable)
    if registration.attendance_confirmed:
        return CancellationState("closed", CANCEL_ATTENDED)
    if started:
        return CancellationState("closed", CANCEL_STARTED)
    return CancellationState("can_cancel")


@transaction.atomic
def cancel_registration(registration: Registration, now: datetime | None = None) -> Registration:
    """"I can no longer attend": sets cancelled_at (the row is kept). Refused outside cancellation_state()."""
    now = now or timezone.now()
    Activity.objects.select_for_update(of=("self",)).filter(pk=registration.activity_id).first()
    registration = Registration.objects.select_for_update(of=("self",)).select_related("activity__status").get(
        pk=registration.pk)
    state = cancellation_state(registration, now)
    if state.kind == "cancelled":
        return registration
    if state.kind != "can_cancel":
        raise RegistrationError(state.reason)
    registration.cancelled_at = now
    registration.save(update_fields=["cancelled_at", "updated_at"])
    audit_log.info("Registration cancelled by the participant: activity %s, registration %s.",
                   registration.activity_id, registration.pk)
    return registration


@transaction.atomic
def restore_registration(registration: Registration, now: datetime | None = None) -> Registration:
    """"I can attend after all": clears cancelled_at while registration is open and a slot is free (the activity row is
    locked, like register())."""
    now = now or timezone.now()
    Activity.objects.select_for_update(of=("self",)).filter(pk=registration.activity_id).first()
    registration = Registration.objects.select_for_update(of=("self",)).select_related("activity__status").get(
        pk=registration.pk)
    state = cancellation_state(registration, now)
    if state.kind != "cancelled":
        return registration
    if not state.can_restore:
        reason = registration_closed_reason(registration.activity, now) or CANCEL_STARTED
        raise RegistrationError(reason)
    registration.cancelled_at = None
    registration.save(update_fields=["cancelled_at", "updated_at"])
    audit_log.info("Registration restored by the participant: activity %s, registration %s.",
                   registration.activity_id, registration.pk)
    return registration
