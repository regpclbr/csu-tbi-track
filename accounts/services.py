"""Business logic for TBI staff accounts, roles and login.

Views stay thin and call the functions in this module.
"""

import logging
import secrets

from django.conf import settings
from django.contrib.auth import authenticate
from django.contrib.auth.password_validation import validate_password
from django.core.cache import cache
from django.core.exceptions import ValidationError
from django.core.validators import validate_email
from django.db.models import Q
from django.db import transaction
from django.http import HttpRequest

from config.network import client_ip  # one client_ip() for the lockout and the registration limit
from config.ratelimit import count_attempt

from .emails import send_invitation_email, send_password_changed_email, send_password_reset_email
from .models import Position, Role, TBIMember
from .permissions import Permission, require_permission, require_staff_member

security_log = logging.getLogger("accounts.security")
# Account and role changes (ASSUMPTIONS 340): who did it, to whom or which role, old and new values. Never passwords.
audit_log = logging.getLogger("accounts.audit")


def _who(member: TBIMember) -> str:
    """How the audit log names a member: ID and email (staff accounts only; never a password)."""
    return f"member_id={member.pk} <{member.email}>"

INVALID_LOGIN_MESSAGE = "Invalid email or password."


# --- Login and brute-force lockout ------------------------------------------------


class LoginError(Exception):
    """Login refused. The message is safe to show: it never says whether the email exists."""


# The login lockout's counter scope (the startup scope went with the portal, ASSUMPTIONS 458).
STAFF_SCOPE = "staff"


def _counter_key(scope: str, kind: str, value: str) -> str:
    return f"login-failures:{scope}:{kind}:{value}"


def _lock_key(scope: str, kind: str, value: str) -> str:
    return f"login-locked:{scope}:{kind}:{value}"


def _lockout_seconds() -> int:
    return settings.LOGIN_LOCKOUT_MINUTES * 60


def lockout_message() -> str:
    return f"Too many failed attempts. Try again in {settings.LOGIN_LOCKOUT_MINUTES} minutes."


def is_locked_out(email: str, ip: str, scope: str = STAFF_SCOPE) -> bool:
    """True if this email or this IP address is locked out right now (for this kind of login)."""
    return bool(cache.get(_lock_key(scope, "email", email)) or cache.get(_lock_key(scope, "ip", ip)))


def _record_failure(scope: str, kind: str, value: str, limit: int, email: str, ip: str) -> None:
    """Count one failed login; lock out `value` when it reaches `limit` failures."""
    key = _counter_key(scope, kind, value)
    cache.add(key, 0, _lockout_seconds())  # start a new counting window if none
    failures = cache.incr(key)
    if failures >= limit:
        cache.set(_lock_key(scope, kind, value), True, _lockout_seconds())
        cache.delete(key)
        security_log.warning(
            "Login lockout (%s): %s=%s after %d failed attempts (email=%s, ip=%s), locked for %d minutes.",
            scope, kind, value, failures, email, ip, settings.LOGIN_LOCKOUT_MINUTES,
        )


def record_failed_login(email: str, ip: str, scope: str = STAFF_SCOPE) -> None:
    """Count a failed login for the email and for the IP address (Step 3 limits)."""
    _record_failure(scope, "email", email, settings.LOGIN_MAX_FAILED_ATTEMPTS, email, ip)
    _record_failure(scope, "ip", ip, settings.LOGIN_MAX_FAILED_ATTEMPTS_PER_IP, email, ip)


def clear_failed_logins(email: str, scope: str = STAFF_SCOPE) -> None:
    cache.delete(_counter_key(scope, "email", email))


