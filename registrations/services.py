"""Business logic for forms, public registration, attendance and evaluations.

Views stay thin and call the functions in this module.
"""

import json
import logging
from dataclasses import dataclass
from datetime import datetime, time

from django.conf import settings
from django.core import signing
from django.core.exceptions import ValidationError
from django.core.validators import validate_email
from django.db import transaction
from django.db.models import Max, Q, QuerySet
from django.urls import reverse
from django.utils import timezone

from accounts.models import TBIMember
from accounts.permissions import Permission, require_permission
from activities.formatting import format_datetime
from activities.models import Activity, ActivityStatus, ParticipantCategory
from activities.services import is_registration_open, open_for_registration, slots_remaining
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

CHOICE_TYPES = (FieldType.MULTIPLE_CHOICE, FieldType.CHECKBOX)
SHORT_TEXT_MAX = 255
CERTIFICATE_NAME_MAX = 150  # EVALUATION_SUBMISSION.certificate_name
CERTIFICATE_EMAIL_MAX = 254
LONG_TEXT_MAX = 5000
RATING_RANGE = range(1, 6)  # 1 to 5

# Name, email, contact number and category are REGISTRATION columns, never form fields.
RESERVED_LABELS = {"name", "full name", "email", "email address", "contact number", "participant category", "category"}

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


# --- Field options (stored as a JSON list in FORM_FIELD.options) --------------------------


def parse_options(raw: str) -> list[str]:
    """The options stored in a field ('' means no options)."""
    return json.loads(raw) if raw else []


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


def _ensure_unlocked(form: FormTemplate) -> None:
    if form_is_locked(form):
        raise RegistrationError(FORM_LOCKED)


@transaction.atomic
def create_form(member: TBIMember, activity: Activity, form_type: str, title: str = "") -> FormTemplate:
    """Create the activity's registration or evaluation form, copying the default fields
    for the activity's type (generated_from_default is true when any were copied)."""
    require_permission(member, Permission.CREATE_ACTIVITY)
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
    require_permission(member, Permission.CREATE_ACTIVITY)
    _ensure_unlocked(form)
    label, stored_options = _clean_field(form, field_label, field_type, options)
    last = form.fields.aggregate(last=Max("sort_order"))["last"] or 0
    return FormField.objects.create(
        template=form, field_label=label, field_type=field_type, options=stored_options,
        is_required=parse_bool(is_required, "is_required", default=True), sort_order=last + 1,
    )


@transaction.atomic
def update_field(member: TBIMember, field: FormField, **changes) -> FormField:
    """Change a field's label, type, options or is_required (refused once the form has answers)."""
    require_permission(member, Permission.CREATE_ACTIVITY)
    allowed = {"field_label", "field_type", "options", "is_required"}
    if unknown := set(changes) - allowed:
        raise RegistrationError(f"These cannot be changed: {', '.join(sorted(unknown))}.")
    _ensure_unlocked(field.template)
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
    return field


@transaction.atomic
def remove_field(member: TBIMember, field: FormField) -> None:
    require_permission(member, Permission.CREATE_ACTIVITY)
    _ensure_unlocked(field.template)
    field.delete()


@transaction.atomic
def reorder_fields(member: TBIMember, form: FormTemplate, field_ids: list[int]) -> None:
    """Put the fields in the given order. Allowed even after answers (order does not change meaning)."""
    require_permission(member, Permission.CREATE_ACTIVITY)
    fields = {field.pk: field for field in form.fields.all()}
    try:
        ids = [int(field_id) for field_id in field_ids]
    except (TypeError, ValueError) as error:
        raise RegistrationError("field_ids must be a list of numbers.") from error
    if sorted(ids) != sorted(fields):
        raise RegistrationError("Give every field of this form exactly once.")
    for number, field_id in enumerate(ids, start=1):
        FormField.objects.filter(pk=field_id).update(sort_order=number)


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


def registration_closed_reason(activity: Activity, now: datetime | None = None) -> str | None:
    """None when registration is open; otherwise why it is closed.

    The open/closed rule itself is activities.services.is_registration_open() (one place);
    this adds the "form exists" check and explains which part failed.
    """
    now = now or timezone.now()
    has_form = get_form(activity, FormType.REGISTRATION) is not None
    if is_registration_open(activity, now) and has_form:
        return None
    if activity.status.name != ActivityStatus.SCHEDULED:
        return "Registration is not open for this activity."
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
        "time_schedule": activity.time_schedule,
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


