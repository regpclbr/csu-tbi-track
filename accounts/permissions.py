"""Staff permission checks, written once and used everywhere (views, services, admin).

Permissions come from the member's ROLE flags. Hiding a button in a template is never
enough: every view and service that needs a permission calls these functions.
"""

from collections.abc import Callable
from contextvars import ContextVar
from enum import StrEnum
from functools import wraps
from urllib.parse import urlsplit

from django.contrib.auth.views import redirect_to_login
from django.core.exceptions import PermissionDenied
from django.http import HttpRequest, HttpResponse

from config.json import JsonResponse, is_api_path

from .models import TBIMember


class Permission(StrEnum):
    """Role flags that grant access. The value is the ROLE column name."""

    CREATE_ACTIVITY = "can_create_activity"  # create or edit activities
    MANAGE_USERS = "can_manage_users"  # Admin Console: users, roles, master data, certificate design
    APPROVE_DOCUMENTS = "can_approve_documents"  # approve or return documents (ASSUMPTIONS 344)
    APPROVE_CERTIFICATES = "can_approve_certificates"  # approve certificates (ASSUMPTIONS 357)
    DELETE_ACTIVITIES = "can_delete_activities"  # delete activities without real records (ASSUMPTIONS 389)
    MANAGE_SIGNATORIES = "can_manage_signatories"  # the signatory list, signature images, defaults (ASSUMPTIONS 418)
    ASSIGN_WORK = "can_assign_work"  # assign one piece of work to a member (WORK_ASSIGNMENT, ASSUMPTIONS 437)


PERMISSION_LABELS = {
    Permission.CREATE_ACTIVITY: "create or edit activities",
    Permission.MANAGE_USERS: "use the Admin Console",
    Permission.APPROVE_DOCUMENTS: "approve or return documents",
    Permission.APPROVE_CERTIFICATES: "approve certificates",
    Permission.DELETE_ACTIVITIES: "delete activities",
    Permission.MANAGE_SIGNATORIES: "manage certificate signatories",
    Permission.ASSIGN_WORK: "assign work to members",
}


def is_staff_member(user: object) -> bool:
    """True only for a logged-in, active TBI_MEMBER.

    A STARTUP is a different model and is never a TBI_MEMBER, so it always gets False.
    """
    return isinstance(user, TBIMember) and user.is_authenticated and user.is_active


def require_staff_member(user: object) -> None:
    """Raise PermissionDenied (HTTP 403) unless `user` is an active staff member (no role flag needed)."""
    if not is_staff_member(user):
        raise PermissionDenied("Only active TBI staff members can do this.")


def has_permission(user: object, permission: Permission) -> bool:
    """True if `user` is an active staff member whose role grants `permission`."""
    return is_staff_member(user) and bool(getattr(user.role, permission.value))


def require_permission(user: object, permission: Permission) -> None:
    """Raise PermissionDenied (HTTP 403) unless `user` has `permission`."""
    _note_grant(user, None)
    if not has_permission(user, permission):
        raise PermissionDenied(f"You do not have permission to {PERMISSION_LABELS[permission]}.")


def has_any_permission(user: object, *permissions: Permission) -> bool:
    """True if `user` has at least one of `permissions` (e.g. an activity's signatories: create activities OR approve
    certificates, ASSUMPTIONS 363)."""
    return any(has_permission(user, permission) for permission in permissions)


def require_any_permission(user: object, *permissions: Permission) -> None:
    """Raise PermissionDenied (HTTP 403) unless `user` has at least one of `permissions`."""
    _note_grant(user, None)
    if not has_any_permission(user, *permissions):
        labels = " or ".join(PERMISSION_LABELS[permission] for permission in permissions)
        raise PermissionDenied(f"You do not have permission to do this (you must be able to {labels}).")


# --- Work assignments: permission for ONE piece of work (ASSUMPTIONS 437) --------------------------------------------
# A member without a role flag may still do one task when the Director assigned it to them (an OPEN WORK_ASSIGNMENT,
# approvals.assignments). The role never changes. Which kinds of assignment give which permission on which object:
OBJECT_KINDS = {
    (Permission.CREATE_ACTIVITY, "activity"): ("activity",),
    (Permission.APPROVE_CERTIFICATES, "activity"): ("certificate_approval",),
    (Permission.APPROVE_DOCUMENTS, "document"): ("document_review",),
    (Permission.APPROVE_DOCUMENTS, "submission"): ("submission_review",),
    (Permission.CREATE_ACTIVITY, None): ("report",),  # a report to prepare: generating reports while it is open
}
SIGNATORY_CHOICE = "signatory_choice"  # can_approve_certificates on an activity, only for choosing who signs

# Who the current member is acting for: (member ID, the assigner) after a permission was granted by an assignment,
# else None. Every permission check sets it, staff_login_required clears it at the start of each request, and History
# (activities.services._write_audit), SUBMISSION_EVENT and the audit lines read it through acting_on_behalf_of().
_ACTING: ContextVar[tuple[int, object] | None] = ContextVar("tbi_acting_on_behalf", default=None)


def _note_grant(user: object, assigner: object | None) -> None:
    _ACTING.set((user.pk, assigner) if assigner is not None and getattr(user, "pk", None) else None)


def acting_on_behalf_of(member: object | None):
    """The member `member` is acting for right now (the assigner of the assignment that allowed the last permission
    check), or None (their own role flag, or System)."""
    value = _ACTING.get()
    if value is None or member is None or value[0] != getattr(member, "pk", None):
        return None
    return value[1]


def _object_name(obj: object | None) -> str | None:
    return obj._meta.model_name if obj is not None else None