def authenticate_member(request: HttpRequest, email: str, password: str) -> TBIMember:
    """Check staff credentials and return the member, or raise LoginError.

    - Wrong password, unknown email and deactivated account all give the same message.
    - Repeated failures lock the email (and, at a higher limit, the IP) for a short time.
    - Only TBI_MEMBER accounts are checked.
    """
    email = email.strip().lower()
    ip = client_ip(request)
    if is_locked_out(email, ip):
        raise LoginError(lockout_message())

    # Django's ModelBackend returns None for unknown emails, wrong passwords and
    # inactive members alike, and takes about the same time for each.
    member = authenticate(request, username=email, password=password)
    if member is None:
        record_failed_login(email, ip)
        raise LoginError(INVALID_LOGIN_MESSAGE)

    clear_failed_logins(email)
    return member


def password_reset_allowed(ip: str, email: str) -> bool:
    """The "forgot password" rate limit (shared limiter, config/ratelimit.py). Counts this request for
    the email (lowercased) and for the IP address; False when either is over its limit. Logged as a
    WARNING with the IP address only, never the email."""
    window = settings.PASSWORD_RESET_RATE_WINDOW_MINUTES
    email_ok = count_attempt("password-reset", "email", email.strip().lower(), settings.PASSWORD_RESET_MAX_REQUESTS_PER_EMAIL, window)
    ip_ok = count_attempt("password-reset", "ip", ip, settings.PASSWORD_RESET_MAX_REQUESTS_PER_IP, window)
    if not (email_ok and ip_ok):
        security_log.warning(
            "Forgot-password rate limit reached (ip=%s, by %s): no email sent.", ip, "ip" if not ip_ok else "email"
        )
    return email_ok and ip_ok


# --- Passwords ------------------------------------------------------------------------


def change_password(member: TBIMember, old_password: str, new_password: str) -> None:
    """Change a member's own password and clear must_change_password.

    Raises ValidationError if the old password is wrong, the new one is the same,
    or the new one fails Django's password validators.
    """
    if not member.check_password(old_password):
        raise ValidationError("Your current password is incorrect.")
    if old_password == new_password:
        raise ValidationError("The new password must be different from the current one.")
    validate_password(new_password, member)
    member.set_password(new_password)
    member.must_change_password = False
    member.save(update_fields=["password", "must_change_password", "updated_at"])
    security_log.info("Password changed: member_id=%s", member.pk)


def generate_temporary_password(member: TBIMember | None = None) -> str:
    """A random temporary password (16 characters) that passes Django's password validators."""
    while True:
        password = secrets.token_urlsafe(12)
        try:
            validate_password(password, member)
            return password
        except ValidationError:
            continue  # extremely rare; just draw another one


# --- Admin Console: user management ------------------------------------------------


class MemberManagementError(Exception):
    """A user-management action was refused (for example, a guardrail)."""


LAST_ADMIN_REFUSED = "At least one active System Administrator must always exist."


def _active_admins():
    """Active members whose role can manage users (the Admins)."""
    return TBIMember.objects.filter(is_active=True, role__can_manage_users=True)


def _is_active_admin(member: TBIMember) -> bool:
    return member.is_active and member.role.can_manage_users


@transaction.atomic
def ensure_another_admin_remains(member: TBIMember) -> None:
    """Refuse if `member` is the last active Admin.

    The admin rows are locked (SELECT ... FOR UPDATE) so two admins removing each
    other at the same moment cannot leave the system with no Admin.
    """
    admin_ids = list(_active_admins().select_for_update().values_list("pk", flat=True))
    if _is_active_admin(member) and admin_ids == [member.pk]:
        raise MemberManagementError(LAST_ADMIN_REFUSED)


def save_new_member_with_invitation(member: TBIMember) -> TBIMember:
    """Give an unsaved member a temporary password, save them, and email the invitation.

    The password is hashed before saving and is only ever placed in the email.
    """
    temporary_password = generate_temporary_password(member)
    member.set_password(temporary_password)
    member.must_change_password = True
    member.is_active = True
    member.save()
    send_invitation_email(member, temporary_password)
    return member


INVITATION_NOT_SENT = "The invitation could not be emailed; the member was not added. Try again."


