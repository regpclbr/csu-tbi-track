"""Staff permission checks, written once and used everywhere (views, services, admin).

Permissions come from the member's ROLE flags. Hiding a button in a template is never
enough: every view and service that needs a permission calls these functions.
"""

from collections.abc import Callable
from enum import StrEnum
from functools import wraps

from django.contrib.auth.views import redirect_to_login
from django.core.exceptions import PermissionDenied
from django.http import HttpRequest, HttpResponse

from config.json import JsonResponse, is_api_path

from .models import TBIMember


class Permission(StrEnum):
    """Role flags that grant access. The value is the ROLE column name."""

    CREATE_ACTIVITY = "can_create_activity"  # create or edit activities
    MANAGE_USERS = "can_manage_users"  # Admin Console: users, roles, master data, certificate design/signatories


PERMISSION_LABELS = {
    Permission.CREATE_ACTIVITY: "create or edit activities",
    Permission.MANAGE_USERS: "use the Admin Console",
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
    if not has_permission(user, permission):
        raise PermissionDenied(f"You do not have permission to {PERMISSION_LABELS[permission]}.")


ViewFunc = Callable[..., HttpResponse]


LOGIN_REQUIRED_MESSAGE = "Login required."


def staff_login_required(view: ViewFunc) -> ViewFunc:
    """Allow only logged-in, active staff members. Others are sent to the staff login page, or,
    for the JSON API, get 401 {"error": "Login required."} (an API call cannot follow a redirect to a page)."""

    @wraps(view)
    def wrapper(request: HttpRequest, *args, **kwargs) -> HttpResponse:
        if not is_staff_member(request.user):
            if is_api_path(request.path):
                return JsonResponse({"error": LOGIN_REQUIRED_MESSAGE}, status=401)
            return redirect_to_login(request.get_full_path())
        return view(request, *args, **kwargs)

    return wrapper


def permission_required(permission: Permission) -> Callable[[ViewFunc], ViewFunc]:
    """Staff login required, plus `permission`; returns 403 when the role lacks it."""

    def decorator(view: ViewFunc) -> ViewFunc:
        @wraps(view)
        def wrapper(request: HttpRequest, *args, **kwargs) -> HttpResponse:
            require_permission(request.user, permission)
            return view(request, *args, **kwargs)

        return staff_login_required(wrapper)

    return decorator
