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

from .emails import send_invitation_email, send_password_reset_email
from .models import Role, TBIMember
from .permissions import Permission, require_permission

security_log = logging.getLogger("accounts.security")

INVALID_LOGIN_MESSAGE = "Invalid email or password."


# --- Login and brute-force lockout ------------------------------------------------


class LoginError(Exception):
    """Login refused. The message is safe to show: it never says whether the email exists."""


# Staff and startups use the same limits and messages but separate counters ("scope").
STAFF_SCOPE, STARTUP_SCOPE = "staff", "startup"


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
    - Only TBI_MEMBER accounts are checked; STARTUP accounts can never log in here.
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
        raise MemberManagementError("At least one active Admin must always exist.")


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

    member = save_new_member_with_invitation(TBIMember(name=name.strip(), email=email, role=role))
    security_log.info(
        "Member created: member_id=%s role=%s by member_id=%s", member.pk, role.role_name, acting_member.pk
    )
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
    security_log.info(
        "Role changed: member_id=%s %s -> %s by member_id=%s",
        member.pk, old_role, new_role.role_name, acting_member.pk,
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
    security_log.info("Member deactivated: member_id=%s by member_id=%s", member.pk, acting_member.pk)
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
    security_log.info("Password reset by an Admin: member_id=%s by member_id=%s", member.pk, acting_member.pk)
    return member


@transaction.atomic
def reactivate_member(acting_member: TBIMember, member: TBIMember) -> TBIMember:
    """Admin Console: let a deactivated member log in again."""
    require_permission(acting_member, Permission.MANAGE_USERS)
    member.is_active = True
    member.save(update_fields=["is_active", "updated_at"])
    security_log.info("Member reactivated: member_id=%s by member_id=%s", member.pk, acting_member.pk)
    return member


MEMBERS_PAGE_SIZE = 20


def list_members(filters: dict | None = None, page: int | str | None = 1, per_page: int = MEMBERS_PAGE_SIZE):
    """Admin Console list (read only): q (name or email), role (a role ID), is_active (True/False). By name, 20 a page."""
    from activities.services import _paginate  # activities imports accounts: import here, not at the top

    filters = filters or {}
    queryset = TBIMember.objects.select_related("role")
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
    acting_member: TBIMember, role: Role, *, can_create_activity: bool, can_manage_users: bool
) -> Role:
    """Admin Console: change a role's two permission flags (roles are never renamed or deleted).

    Refused if taking can_manage_users away from this role would leave no active Admin.
    """
    require_permission(acting_member, Permission.MANAGE_USERS)
    if role.can_manage_users and not can_manage_users:
        ensure_admins_remain_without_role(role)
    old = (role.can_create_activity, role.can_manage_users)
    role.can_create_activity, role.can_manage_users = can_create_activity, can_manage_users
    role.save(update_fields=["can_create_activity", "can_manage_users"])
    security_log.info(
        "Role permissions changed: role=%s (create_activity, manage_users) %s -> %s by member_id=%s",
        role.role_name, old, (can_create_activity, can_manage_users), acting_member.pk,
    )
    return role


@transaction.atomic
def ensure_admins_remain_without_role(role: Role) -> None:
    """Refuse if no active member outside `role` can manage users."""
    others = _active_admins().exclude(role=role).select_for_update()
    if not others.exists():
        raise MemberManagementError(
            f'At least one active Admin must always exist: no active member outside "{role.role_name}" can manage users.'
        )