def assignment_for(user: object, permission: Permission, obj: object | None = None, kind: str | None = None):
    """The member's OPEN assignment that gives `permission` on `obj` (or None). `kind` narrows it (signatory_choice)."""
    if not is_staff_member(user):
        return None
    kinds = (kind,) if kind else OBJECT_KINDS.get((permission, _object_name(obj)), ())
    if not kinds:
        return None
    from approvals.assignments import open_assignment  # approvals imports this module

    return open_assignment(user, kinds, obj)


def has_permission_for(user: object, permission: Permission, obj: object | None = None, kind: str | None = None) -> bool:
    """True when the role flag gives `permission`, or an open assignment gives it for THIS object only."""
    return has_permission(user, permission) or assignment_for(user, permission, obj, kind) is not None


def require_permission_for(user: object, permission: Permission, obj: object | None = None, kind: str | None = None):
    """Like require_permission, but an open assignment for `obj` also allows it. Returns the assignment that allowed it
    (then everything the service writes is "on behalf of" its assigner), or None when the role flag did."""
    if has_permission(user, permission):
        _note_grant(user, None)
        return None
    assignment = assignment_for(user, permission, obj, kind)
    if assignment is None:
        _note_grant(user, None)
        raise PermissionDenied(f"You do not have permission to {PERMISSION_LABELS[permission]}.")
    _note_grant(user, assignment.assigned_by)
    return assignment


def has_any_permission_for(user: object, permissions: tuple, obj: object | None = None) -> bool:
    return any(has_permission_for(user, permission, obj) for permission in permissions)


def require_any_permission_for(user: object, permissions: tuple, obj: object | None = None):
    """require_any_permission with assignments: the first permission the role has, else the first an assignment gives."""
    if has_any_permission(user, *permissions):
        _note_grant(user, None)
        return None
    for permission in permissions:
        assignment = assignment_for(user, permission, obj)
        if assignment is not None:
            _note_grant(user, assignment.assigned_by)
            return assignment
    _note_grant(user, None)
    labels = " or ".join(PERMISSION_LABELS[permission] for permission in permissions)
    raise PermissionDenied(f"You do not have permission to do this (you must be able to {labels}).")


ViewFunc = Callable[..., HttpResponse]


LOGIN_REQUIRED_MESSAGE = "Login required."


def is_htmx(request: HttpRequest) -> bool:
    """True for a request sent by HTMX (a partial update of a page). Not for HTMX's history restore (back/forward when
    the page is not in its cache): that request needs the whole page, or a partial would replace the page."""
    return (request.headers.get("HX-Request") == "true"
            and request.headers.get("HX-History-Restore-Request") != "true")


def htmx_redirect(request: HttpRequest, url: str, status: int = 200) -> HttpResponse:
    """Tell HTMX to load `url` as a full page (HX-Redirect), instead of swapping a redirect's
    page into a part of the current one."""
    response = HttpResponse(status=status)
    response["HX-Redirect"] = url
    return response


def _current_page(request: HttpRequest) -> str:
    """The page the member is on: for HTMX, the page that sent the request (HX-Current-URL), so the
    login sends them back there and not to a partial."""
    current = request.headers.get("HX-Current-URL", "")
    if is_htmx(request) and current:
        parts = urlsplit(current)
        if parts.netloc in ("", request.get_host()):
            return parts.path + (f"?{parts.query}" if parts.query else "")
    return request.get_full_path()


def staff_login_required(view: ViewFunc) -> ViewFunc:
    """Allow only logged-in, active staff members. Others are sent to the staff login page, or,
    for the JSON API, get 401 {"error": "Login required."} (an API call cannot follow a redirect to a page).
    An HTMX request gets 401 with an HX-Redirect header to the login page."""

    @wraps(view)
    def wrapper(request: HttpRequest, *args, **kwargs) -> HttpResponse:
        _ACTING.set(None)  # a new request never inherits "on behalf of" (ASSUMPTIONS 437)
        if not is_staff_member(request.user):
            if is_api_path(request.path):
                return JsonResponse({"error": LOGIN_REQUIRED_MESSAGE}, status=401)
            login_redirect = redirect_to_login(_current_page(request))
            if is_htmx(request):  # 401 + HX-Redirect: HTMX loads the login page as a full page
                return htmx_redirect(request, login_redirect.url, status=401)
            return login_redirect
        return view(request, *args, **kwargs)

    return wrapper


def object_permission_required(permission: Permission, loader: Callable[..., object],
                               kind: str | None = None) -> Callable[[ViewFunc], ViewFunc]:
    """Staff login required, plus `permission` from the role OR from an open assignment for the object
    `loader(**url kwargs)` returns (ASSUMPTIONS 437; the loader gives 404 for a missing object). 403 otherwise. The
    service checks again (require_permission_for) and logs "on behalf of" when the assignment allowed it."""

    def decorator(view: ViewFunc) -> ViewFunc:
        @wraps(view)
        def wrapper(request: HttpRequest, *args, **kwargs) -> HttpResponse:
            require_permission_for(request.user, permission, loader(**kwargs), kind)
            return view(request, *args, **kwargs)

        return staff_login_required(wrapper)

    return decorator


def permission_required(permission: Permission) -> Callable[[ViewFunc], ViewFunc]:
    """Staff login required, plus `permission`; returns 403 when the role lacks it."""

    def decorator(view: ViewFunc) -> ViewFunc:
        @wraps(view)
        def wrapper(request: HttpRequest, *args, **kwargs) -> HttpResponse:
            require_permission(request.user, permission)
            return view(request, *args, **kwargs)

        return staff_login_required(wrapper)

    return decorator
