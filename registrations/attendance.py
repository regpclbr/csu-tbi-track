"""Attendance per day (ASSUMPTIONS 434, replaces item 111): the ONE module that records or removes ATTENDANCE_DAY rows.

- An activity's days are every calendar day from start_date to end_date (activity_days).
- QR check-in (check_in): one "Attendance check-in" link per activity (/checkin/<id>/, Share card). It works only inside
  checkin_state(): on each activity day from PARTICIPANT_CHECKIN_EARLY_MINUTES before start_time until end_time (Manila;
  no times = the whole day), while the activity is Scheduled or Ongoing. The participant types the email they registered
  with; today's row is recorded (method qr). One per person per day. Their signature is asked for when they have none.
  An email is sent afterwards ("You were checked in…", a background task; never blocks the check-in).
- Walk-ins (register_walk_in): on the same page, someone who did not pre-register registers (short form + signature +
  privacy consent) and is checked in at once, even when online registration is closed. max_slots does not apply.
- Staff (mark_by_staff, through services.set_attendance): mark or undo one day, method staff, with the old rules (Ongoing
  or Conducted only; undoing someone's LAST day is refused after an evaluation or an approved certificate, and a
  certificate still waiting is deleted). On a staff device the participant may sign in the same dialog.

REGISTRATION.attendance_confirmed = "attended at least one day" is kept in step here (_sync_attended), in the same
transaction as the day rows. Every change also saves the registration (updated_at), which the signature retention
check reads (signatures.last_change).
"""

import logging
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta

from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.validators import validate_email
from django.db import IntegrityError, transaction
from django.utils import timezone

from accounts.models import TBIMember
from accounts.permissions import Permission, require_permission, require_permission_for
from activities.formatting import format_date, format_datetime, format_time
from activities.models import Activity, ActivityStatus
from config.ratelimit import count_attempt

from . import signatures
from .models import AttendanceDay, EvaluationSubmission, FormType, Registration

security_log = logging.getLogger("accounts.security")
audit_log = logging.getLogger("registrations.audit")

CHECKIN_STATUSES = (ActivityStatus.SCHEDULED, ActivityStatus.ONGOING)
CHECKIN_EMAIL_TASK = "registrations.tasks.run_send_checkin_email"

CHECKIN_CLOSED = "Check-in for this activity has closed."
CHECKIN_NOT_AVAILABLE = "Check-in is not available for this activity."
WALK_IN_DUPLICATE = ("This email is already registered for this activity. Go back and type it on the check-in page "
                     "instead.")
WALK_IN_NO_FORM = ("Walk-in registration is not available for this activity yet. Please see the TBI staff at the "
                   "registration desk.")
STAFF_METHOD_ONLY = "Attendance marked by staff is recorded with the method “staff”."
STAFF_DAY_REFUSED = "Choose a day of this activity, up to today."


class AttendanceError(Exception):
    """A refused check-in or attendance change. The message is safe to show."""


# --- Days and the check-in window ----------------------------------------------------------------------------------


def activity_days(activity: Activity) -> list[date]:
    """Every calendar day of the activity, start_date to end_date (weekends included)."""
    count = (activity.end_date - activity.start_date).days + 1
    return [activity.start_date + timedelta(days=offset) for offset in range(max(count, 1))]


def day_window(activity: Activity, day: date) -> tuple[datetime, datetime]:
    """When the QR check-in is open on one day (Manila): from PARTICIPANT_CHECKIN_EARLY_MINUTES before start_time until
    end_time. No times (older activities): the whole day."""
    if activity.start_time is None or activity.end_time is None:
        opens = timezone.make_aware(datetime.combine(day, time(0, 0)))
        return opens, opens + timedelta(days=1)
    opens = timezone.make_aware(datetime.combine(day, activity.start_time))
    closes = timezone.make_aware(datetime.combine(day, activity.end_time))
    return opens - timedelta(minutes=settings.PARTICIPANT_CHECKIN_EARLY_MINUTES), closes


def today(now: datetime | None = None) -> date:
    return timezone.localdate(now or timezone.now())


@dataclass(frozen=True)
class CheckinState:
    """kind: "open" (day = today), "not_yet_open" (reason says when), "closed" or "not_available" (Draft: 404)."""

    kind: str
    reason: str | None
    day: date | None = None


def checkin_state(activity: Activity, now: datetime | None = None) -> CheckinState:
    """The one rule for the QR check-in (also behind walk-ins and the Share card). Callers refresh the status first
    (activities.services.refresh_status) so a started activity is Ongoing."""
    now = now or timezone.now()
    status = activity.status.name
    if status == ActivityStatus.DRAFT:
        return CheckinState("not_available", CHECKIN_NOT_AVAILABLE)
    if status not in CHECKIN_STATUSES:
        return CheckinState("closed", CHECKIN_CLOSED)
    for day in activity_days(activity):
        opens, closes = day_window(activity, day)
        if opens <= now < closes:
            return CheckinState("open", None, day)
        if now < opens:
            return CheckinState("not_yet_open", f"Check-in opens on {format_datetime(opens)}.", day)
    return CheckinState("closed", CHECKIN_CLOSED)