@transaction.atomic
def create_member(acting_member: TBIMember, *, name: str, email: str, role: Role) -> TBIMember:
    """Admin Console: create a staff member and email them a temporary password."""
    require_permission(acting_member, Permission.MANAGE_USERS)
    if not name.strip():
        raise MemberManagementError("Enter the member's name.")
    email = TBIMember.objects.normalize_email(email.strip()).lower()
    try:
        validate_email(email)
    except ValidationError:
        raise MemberManagementError("Enter a valid email address.") from None
    if TBIMember.objects.filter(email__iexact=email).exists():
        raise MemberManagementError("A member with this email already exists.")

    try:
        with transaction.atomic():  # the member is saved only when the invitation could be emailed
            member = save_new_member_with_invitation(TBIMember(name=name.strip(), email=email, role=role))
    except Exception as error:  # SMTP / email API failure: nothing is saved, the Admin tries again
        audit_log.warning("Member invitation not emailed (%s): member not created, by %s", type(error).__name__,
                          _who(acting_member))
        raise MemberManagementError(INVITATION_NOT_SENT) from error
    audit_log.info("Member created: %s role=%r by %s", _who(member), role.role_name, _who(acting_member))
    return member


FIRST_ADMIN_EXISTS = (
    "An active System Administrator already exists. Sign in with that account and invite people from "
    "Admin Console → Members instead."
)


@transaction.atomic
def create_first_admin(*, email: str, name: str, password: str) -> TBIMember:
    """The very first System Administrator of a clean database (`manage.py create_first_admin`, ASSUMPTIONS 395).

    Run on the server's console, so there is no acting member. Refused once any active member can manage users (the
    admin rows are locked, so two consoles cannot both create one). The password is checked with the password
    validators and never logged; the person typed it themselves, so no forced change at first sign-in. Every other
    member is invited from the Admin Console (temporary password + forced change).
    """
    if list(_active_admins().select_for_update().values_list("pk", flat=True)):
        raise MemberManagementError(FIRST_ADMIN_EXISTS)
    name = name.strip()
    if not name:
        raise MemberManagementError("Enter the name.")
    email = TBIMember.objects.normalize_email(email.strip()).lower()
    try:
        validate_email(email)
    except ValidationError:
        raise MemberManagementError("Enter a valid email address.") from None
    if TBIMember.objects.filter(email__iexact=email).exists():
        raise MemberManagementError("A member with this email already exists.")
    try:
        validate_password(password, TBIMember(email=email, name=name))
    except ValidationError as error:
        raise MemberManagementError(" ".join(error.messages)) from None
    try:
        member = TBIMember.objects.create_superuser(email=email, name=name, password=password)
    except ValueError as error:  # the roles are not loaded yet
        raise MemberManagementError(f"{error} Run: python manage.py seed_data") from None
    audit_log.info("First System Administrator created: %s by the create_first_admin command", _who(member))
    return member


@transaction.atomic
def change_member_role(acting_member: TBIMember, member: TBIMember, new_role: Role) -> TBIMember:
    """Admin Console: give a member a different role.

    Refused if it would take away the last active Admin.
    """
    require_permission(acting_member, Permission.MANAGE_USERS)
    if not new_role.can_manage_users:
        ensure_another_admin_remains(member)
    old_role = member.role.role_name
    member.role = new_role
    member.save(update_fields=["role", "updated_at"])
    audit_log.info(
        "Member role changed: %s role %r -> %r by %s", _who(member), old_role, new_role.role_name, _who(acting_member)
    )
    return member