@transaction.atomic
def register(
    activity: Activity,
    *,
    name: str,
    email: str,
    contact_number: str = "",
    participant_category=None,
    answers: dict | None = None,
    now: datetime | None = None,
    startup=None,
) -> Registration:
    """Public registration. Everyone who registers is in (no approval step).

    `startup` is set only by the startup portal (a logged-in startup registering); every
    other rule is exactly the same as for a public registration.

    The activity row is locked first, so two people cannot take the last slot at the
    same time. The registration and its answers are saved together; the confirmation
    email is sent only after the save succeeds.
    """
    locked = Activity.objects.select_for_update().select_related("status").get(pk=activity.pk)
    if reason := registration_closed_reason(locked, now):
        raise RegistrationError(reason)

    errors: dict[str, str] = {}
    name, email, contact_number = (name or "").strip(), (email or "").strip().lower(), (contact_number or "").strip()
    if not name:
        errors["name"] = "This field is required."
    try:
        validate_email(email)
    except ValidationError:
        errors["email"] = "Enter a valid email address."
    category = None
    try:
        category = _choose_category(locked, participant_category)
    except ValidationError as error:
        errors.update({key: messages[0] for key, messages in error.message_dict.items()})
    form = get_form(locked, FormType.REGISTRATION)
    try:
        stored_answers = validate_answers(form, answers)
    except ValidationError as error:
        errors.update({key: messages[0] for key, messages in error.message_dict.items()})
    if errors:
        raise ValidationError(errors)
    if locked.registrations.filter(email__iexact=email).exists():
        raise RegistrationError("This email is already registered for this activity.")

    registration = Registration.objects.create(
        activity=locked, template=form, participant_category=category,
        name=name, email=email, contact_number=contact_number, startup=startup,
    )
    for field, value in stored_answers.items():
        FormAnswer.objects.create(
            field=field, answer_source=FormAnswer.AnswerSource.REGISTRATION, registration=registration, answer_value=value
        )
    # The confirmation email is a background task queued after the commit. robust=True: a problem
    # queueing it never turns the saved registration into an error (the task itself never raises).
    registration_id = registration.pk
    transaction.on_commit(lambda: queue_confirmation(registration_id), robust=True)
    return registration


# --- Attendance (staff, needs can_create_activity) -----------------------------------------

ATTENDANCE_CLOSED = "Attendance can be recorded only when the activity is Ongoing or Conducted."


def attendance_closed_reason(activity: Activity) -> str | None:
    """None when attendance can be recorded now (Ongoing or Conducted); otherwise why not."""
    return None if activity.status.name in ATTENDANCE_STATUSES else ATTENDANCE_CLOSED


@transaction.atomic
def set_attendance(
    member: TBIMember, activity: Activity, registration_ids: list[int], attended: bool, method: str | None = None
) -> int:
    """Mark (or unmark) attendance for one or many registrations. Returns how many changed.

    Allowed only while the activity is Ongoing or Conducted (Conducted locks the activity's
    own fields, not its registrations). Unmarking clears the method, and is refused for
    anyone who has already submitted the evaluation or has a certificate. All or nothing.
    """
    require_permission(member, Permission.CREATE_ACTIVITY)
    locked = Activity.objects.select_for_update().select_related("status").get(pk=activity.pk)
    if reason := attendance_closed_reason(locked):
        raise RegistrationError(reason)
    if attended and method not in Registration.ConfirmationMethod.values:
        raise RegistrationError("Choose how attendance was confirmed: email or wet_signature.")

    ids = {int(registration_id) for registration_id in registration_ids}
    registrations = list(locked.registrations.select_for_update().filter(pk__in=ids))
    if not ids or len(registrations) != len(ids):
        raise RegistrationError("Some registrations were not found for this activity.")
    if not attended:
        evaluated = [r.name for r in registrations if EvaluationSubmission.objects.filter(registration=r).exists()]
        if evaluated:
            raise RegistrationError(
                f"Cannot unmark {', '.join(sorted(evaluated))}: already submitted the evaluation."
            )
        with_certificate = [r.name for r in registrations if r.certificates.exists()]
        if with_certificate:  # Step 9: a certificate was generated for their attendance
            raise RegistrationError(
                f"Cannot unmark {', '.join(sorted(with_certificate))}: a certificate was already generated."
            )

    for registration in registrations:
        registration.attendance_confirmed = attended
        registration.confirmation_method = method if attended else None
        registration.save(update_fields=["attendance_confirmed", "confirmation_method", "updated_at"])
    return len(registrations)