def checkin_url(activity: Activity) -> str:
    """The full check-in page URL (SITE_URL, never the request's Host), printed as a QR code at the venue."""
    from django.urls import reverse

    return settings.SITE_URL + reverse("checkin", args=[activity.pk])


def guard_checkin(ip: str, email, honeypot) -> None:
    """Rate limit and honeypot of every POST to the public check-in page (email, signature and walk-in steps):
    CHECKIN_MAX_ATTEMPTS_PER_IP per IP address and CHECKIN_MAX_ATTEMPTS_PER_EMAIL per typed email in
    CHECKIN_RATE_WINDOW_MINUTES. Logged with the IP address only. Raises AttendanceError."""
    from .services import HONEYPOT_MESSAGE, RegistrationRateLimited

    email = str(email or "").strip().lower()
    window = settings.CHECKIN_RATE_WINDOW_MINUTES
    ip_ok = count_attempt("checkin", "ip", ip, settings.CHECKIN_MAX_ATTEMPTS_PER_IP, window)
    email_ok = count_attempt("checkin", "email", email, settings.CHECKIN_MAX_ATTEMPTS_PER_EMAIL, window) if email else True
    if not (ip_ok and email_ok):
        security_log.warning("Check-in rate limit reached (ip=%s, by %s).", ip, "ip" if not ip_ok else "email")
        raise RegistrationRateLimited(f"Too many check-in attempts. Please try again in {window} minutes or see the TBI staff.")
    if str(honeypot or "").strip():
        security_log.warning("Check-in refused: the hidden field was filled in (ip=%s).", ip)
        raise AttendanceError(HONEYPOT_MESSAGE)


# --- Shared helpers ----------------------------------------------------------------------------------------------------


def _sync_attended(registration: Registration) -> None:
    """attendance_confirmed = has at least one day row. Always saves the registration (updated_at moves too)."""
    registration.attendance_confirmed = registration.attendance_days.exists()
    registration.save(update_fields=["attendance_confirmed", "updated_at"])


def _queue_checkin_email(attendance_day_id: int) -> None:
    from django_q.tasks import async_task

    async_task(CHECKIN_EMAIL_TASK, attendance_day_id, task_name=f"Check-in email {attendance_day_id}")


def _record_day(registration: Registration, day: date, method: str, now: datetime, member=None) -> AttendanceDay:
    with transaction.atomic():  # a savepoint: a duplicate (two scans at once) is refused by the unique constraint
        row = AttendanceDay.objects.create(
            registration=registration, day=day, checked_in_at=now, created_at=now, method=method, marked_by=member)
    _sync_attended(registration)
    return row


def checked_in_text(row: AttendanceDay) -> str:
    """"8:42 AM" (Manila)."""
    return format_time(timezone.localtime(row.checked_in_at).time())


# --- QR check-in (public) ---------------------------------------------------------------------------------------------


@dataclass
class CheckinResult:
    """kind: "checked_in", "already" (row = the earlier check-in), "needs_signature" or "not_registered"."""

    kind: str
    registration: Registration | None = None
    row: AttendanceDay | None = None

    @property
    def message(self) -> str:
        if self.kind == "already":
            return f"Already checked in today at {checked_in_text(self.row)}."
        if self.kind == "checked_in":
            return f"You're checked in for {format_date(self.row.day)} at {checked_in_text(self.row)}."
        return ""


def _clean_email(email) -> str:
    email = str(email or "").strip().lower()
    try:
        validate_email(email)
    except ValidationError:
        raise ValidationError({"email": ["Enter the email address you registered with."]}) from None
    return email