@transaction.atomic
def deactivate_member(acting_member: TBIMember, member: TBIMember) -> TBIMember:
    """Admin Console: deactivate a member (members are never deleted).

    An Admin cannot deactivate their own account, and the last active Admin
    cannot be deactivated.
    """
    require_permission(acting_member, Permission.MANAGE_USERS)
    if member.pk == acting_member.pk:
        raise MemberManagementError("You cannot deactivate your own account.")
    ensure_another_admin_remains(member)
    member.is_active = False
    member.save(update_fields=["is_active", "updated_at"])
    audit_log.info("Member deactivated: %s is_active True -> False by %s", _who(member), _who(acting_member))
    return member


@transaction.atomic
def reset_member_password(acting_member: TBIMember, member: TBIMember) -> TBIMember:
    """Admin Console: give a member a new temporary password by email (like the invitation).

    The member must choose a new password at the next sign-in (must_change_password), and every
    session they have open ends (Django checks the password hash on each request). Refused for a
    deactivated member and for your own account (use "change password" instead).
    """
    require_permission(acting_member, Permission.MANAGE_USERS)
    if member.pk == acting_member.pk:
        raise MemberManagementError("Use Change password for your own account.")
    if not member.is_active:
        raise MemberManagementError("This member is deactivated. Reactivate the account first.")
    temporary_password = generate_temporary_password(member)
    member.set_password(temporary_password)
    member.must_change_password = True
    member.save(update_fields=["password", "must_change_password", "updated_at"])
    send_password_reset_email(member, temporary_password)  # inside the transaction: no email, no reset
    audit_log.info(
        "Password reset by an administrator: %s must_change_password -> True (temporary password emailed) by %s",
        _who(member), _who(acting_member),
    )
    return member


@transaction.atomic
def reactivate_member(acting_member: TBIMember, member: TBIMember) -> TBIMember:
    """Admin Console: let a deactivated member log in again."""
    require_permission(acting_member, Permission.MANAGE_USERS)
    was_active = member.is_active
    member.is_active = True
    member.save(update_fields=["is_active", "updated_at"])
    audit_log.info("Member reactivated: %s is_active %s -> True by %s", _who(member), was_active, _who(acting_member))
    return member


MEMBERS_PAGE_SIZE = 20


def list_members(filters: dict | None = None, page: int | str | None = 1, per_page: int = MEMBERS_PAGE_SIZE):
    """Admin Console list (read only): q (name or email), role (a role ID), is_active (True/False). By name, 20 a page."""
    from activities.services import _paginate  # activities imports accounts: import here, not at the top

    filters = filters or {}
    queryset = TBIMember.objects.select_related("role", "position")
    search = (filters.get("q") or "").strip()
    if search:
        queryset = queryset.filter(Q(name__icontains=search) | Q(email__icontains=search))
    if filters.get("role"):
        queryset = queryset.filter(role_id=filters["role"])
    if filters.get("is_active") is not None:
        queryset = queryset.filter(is_active=filters["is_active"])
    return _paginate(queryset.order_by("name", "member_id"), page, per_page)


# --- Admin Console: role permission flags -------------------------------------------------