def list_registrations(
    activity: Activity, attended: bool | None = None, participant_category_id: int | None = None, search: str = ""
) -> QuerySet:
    """Staff list of an activity's registrations by name, with category, startup, evaluation and answers.
    `search` matches the name or the email (ignoring case)."""
    registrations = (
        activity.registrations.select_related("participant_category", "startup", "evaluation_submission")
        .prefetch_related("answers__field")
        .order_by("name", "registration_id")
    )
    if search := (search or "").strip():
        registrations = registrations.filter(Q(name__icontains=search) | Q(email__icontains=search))
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


def evaluation_window(activity: Activity) -> tuple[datetime, datetime]:
    """(opens, closes): opens at 00:00 Manila time on the end date, closes at the deadline."""
    opens = timezone.make_aware(datetime.combine(activity.end_date, time(0, 0)))
    return opens, activity.evaluation_deadline


def _window_reason(activity: Activity, now: datetime) -> str | None:
    opens, closes = evaluation_window(activity)
    if now < opens:
        return f"The evaluation opens on {format_datetime(opens)}."
    if now > closes:
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
    shows_person = not submitted and now <= activity.evaluation_deadline
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


@transaction.atomic
def submit_evaluation(
    token: str,
    *,
    certificate_name: str,
    certificate_email: str,
    comments: str = "",
    answers: dict | None = None,
    now: datetime | None = None,
) -> EvaluationSubmission:
    """Save the evaluation and its answers together. Never anonymous: tied to the registration.

    The participant also gives the name printed on their certificate and the address it is
    emailed to (Step 9). After the save, an Approved certificate is issued and emailed.
    """
    registration = registration_from_token(token)
    registration = Registration.objects.select_for_update().get(pk=registration.pk)  # one submission, even on double click
    if reason := evaluation_closed_reason(registration, now):
        raise RegistrationError(reason)
    form = get_form(registration.activity, FormType.EVALUATION)
    name, email, stored_answers = check_evaluation(form, certificate_name, certificate_email, answers)
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


def send_evaluation_links(member: TBIMember, activity: Activity, now: datetime | None = None) -> int:
    """Queue one email task per attendee who has not evaluated yet (their personal link).
    Returns how many were queued; the emails are sent in the background, so a mail-server
    problem never fails this action.

    Refused if the activity has no evaluation form or the evaluation window is not open.
    """
    require_permission(member, Permission.CREATE_ACTIVITY)
    if reason := evaluation_links_closed_reason(activity, now):
        raise RegistrationError(reason)
    registration_ids = list(evaluation_link_recipients(activity).order_by("pk").values_list("pk", flat=True))
    for registration_id in registration_ids:
        queue_evaluation_link(registration_id)
    return len(registration_ids)


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


def request_evaluation_link(activity: Activity, email, now: datetime | None = None) -> None:
    """Someone typed an email on the general evaluation page: email the personal evaluation link (the same
    Django-Q2 task as send_evaluation_links) to every registration of this activity with that email that
    attended and has not evaluated yet. The database allows one registration per email per activity
    (unique_registration_per_activity_email), but every match is handled anyway.

    Returns nothing on purpose: the caller always shows EVALUATION_REQUEST_SENT. The link goes only to the
    registered address, so typing someone's email never shows their link. Raises RegistrationError when the
    page takes no requests now (evaluation_request_state) and ValidationError for an invalid email address.
    """
    state = evaluation_request_state(activity, now)
    if state.reason:
        raise RegistrationError(state.reason)
    email = str(email or "").strip().lower()
    try:
        validate_email(email)
    except ValidationError as error:
        raise ValidationError({"email": "Enter a valid email address."}) from error
    recipients = evaluation_link_recipients(activity).filter(email__iexact=email)
    for registration_id in recipients.order_by("pk").values_list("pk", flat=True):
        transaction.on_commit(lambda registration_id=registration_id: queue_evaluation_link(registration_id), robust=True)