@transaction.atomic
def check_in(activity: Activity, email, *, signature: dict | None = None, now: datetime | None = None) -> CheckinResult:
    """Record today's attendance for the registration with this email (method qr).

    Refused (AttendanceError) outside checkin_state(). A person without a usable signature gets "needs_signature" until
    `signature` (the raw fields, signatures.clean_signature) is sent; nothing is recorded before that. A cancelled
    registration ("Will not attend") who came anyway is checked in too (the Confirmation still shows what they signified).
    The check-in email is queued after the commit (robust: a queueing problem never undoes the check-in)."""
    now = now or timezone.now()
    locked = Activity.objects.select_for_update(of=("self",)).select_related("status").get(pk=activity.pk)
    state = checkin_state(locked, now)
    if state.kind != "open":
        raise AttendanceError(state.reason)
    email = _clean_email(email)
    registration = (locked.registrations.select_for_update(of=("self",)).filter(email__iexact=email)
                    .select_related("activity").first())
    if registration is None:
        return CheckinResult("not_registered")
    existing = registration.attendance_days.filter(day=state.day).first()
    if existing is not None:
        return CheckinResult("already", registration, existing)
    clean = None
    if not signatures.has_signature(registration):
        if not signatures.is_given(signature):
            return CheckinResult("needs_signature", registration)
        clean = signatures.clean_signature(signature)
    try:
        row = _record_day(registration, state.day, AttendanceDay.Method.QR, now)
    except IntegrityError:  # the same person scanned twice at the same moment
        return CheckinResult("already", registration, registration.attendance_days.get(day=state.day))
    if clean is not None:
        signatures.save_signature(registration, clean, now)
    audit_log.info("Checked in by QR: activity %s, registration %s, day %s.", locked.pk, registration.pk, state.day)
    row_id = row.pk
    transaction.on_commit(lambda: _queue_checkin_email(row_id), robust=True)
    return CheckinResult("checked_in", registration, row)


@transaction.atomic
def register_walk_in(
    activity: Activity,
    *,
    name: str,
    email: str,
    contact_number: str = "",
    designation: str = "",
    office_agency: str = "",
    participant_category=None,
    signature: dict | None = None,
    now: datetime | None = None,
) -> CheckinResult:
    """A walk-in: someone who did not pre-register registers at the venue and is checked in at once (source walk_in).

    Allowed only inside checkin_state(), even when online registration is closed (deadline, slots or by hand) and
    regardless of max_slots. Name, email, Designation / Position, Office / Agency and the category are checked like
    register(); the signature is required; the activity's registration form is the row's template (its own questions
    are not asked of walk-ins). The same email twice in one activity is refused (WALK_IN_DUPLICATE). The privacy
    consent is checked by the page before this call (services.check_privacy_consent)."""
    from .services import RegistrationError, clean_registrant, get_form

    now = now or timezone.now()
    locked = Activity.objects.select_for_update(of=("self",)).select_related("status").get(pk=activity.pk)
    state = checkin_state(locked, now)
    if state.kind != "open":
        raise AttendanceError(state.reason)
    errors: dict[str, str] = {}
    values = {}
    try:
        values = clean_registrant(locked, name=name, email=email, contact_number=contact_number,
                                  designation=designation, office_agency=office_agency,
                                  participant_category=participant_category)
    except ValidationError as error:
        errors.update({key: messages[0] for key, messages in error.message_dict.items()})
    clean = None
    try:
        clean = signatures.clean_signature(signature)
    except ValidationError as error:
        errors.update({key: messages[0] for key, messages in error.message_dict.items()})
    if errors:
        raise ValidationError(errors)
    if locked.registrations.filter(email__iexact=values["email"]).exists():
        raise RegistrationError(WALK_IN_DUPLICATE)
    form = get_form(locked, FormType.REGISTRATION)
    if form is None:
        raise AttendanceError(WALK_IN_NO_FORM)
    registration = Registration.objects.create(
        activity=locked, template=form, registration_source=Registration.Source.WALK_IN, **values)
    row = _record_day(registration, state.day, AttendanceDay.Method.QR, now)
    signatures.save_signature(registration, clean, now)
    audit_log.info("Walk-in registered and checked in: activity %s, registration %s, day %s.",
                   locked.pk, registration.pk, state.day)
    row_id = row.pk
    transaction.on_commit(lambda: _queue_checkin_email(row_id), robust=True)
    return CheckinResult("checked_in", registration, row)


# --- Staff marking ---------------------------------------------------------------------------------------------------


def staff_days(activity: Activity, now: datetime | None = None) -> list[date]:
    """The days staff may mark or undo: the activity's days up to today (Manila). When none has started yet (staff
    moved the activity to Ongoing early by hand), its first day."""
    current = today(now)
    days = activity_days(activity)
    return [day for day in days if day <= current] or days[:1]


def default_staff_day(activity: Activity, now: datetime | None = None) -> date:
    """Today when it is an activity day; after the activity, its last day; before it, its first day."""
    current = today(now)
    return min(max(current, activity.start_date), activity.end_date)


def parse_day(value) -> date | None:
    if isinstance(value, date):
        return value
    try:
        return date.fromisoformat(str(value or "").strip())
    except ValueError:
        return None


