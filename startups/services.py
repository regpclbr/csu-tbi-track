"""Business logic for startups: Navigatu sync, startup login, set-password links, portal reads.

Views (and the Django admin) stay thin and call the functions in this module.

- Navigatu data is read-only here and comes ONLY from `providers.get_provider()`.
- A startup is never a Django user. Its login lives in the session under STARTUP_SESSION_KEY,
  separate from staff login (ASSUMPTIONS item 62).
"""

import logging
from collections import Counter
from datetime import datetime, timedelta

from django.conf import settings
from django.contrib.auth.hashers import make_password
from django.contrib.auth.password_validation import validate_password
from django.core import signing
from django.core.exceptions import PermissionDenied, ValidationError
from django.core.validators import validate_email
from django.db import connection, transaction
from django.db.models import Q, QuerySet
from django.http import HttpRequest
from django.urls import reverse
from django.utils import timezone
from django.utils.crypto import constant_time_compare, salted_hmac

from accounts.models import TBIMember
from accounts.permissions import Permission, require_permission
from accounts.services import (
    INVALID_LOGIN_MESSAGE,
    STARTUP_SCOPE,
    LoginError,
    clear_failed_logins,
    client_ip,
    is_locked_out,
    lockout_message,
    record_failed_login,
)
from activities.models import Activity
from activities.services import PageResult, _paginate
from registrations.models import Registration

from . import emails
from .models import Startup
from .providers import StartupProvider, StartupRecord, get_provider

sync_log = logging.getLogger("startups.sync")
security_log = logging.getLogger("accounts.security")

# Startup login keeps the logged-in startup's id under this session key.
# It is deliberately separate from Django's staff login: a startup is never
# `request.user`, so staff views (which check for an active TBI_MEMBER) never let it in.
STARTUP_SESSION_KEY = "startup_id"
STARTUP_SESSION_HASH_KEY = "startup_auth_hash"  # changes when the password changes -> old sessions end

DEFAULT_PAGE_SIZE = 20
_SYNC_LOCK_ID = 7_202_607  # any fixed number: only one sync runs at a time (PostgreSQL advisory lock)


class StartupError(Exception):
    """An action on a startup was refused. The message is safe to show."""


# --- Sync from Navigatu ------------------------------------------------------------------------

SYNCED_FIELDS = ("name", "contact_number", "email", "maturity_stage", "last_assessment_at", "is_active")


def _clean_record(record: StartupRecord) -> tuple[dict | None, str]:
    """The values we store for a record, or (None, reason) if the record cannot be used."""
    values = {
        "name": (record.name or "").strip(),
        "contact_number": (record.contact_number or "").strip(),
        "email": (record.email or "").strip().lower(),
        "maturity_stage": (record.maturity_stage or "").strip() or None,
        "last_assessment_at": record.last_assessment_at,
        "is_active": bool(record.is_active),
    }
    navigatu_id = (record.navigatu_id or "").strip()
    if not navigatu_id or len(navigatu_id) > 100:
        return None, "missing or too long navigatu_id"
    if not values["name"] or len(values["name"]) > 255:
        return None, "missing or too long name"
    try:
        validate_email(values["email"])
    except ValidationError:
        return None, "invalid email"
    if len(values["contact_number"]) > 30 or len(values["maturity_stage"] or "") > 100:
        return None, "contact number or maturity stage too long"
    return values, ""


def _deactivation_guard(record_count: int, to_deactivate: int, active_before: int) -> str:
    """Why this run must not deactivate anyone ("" if it may)."""
    if record_count == 0:
        return "Navigatu returned no startups."
    ratio = settings.STARTUP_SYNC_MAX_DEACTIVATE_RATIO
    if active_before and to_deactivate / active_before > ratio:
        return (
            f"{to_deactivate} of {active_before} active startups would be deactivated "
            f"(more than {ratio:.0%})."
        )
    return ""