@transaction.atomic
def update_role_permissions(
    acting_member: TBIMember,
    role: Role,
    *,
    can_create_activity: bool,
    can_manage_users: bool,
    can_approve_documents: bool | None = None,
    can_approve_certificates: bool | None = None,
    can_delete_activities: bool | None = None,
    can_manage_signatories: bool | None = None,
    can_assign_work: bool | None = None,
) -> Role:
    """Change a role's permission flags (roles are never renamed or deleted). Used by the Django admin and the
    Admin Console roles screen. A flag passed as None keeps its current value.

    Refused if the role would get can_manage_users together with a flag it may not have (separation of duties,
    check_role_flags) or if taking can_manage_users away from this role would leave no active System Administrator.
    Every changed flag is one line on accounts.audit.
    """
    require_permission(acting_member, Permission.MANAGE_USERS)
    if can_approve_documents is None:
        can_approve_documents = role.can_approve_documents
    if can_approve_certificates is None:
        can_approve_certificates = role.can_approve_certificates
    if can_delete_activities is None:
        can_delete_activities = role.can_delete_activities
    if can_manage_signatories is None:
        can_manage_signatories = role.can_manage_signatories
    if can_assign_work is None:
        can_assign_work = role.can_assign_work
    check_role_flags(
        can_create_activity=can_create_activity,
        can_manage_users=can_manage_users,
        can_approve_documents=can_approve_documents,
        can_approve_certificates=can_approve_certificates,
        can_manage_signatories=can_manage_signatories,
        can_assign_work=can_assign_work,
    )
    if role.can_manage_users and not can_manage_users:
        ensure_admins_remain_without_role(role)
    flags = ("can_create_activity", "can_manage_users", "can_approve_documents", "can_approve_certificates",
             "can_delete_activities", "can_manage_signatories", "can_assign_work")
    old = {flag: getattr(role, flag) for flag in flags}
    role.can_create_activity, role.can_manage_users = can_create_activity, can_manage_users
    role.can_approve_documents = can_approve_documents
    role.can_approve_certificates = can_approve_certificates
    role.can_delete_activities = can_delete_activities
    role.can_manage_signatories = can_manage_signatories
    role.can_assign_work = can_assign_work
    role.save(update_fields=list(flags))
    for flag, before in old.items():
        if before != getattr(role, flag):
            audit_log.info(
                "Role flag changed: role=%r %s %s -> %s by %s", role.role_name, flag, before, getattr(role, flag),
                _who(acting_member),
            )
    return role


@transaction.atomic
def set_role_document_approval(acting_member: TBIMember, role: Role, enabled: bool) -> Role:
    """Admin Console roles screen: turn can_approve_documents on or off for one role (the other flags stay).
    Turning it off for the last role that has active approvers is allowed: the roles screen then shows a warning."""
    role = Role.objects.select_for_update().get(pk=role.pk)
    return update_role_permissions(
        acting_member,
        role,
        can_create_activity=role.can_create_activity,
        can_manage_users=role.can_manage_users,
        can_approve_documents=enabled,
    )


@transaction.atomic
def set_role_certificate_approval(acting_member: TBIMember, role: Role, enabled: bool) -> Role:
    """Admin Console roles screen: turn can_approve_certificates on or off for one role (the other flags stay).
    No last-approver guard (like documents): the roles screen warns when nobody can approve certificates."""
    role = Role.objects.select_for_update().get(pk=role.pk)
    return update_role_permissions(
        acting_member,
        role,
        can_create_activity=role.can_create_activity,
        can_manage_users=role.can_manage_users,
        can_approve_certificates=enabled,
    )


@transaction.atomic
def set_role_activity_creation(acting_member: TBIMember, role: Role, enabled: bool) -> Role:
    """Admin Console roles screen: turn can_create_activity on or off for one role (the other flags stay; ASSUMPTIONS
    388). Refused for a role that manages users (separation of duties, check_role_flags). No last-member guard (like the
    approvals): the roles screen warns when no active member can create activities."""
    role = Role.objects.select_for_update().get(pk=role.pk)
    return update_role_permissions(
        acting_member,
        role,
        can_create_activity=enabled,
        can_manage_users=role.can_manage_users,
    )


@transaction.atomic
def set_role_activity_deletion(acting_member: TBIMember, role: Role, enabled: bool) -> Role:
    """Admin Console roles screen: turn can_delete_activities on or off for one role (ASSUMPTIONS 389). Allowed together
    with can_manage_users (the System Administrator cleans up mistakes and test activities). No last-member guard: the
    roles screen warns when nobody can delete activities."""
    role = Role.objects.select_for_update().get(pk=role.pk)
    return update_role_permissions(
        acting_member,
        role,
        can_create_activity=role.can_create_activity,
        can_manage_users=role.can_manage_users,
        can_delete_activities=enabled,
    )