def mark_by_staff(
    member: TBIMember,
    activity: Activity,
    registration_ids: list[int],
    attended: bool,
    *,
    day=None,
    method: str | None = None,
    signature: dict | None = None,
    now: datetime | None = None,
) -> int:
    """Mark (or undo) one day's attendance for one or many registrations; returns how many registrations were handled.
    The rules of services.set_attendance (which calls this inside its transaction and permission check):
    Ongoing or Conducted only; `day` one of staff_days() (default default_staff_day()); method "staff" (or None).
    Undo removes that day's row; when it was the person's LAST day the old undo rules apply (refused after an evaluation
    or an approved certificate; a waiting certificate is deleted). `signature` (draw or typed, one registration only):
    collected on the staff device for a person without one. All or nothing."""
    from certificates import services as certificate_services

    from .services import RegistrationError, attendance_closed_reason

    require_permission_for(member, Permission.CREATE_ACTIVITY, activity)  # or its assignment (437)
    now = now or timezone.now()
    locked = Activity.objects.select_for_update(of=("self",)).select_related("status").get(pk=activity.pk)
    if reason := attendance_closed_reason(locked):
        raise RegistrationError(reason)
    if attended and method not in (None, AttendanceDay.Method.STAFF):
        raise RegistrationError(STAFF_METHOD_ONLY)
    chosen = parse_day(day) if day not in (None, "") else default_staff_day(locked, now)
    if chosen is None or chosen not in staff_days(locked, now):
        raise RegistrationError(STAFF_DAY_REFUSED)

    ids = {int(registration_id) for registration_id in registration_ids}
    registrations = list(locked.registrations.select_for_update(of=("self",)).filter(pk__in=ids))
    if not ids or len(registrations) != len(ids):
        raise RegistrationError("Some registrations were not found for this activity.")
    clean = None
    if attended and signatures.is_given(signature):
        if len(registrations) != 1:
            raise RegistrationError("A signature can be collected for one person at a time.")
        if not signatures.has_signature(registrations[0]):
            clean = signatures.clean_signature(signature, signatures.STAFF_KINDS)

    if not attended:
        # Undoing someone's last day = they no longer attended: the old undo rules (ASSUMPTIONS 111, 362).
        last_day = [r for r in registrations if not r.attendance_days.exclude(day=chosen).exists() and r.attendance_confirmed]
        evaluated = sorted(r.name for r in last_day if EvaluationSubmission.objects.filter(registration=r).exists())
        if evaluated:
            raise RegistrationError(f"Cannot undo attendance for {', '.join(evaluated)}: already submitted the evaluation.")
        blocked = sorted(f"{r.name} ({reason[:-1]})" for r in last_day
                         if (reason := certificate_services.undo_attendance_refused_reason(r)))
        if blocked:
            raise RegistrationError(f"Cannot undo attendance for {', '.join(blocked)}.")
        for registration in last_day:
            certificate_services.delete_certificate_for_undone_attendance(member, registration)
        for registration in registrations:
            registration.attendance_days.filter(day=chosen).delete()
            _sync_attended(registration)
    else:
        for registration in registrations:
            if not registration.attendance_days.filter(day=chosen).exists():
                _record_day(registration, chosen, AttendanceDay.Method.STAFF, now, member)
            else:
                _sync_attended(registration)
        if clean is not None:
            signatures.save_signature(registrations[0], clean, now)
    audit_log.info("Attendance %s for %s: activity %s, registrations %s, by member %s.",
                   "marked (staff)" if attended else "undone", chosen, locked.pk, sorted(ids), member.pk)
    if attended and locked.status.name == ActivityStatus.CONDUCTED:  # marked after Conducted: their certificate too
        certificate_services.auto_generate_certificates(locked, registration_ids=sorted(ids), now=now)
    return len(registrations)


# --- Counts for the Overview and the Registrations tab ----------------------------------------------------------------


def checkins_by_day(activity: Activity) -> list[dict]:
    """Per activity day: how many attended (QR + staff + copied), and how many by QR."""
    rows = AttendanceDay.objects.filter(registration__activity=activity)
    counts: dict[date, dict] = {day: {"day": day, "total": 0, "qr": 0} for day in activity_days(activity)}
    for day, method in rows.values_list("day", "method"):
        entry = counts.setdefault(day, {"day": day, "total": 0, "qr": 0})
        entry["total"] += 1
        entry["qr"] += method == AttendanceDay.Method.QR
    return [counts[day] for day in sorted(counts)]


def pre_registration_counts(activity: Activity) -> dict[str, int]:
    """For the meal estimate (ASSUMPTIONS 434): pre-registered "Will attend" / "Will not attend" (online and portal), and
    walk-ins."""
    registrations = activity.registrations
    pre = registrations.exclude(registration_source=Registration.Source.WALK_IN)
    return {
        "will_attend": pre.filter(cancelled_at__isnull=True).count(),
        "will_not_attend": pre.filter(cancelled_at__isnull=False).count(),
        "walk_ins": registrations.filter(registration_source=Registration.Source.WALK_IN).count(),
    }
