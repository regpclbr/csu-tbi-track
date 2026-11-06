"""Staff permission checks, written once and used everywhere (views, services, admin).

Permissions come from the member's ROLE flags. Hiding a button in a template is never
enough: every view and service that needs a permission calls these functions.
"""

from collections.abc import Callable
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
    MANAGE_USERS = "can_manage_users"  # Admin Console: users, roles, master data, certificate design/signatories
    APPROVE_DOCUMENTS = "can_approve_documents"  # approve or return documents (ASSUMPTIONS 344)


PERMISSION_LABELS = {
    Permission.CREATE_ACTIVITY: "create or edit activities",
    Permission.MANAGE_USERS: "use the Admin Console",
    Permission.APPROVE_DOCUMENTS: "approve or return documents",
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


def is_htmx(request: HttpRequest) -> bool:
    """True for a request sent by HTMX (a partial update of a page)."""
    return request.headers.get("HX-Request") == "true"


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
        if not is_staff_member(request.user):
            if is_api_path(request.path):
                return JsonResponse({"error": LOGIN_REQUIRED_MESSAGE}, status=401)
            login_redirect = redirect_to_login(_current_page(request))
            if is_htmx(request):  # 401 + HX-Redirect: HTMX loads the login page as a full page
                return htmx_redirect(request, login_redirect.url, status=401)
            return login_redirect
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