@transaction.atomic
def set_role_signatory_management(acting_member: TBIMember, role: Role, enabled: bool) -> Role:
    """Admin Console roles screen: turn can_manage_signatories on or off for one role (ASSUMPTIONS 418). Refused for a
    role that manages users (separation of duties, check_role_flags). No last-member guard (like the approvals): the
    roles screen warns when no active member can manage signatories."""
    role = Role.objects.select_for_update().get(pk=role.pk)
    return update_role_permissions(
        acting_member,
        role,
        can_create_activity=role.can_create_activity,
        can_manage_users=role.can_manage_users,
        can_manage_signatories=enabled,
    )


@transaction.atomic
def set_role_work_assignment(acting_member: TBIMember, role: Role, enabled: bool) -> Role:
    """Admin Console roles screen: turn can_assign_work on or off for one role (ASSUMPTIONS 437). Refused for a role that
    manages users (separation of duties, check_role_flags). No last-member guard: the roles screen warns when no active
    member can assign work. Open assignments stay until they are done or removed."""
    role = Role.objects.select_for_update().get(pk=role.pk)
    return update_role_permissions(
        acting_member,
        role,
        can_create_activity=role.can_create_activity,
        can_manage_users=role.can_manage_users,
        can_assign_work=enabled,
    )


def active_work_assigners():
    """Active members whose role can assign work (the roles screen warning)."""
    return TBIMember.objects.filter(is_active=True, role__can_assign_work=True)


def active_signatory_managers():
    """Active members whose role can manage the certificate signatories (the roles screen warning)."""
    return TBIMember.objects.filter(is_active=True, role__can_manage_signatories=True)


def active_activity_deleters():
    """Active members whose role can delete activities (the roles screen warning)."""
    return TBIMember.objects.filter(is_active=True, role__can_delete_activities=True)


def active_activity_creators():
    """Active members whose role can create and edit activities (the roles screen warning)."""
    return TBIMember.objects.filter(is_active=True, role__can_create_activity=True)


def active_certificate_approvers():
    """Active members whose role can approve certificates (the roles screen warning)."""
    return TBIMember.objects.filter(is_active=True, role__can_approve_certificates=True)


def active_document_approvers():
    """Active members whose role can approve documents (review notifications, the roles screen warning)."""
    return TBIMember.objects.filter(is_active=True, role__can_approve_documents=True)


BOTH_FLAGS_REFUSED = (
    "A role cannot both manage users and create activities (separation of duties). "
    "Turn one of the two flags off."
)
APPROVE_AND_MANAGE_REFUSED = (
    "A role cannot both manage users and approve documents (separation of duties). "
    "Turn one of the two flags off."
)
ASSIGN_AND_MANAGE_REFUSED = (
    "A role cannot both manage users and assign work (separation of duties). Turn one of the two flags off."
)
SIGNATORIES_AND_MANAGE_REFUSED = (
    "A role cannot both manage users and manage certificate signatories (separation of duties). "
    "Turn one of the two flags off."
)


def check_role_flags(
    *, can_create_activity: bool, can_manage_users: bool, can_approve_documents: bool = False,
    can_approve_certificates: bool = False, can_manage_signatories: bool = False, can_assign_work: bool = False,
) -> None:
    """Separation of duties (ASSUMPTIONS 339, 344, 418): a role with can_manage_users may have neither
    can_create_activity, nor can_approve_documents, nor can_manage_signatories (whoever manages accounts and role
    flags never also decides whose signature is printed). can_approve_certificates is allowed with it (ASSUMPTIONS 361):
    the System Administrator approves certificates, but never generates them or adds signatures. can_delete_activities
    is allowed with every flag (ASSUMPTIONS 389)."""
    if can_create_activity and can_manage_users:
        raise MemberManagementError(BOTH_FLAGS_REFUSED)
    if can_approve_documents and can_manage_users:
        raise MemberManagementError(APPROVE_AND_MANAGE_REFUSED)
    if can_manage_signatories and can_manage_users:
        raise MemberManagementError(SIGNATORIES_AND_MANAGE_REFUSED)
    if can_assign_work and can_manage_users:  # ASSUMPTIONS 437: whoever manages accounts never hands out work
        raise MemberManagementError(ASSIGN_AND_MANAGE_REFUSED)