def sync_startups(
    now: datetime | None = None,
    force: bool = False,
    provider: StartupProvider | None = None,
    deactivate_missing: bool = True,
) -> dict:
    """Copy Navigatu's startup data into STARTUP (read-only data; never touches `password`).

    - Match by navigatu_id first, then by email (ignoring case).
    - Create or update name, contact_number, email, maturity_stage, last_assessment_at, is_active;
      a row is saved only if one of these changed. last_synced_at is refreshed for every startup
      the provider returned, in one bulk update that does not touch updated_at.
    - Email conflicts and unusable records are skipped and logged, never merged.
    - Startups the provider no longer returns are deactivated (never deleted), unless the safety
      guard stops it: zero records, or more than STARTUP_SYNC_MAX_DEACTIVATE_RATIO of the active
      startups would be deactivated. `force=True` (manual command only) overrides the guard.
    - `provider` and `deactivate_missing=False` are only for seed_demo: its mock sync must never
      deactivate startups that are not demo data.
    """
    now = now or timezone.now()
    records = (provider or get_provider()).list_startups()  # ProviderNotAvailable goes to the caller
    result = {
        "created": 0, "updated": 0, "unchanged": 0, "deactivated": 0, "skipped": 0,
        "deactivation_skipped": False, "deactivation_skipped_reason": "",
    }

    with transaction.atomic():
        with connection.cursor() as cursor:  # one sync at a time (scheduled + manual cannot overlap)
            cursor.execute("SELECT pg_advisory_xact_lock(%s)", [_SYNC_LOCK_ID])

        rows = list(Startup.objects.select_for_update().order_by("pk"))
        by_navigatu_id = {row.navigatu_id: row for row in rows if row.navigatu_id}
        by_email = {row.email.lower(): row for row in rows}
        active_before = sum(1 for row in rows if row.is_active)
        seen: set[int] = set()  # rows the provider returned
        protected: set[int] = set()  # rows involved in a conflict: left untouched

        cleaned = []
        for record in records:
            values, problem = _clean_record(record)
            if values is None:
                result["skipped"] += 1
                sync_log.warning("Skipped Navigatu record %r: %s.", record.navigatu_id, problem)
            else:
                cleaned.append((record.navigatu_id.strip(), values))
        email_counts = Counter(values["email"] for _, values in cleaned)

        for navigatu_id, values in cleaned:
            email = values["email"]
            row, owner = by_navigatu_id.get(navigatu_id), by_email.get(email)
            conflict = ""
            if email_counts[email] > 1:
                conflict = f"Navigatu has {email_counts[email]} startups with the email {email}"
            elif row is not None and owner is not None and owner.pk != row.pk:
                conflict = f"the email {email} already belongs to startup {owner.pk}"
            elif row is None and owner is not None and owner.navigatu_id and owner.navigatu_id != navigatu_id:
                conflict = f"the email {email} belongs to startup {owner.pk} with Navigatu ID {owner.navigatu_id}"
            if conflict:
                result["skipped"] += 1
                protected.update(r.pk for r in (row, owner) if r is not None)
                sync_log.warning("Skipped Navigatu record %s: %s. Nothing was merged.", navigatu_id, conflict)
                continue

            row = row or owner  # an email match links an existing row that had no navigatu_id
            if row is None:
                row = Startup.objects.create(navigatu_id=navigatu_id, last_synced_at=now, **values)
                by_navigatu_id[navigatu_id], by_email[email] = row, row
                result["created"] += 1
                sync_log.info("Created startup %s (%s) from Navigatu.", row.pk, navigatu_id)
            else:
                changed = [field for field, value in values.items() if getattr(row, field) != value]
                if row.navigatu_id != navigatu_id:
                    changed.append("navigatu_id")
                    row.navigatu_id = navigatu_id
                if changed:
                    by_email.pop(row.email.lower(), None)  # before the email changes below
                    for field in changed:
                        if field != "navigatu_id":
                            setattr(row, field, values[field])
                    row.save(update_fields=[*changed, "updated_at"])
                    by_email[email] = row
                    result["updated"] += 1
                else:
                    result["unchanged"] += 1
            seen.add(row.pk)

        # Bulk update: refreshes the "last checked" time without saving rows or changing updated_at.
        Startup.objects.filter(pk__in=seen).update(last_synced_at=now)

        missing = [row for row in rows if row.is_active and row.pk not in seen and row.pk not in protected]
        if not deactivate_missing:
            missing = []
        reason = _deactivation_guard(len(records), len(missing), active_before)
        if reason and not force:
            result["deactivation_skipped"] = True
            result["deactivation_skipped_reason"] = reason
            sync_log.warning(
                "Deactivation skipped (safety guard): %s %d startup(s) were NOT deactivated. "
                "Run `python manage.py sync_startups --force` to deactivate them on purpose.",
                reason, len(missing),
            )
        else:
            if reason:
                sync_log.warning("Safety guard overridden with --force: %s", reason)
            for row in missing:
                row.is_active = False
                row.save(update_fields=["is_active", "updated_at"])
                result["deactivated"] += 1
                sync_log.info("Deactivated startup %s (%s): no longer returned by Navigatu.", row.pk, row.email)

    sync_log.info("Startup sync finished: %s", result)
    return result