@transaction.atomic
def ensure_admins_remain_without_role(role: Role) -> None:
    """Refuse if no active member outside `role` can manage users."""
    others = _active_admins().exclude(role=role).select_for_update()
    if not others.exists():
        raise MemberManagementError(
            "At least one active System Administrator must always exist: "
            f'no active member outside "{role.role_name}" can manage users.'
        )


# --- Positions (job titles) and My profile (ASSUMPTIONS 390) ----------------------------------------------------------
# A position is ONLY a displayed job title: no code reads it for a permission (the role decides what a member may do).

POSITION_NAME_MAX = 100
NAME_MAX = 255
WRONG_PASSWORD_LIMIT = 10  # wrong current passwords per member per hour on My profile (config.ratelimit)
PASSWORD_LIMIT_REACHED = "Too many attempts. Wait an hour, then try again."


def _position_key(name: str) -> str:
    return " ".join(name.split()).lower()


def validate_position_name(name: str, position: Position | None = None) -> str:
    """Trimmed, 1-100 characters, unique ignoring upper/lower case and spaces."""
    name = " ".join((name or "").split())
    if not name:
        raise ValidationError({"name": ["Type a position name."]})
    if len(name) > POSITION_NAME_MAX:
        raise ValidationError({"name": [f"A position name can be at most {POSITION_NAME_MAX} characters."]})
    others = Position.objects.exclude(pk=position.pk) if position else Position.objects.all()
    if any(_position_key(existing) == _position_key(name) for existing in others.values_list("name", flat=True)):
        raise ValidationError({"name": [f"A position named “{name}” already exists."]})
    return name


def add_position(acting_member: TBIMember, name: str) -> Position:
    """Admin Console: add a job title (can_manage_users)."""
    require_permission(acting_member, Permission.MANAGE_USERS)
    position = Position.objects.create(name=validate_position_name(name))
    audit_log.info("Position added: position_id=%s %r by %s", position.pk, position.name, _who(acting_member))
    return position


def rename_position(acting_member: TBIMember, position: Position, name: str) -> Position:
    """Admin Console: rename a job title; every member who has it shows the new name."""
    require_permission(acting_member, Permission.MANAGE_USERS)
    old, position.name = position.name, validate_position_name(name, position)
    position.save(update_fields=["name"])
    audit_log.info("Position renamed: position_id=%s %r -> %r by %s", position.pk, old, position.name, _who(acting_member))
    return position


def set_position_active(acting_member: TBIMember, position: Position, active: bool) -> Position:
    """Admin Console: deactivate (no longer offered; members who have it keep it) or reactivate. Never deleted."""
    require_permission(acting_member, Permission.MANAGE_USERS)
    if position.is_active != active:
        position.is_active = active
        position.save(update_fields=["is_active"])
        audit_log.info("Position %s: position_id=%s %r by %s", "reactivated" if active else "deactivated",
                       position.pk, position.name, _who(acting_member))
    return position


def offered_positions(member: TBIMember) -> list[Position]:
    """The My profile dropdown: active positions, plus the member's own even when it was deactivated."""
    positions = Position.objects.filter(is_active=True)
    if member.position_id:
        positions = positions | Position.objects.filter(pk=member.position_id)
    return list(positions.distinct().order_by("name"))


POSITION_NOT_OFFERED = "Choose a position from the list."