# --- Set-password link (single use, without a new column) -------------------------------------------

_LINK_SALT = "startups.set-password-link"
LINK_INVALID = "This link is not valid."
LINK_EXPIRED = "This link has expired. Ask the TBI to send you a new one."
LINK_USED = "This link has already been used or is no longer valid. Ask the TBI to send you a new one."


def _fingerprint(startup: Startup) -> str:
    """Changes whenever the password (or email) changes, which makes older links stop working."""
    return salted_hmac(_LINK_SALT, f"{startup.pk}|{startup.password or ''}|{startup.email}", algorithm="sha256").hexdigest()[:32]


def password_token(startup: Startup) -> str:
    return signing.TimestampSigner(salt=_LINK_SALT).sign_object({"s": startup.pk, "f": _fingerprint(startup)})


def password_link(startup: Startup) -> str:
    # The page URL (the frontend's set-password page), not the JSON endpoint under /api/.
    return settings.SITE_URL + reverse("startup_set_password", args=[password_token(startup)])


def startup_from_password_token(token: str, *, lock: bool = False) -> Startup:
    """The startup a link belongs to, or StartupError: tampered, expired, used or inactive."""
    max_age = timedelta(hours=settings.STARTUP_PASSWORD_LINK_HOURS)
    try:
        data = signing.TimestampSigner(salt=_LINK_SALT).unsign_object(token, max_age=max_age)
    except signing.SignatureExpired as error:
        raise StartupError(LINK_EXPIRED) from error
    except (signing.BadSignature, ValueError, TypeError) as error:
        raise StartupError(LINK_INVALID) from error
    if not isinstance(data, dict):
        raise StartupError(LINK_INVALID)
    queryset = Startup.objects.select_for_update() if lock else Startup.objects
    startup = queryset.filter(pk=data.get("s"), is_active=True).first()
    if startup is None:
        raise StartupError(LINK_INVALID)
    if not constant_time_compare(str(data.get("f", "")), _fingerprint(startup)):
        raise StartupError(LINK_USED)
    return startup


def set_password_with_link(token: str, password: str, password_again: str) -> Startup:
    """Set the startup's password from a valid link. The link (and every other link) then stops working."""
    with transaction.atomic():
        startup = startup_from_password_token(token, lock=True)  # locked: two tabs cannot both use it
        if password != password_again:
            raise ValidationError({"password_again": ["The two passwords do not match."]})
        try:
            validate_password(password, startup)
        except ValidationError as error:
            raise ValidationError({"password": error.messages}) from error
        startup.set_password(password)
        startup.save(update_fields=["password", "updated_at"])
    security_log.info("Startup password set with a link: startup_id=%s", startup.pk)
    return startup


def send_password_link(member: TBIMember, startup: Startup) -> None:
    """Email a startup its "set your password" link (Admin only). Inactive startups get nothing."""
    require_permission(member, Permission.MANAGE_USERS)
    if not startup.is_active:
        raise StartupError("This startup is not active, so it cannot log in.")
    emails.send_set_password_email(startup, password_link(startup), settings.STARTUP_PASSWORD_LINK_HOURS)
    security_log.info("Startup set-password link sent: startup_id=%s by member_id=%s", startup.pk, member.pk)


REVIEW_STARTUP_DEFAULT_NAME = "TEST Startup (review)"


def create_review_startup(*, email: str, name: str = REVIEW_STARTUP_DEFAULT_NAME) -> Startup:
    """One TEST startup for the hosted review version (`manage.py create_review_startup`, ASSUMPTIONS 396), while the
    Navigatu sync is off. No navigatu_id (so a later real sync never matches it by ID), active, no password: the
    "set your password" link is emailed right away (the same email as the Admin's button). The name always starts
    with "TEST" so nobody takes it for a real startup. Refused when the email is already used.
    """
    name = " ".join(name.split()) or REVIEW_STARTUP_DEFAULT_NAME
    if not name.upper().startswith("TEST"):
        name = f"TEST {name}"
    email = email.strip().lower()
    try:
        validate_email(email)
    except ValidationError:
        raise StartupError("Enter a valid email address.") from None
    if Startup.objects.filter(email__iexact=email).exists():
        raise StartupError("A startup with this email already exists.")
    with transaction.atomic():  # the startup exists only when its link could be emailed
        startup = Startup.objects.create(name=name[:255], email=email, is_active=True)
        emails.send_set_password_email(startup, password_link(startup), settings.STARTUP_PASSWORD_LINK_HOURS)
    security_log.info("Review startup created: startup_id=%s by the create_review_startup command", startup.pk)
    return startup


# --- Startup login (separate from staff) ------------------------------------------------------------


def _session_hash(startup: Startup) -> str:
    return salted_hmac("startups.session", startup.password or "", algorithm="sha256").hexdigest()


def authenticate_startup(request: HttpRequest, email: str, password: str) -> Startup:
    """Check startup credentials, with the same lockout rules and messages as staff (own counters).

    Unknown email, wrong password, inactive startup and a startup without a password all give
    the same message.
    """
    email = (email or "").strip().lower()
    ip = client_ip(request)
    if is_locked_out(email, ip, STARTUP_SCOPE):
        raise LoginError(lockout_message())
    startup = Startup.objects.filter(email__iexact=email).first()
    if startup is None:
        make_password(password)  # spend the same time as a real check, so timing reveals nothing
    if startup is None or not startup.is_active or not startup.check_password(password):
        record_failed_login(email, ip, STARTUP_SCOPE)
        raise LoginError(INVALID_LOGIN_MESSAGE)
    clear_failed_logins(email, STARTUP_SCOPE)
    return startup


def login_startup(request: HttpRequest, startup: Startup) -> None:
    """Start a fresh session for the startup. This also ends any staff login in this browser."""
    request.session.flush()
    request.session[STARTUP_SESSION_KEY] = startup.pk
    request.session[STARTUP_SESSION_HASH_KEY] = _session_hash(startup)


def logout_startup(request: HttpRequest) -> None:
    request.session.flush()


def current_startup(request: HttpRequest) -> Startup | None:
    """The logged-in startup: still active, and its password unchanged since it logged in."""
    startup_id = request.session.get(STARTUP_SESSION_KEY)
    if not startup_id:
        return None
    startup = Startup.objects.filter(pk=startup_id, is_active=True).first()
    if startup is None or not constant_time_compare(request.session.get(STARTUP_SESSION_HASH_KEY, ""), _session_hash(startup)):
        request.session.flush()
        return None
    return startup


# --- Reading: profile, registrations, staff list ------------------------------------------------------


def registrations_for(startup: Startup) -> QuerySet:
    """Registrations made while logged in as this startup, plus older public registrations with
    its email (ignoring case). Read only: old rows are not changed by reading them."""
    return (
        Registration.objects.filter(Q(startup=startup) | Q(startup__isnull=True, email__iexact=startup.email))
        .select_related("activity", "activity__status", "participant_category")
        .order_by("-registration_date", "-registration_id")
    )


def list_startups(filters: dict | None = None, page: int | str | None = 1, per_page: int = DEFAULT_PAGE_SIZE) -> PageResult:
    """Staff list: q (name or email), maturity_stage, is_active (True/False). 20 per page, by name."""
    filters = filters or {}
    queryset = Startup.objects.all()
    search = (filters.get("q") or "").strip()
    if search:
        queryset = queryset.filter(Q(name__icontains=search) | Q(email__icontains=search))
    if filters.get("maturity_stage"):
        queryset = queryset.filter(maturity_stage__iexact=filters["maturity_stage"].strip())
    if filters.get("is_active") is not None:
        queryset = queryset.filter(is_active=filters["is_active"])
    return _paginate(queryset.order_by("name", "startup_id"), page, per_page)


def maturity_stages() -> list[str]:
    """The maturity stages the synced startups have (Navigatu's plain text, ignoring case and outer spaces), by name:
    the choices of the staff list's filter. Read only; the stages are placeholders until the TBI confirms them."""
    stages: dict[str, str] = {}
    for stage in Startup.objects.exclude(maturity_stage__isnull=True).values_list("maturity_stage", flat=True):
        if cleaned := stage.strip():
            stages.setdefault(cleaned.lower(), cleaned)
    return sorted(stages.values(), key=str.lower)


def register_as_startup(
    startup: Startup, activity: Activity, *, participant_category=None, answers: dict | None = None,
    now: datetime | None = None,
) -> Registration:
    """A logged-in startup registers: the same service and rules as public registration (Step 5),
    with name, email and contact number from the startup's profile, and registration.startup set."""
    from registrations.services import register  # registrations already depends on startups.models

    if not startup.is_active:
        raise PermissionDenied("This startup account is not active.")
    return register(
        activity,
        name=startup.name,
        email=startup.email,
        contact_number=startup.contact_number,
        participant_category=participant_category,
        answers=answers,
        now=now,
        startup=startup,
    )