def set_member_position(acting_member: TBIMember, member: TBIMember, position_id: int | None) -> TBIMember:
    """Admin Console: set a member's position (ASSUMPTIONS 429): the official job title from the Positions list, shown
    under the name and printed under "Prepared by" on generated reports and forms. Same choices as My profile
    (offered_positions: active ones, plus the member's own); None = no position. Never a permission. Logged on
    accounts.audit (IDs only)."""
    require_permission(acting_member, Permission.MANAGE_USERS)
    position = None
    if position_id is not None:
        position = Position.objects.filter(pk=position_id).first()
        if position is None or (not position.is_active and position.pk != member.position_id):
            raise MemberManagementError(POSITION_NOT_OFFERED)
    new_id = position.pk if position else None
    if member.position_id != new_id:
        old_id = member.position_id
        member.position = position
        member.save(update_fields=["position", "updated_at"])
        audit_log.info("Member position changed: %s position %s -> %s by %s", _who(member), old_id, new_id,
                       _who(acting_member))
    return member


def update_own_profile(member: TBIMember, *, name: str, position_id: int | None) -> TBIMember:
    """My profile: the member changes their OWN name and position (any active staff member). Email and role are not
    changed here (only the System Administrator changes them). Logged on accounts.audit (IDs and names only)."""
    require_staff_member(member)
    name = " ".join((name or "").split())
    errors = {}
    if not name:
        errors["name"] = ["Type your name."]
    elif len(name) > NAME_MAX:
        errors["name"] = [f"Your name can be at most {NAME_MAX} characters."]
    position = None
    if position_id is not None:
        position = Position.objects.filter(pk=position_id).first()
        if position is None or (not position.is_active and position.pk != member.position_id):
            errors["position"] = [POSITION_NOT_OFFERED]
    if errors:
        raise ValidationError(errors)
    changes = []
    if member.name != name:
        changes.append(f"name {member.name!r} -> {name!r}")
    if member.position_id != (position.pk if position else None):
        changes.append(f"position {member.position_id} -> {position.pk if position else None}")
    if changes:
        member.name, member.position = name, position
        member.save(update_fields=["name", "position", "updated_at"])
        audit_log.info("Own profile changed: member_id=%s %s", member.pk, "; ".join(changes))
    return member


def signatory_named_like(member: TBIMember) -> bool:
    """True when an active certificate signatory has the member's name (ignoring case): My profile then says that
    certificates use the signatory list, so a name change here does not change what is printed."""
    from certificates.models import CertificateSignatory

    key = " ".join(member.name.split()).lower()
    return any(" ".join(name.split()).lower() == key
               for name in CertificateSignatory.objects.filter(is_active=True).values_list("name", flat=True))


def change_own_password(member: TBIMember, current_password: str, new_password1: str, new_password2: str) -> bool:
    """My profile: change your OWN password (current password, the new one twice, Django's password validators).
    Wrong current passwords are limited to WRONG_PASSWORD_LIMIT per hour per member. The caller keeps the session
    (update_session_auth_hash). Sends "Your password was changed" (never the password; printed while emails are not
    really sent). Returns True when that email was sent; a failed email never undoes the change."""
    require_staff_member(member)
    if not member.check_password(current_password or ""):
        if not count_attempt("own_password", "member", str(member.pk), WRONG_PASSWORD_LIMIT, 60):
            security_log.warning("Own password change rate limit reached: member_id=%s", member.pk)
            raise ValidationError({"current_password": [PASSWORD_LIMIT_REACHED]})
        raise ValidationError({"current_password": ["Your current password is not correct."]})
    if new_password1 != new_password2:
        raise ValidationError({"new_password2": ["The two new passwords do not match."]})
    try:
        change_password(member, current_password, new_password1)
    except ValidationError as error:
        raise ValidationError({"new_password1": error.messages}) from error
    audit_log.info("Own password changed: member_id=%s", member.pk)
    try:
        send_password_changed_email(member)
    except Exception as error:  # the password stays changed; the page says the email was not sent
        security_log.warning("Password-changed email not sent: member_id=%s (%s)", member.pk, type(error).__name__)
        return False
    return True
