"""Startup portal access check. Staff checks live in accounts/permissions.py."""

from collections.abc import Callable
from functools import wraps

from django.http import HttpRequest, HttpResponse

from config.json import JsonResponse

from . import services


def startup_login_required(view: Callable[..., HttpResponse]) -> Callable[..., HttpResponse]:
    """Only a logged-in, active startup. Staff sessions are refused (they are never startups).

    The startup is passed to the view as `request.startup`.
    """

    @wraps(view)
    def wrapper(request: HttpRequest, *args, **kwargs) -> HttpResponse:
        if request.user.is_authenticated:
            return JsonResponse({"error": "Staff accounts cannot use the startup portal."}, status=403)
        startup = services.current_startup(request)
        if startup is None:
            return JsonResponse({"error": "Startup login required."}, status=401)
        request.startup = startup
        return view(request, *args, **kwargs)

    return wrapper


STAFF_REFUSED = "Staff accounts cannot use the startup portal. Log out of the staff account first."


def portal_page_required(view: Callable[..., HttpResponse]) -> Callable[..., HttpResponse]:
    """The HTML twin of startup_login_required, for the portal pages (phase F6).

    A staff session gets the 403 page (staff never use the portal); nobody logged in is sent to the portal login
    (an HTMX request gets 401 with HX-Redirect). The startup is passed to the view as `request.startup`.
    """
    from django.contrib.auth.views import redirect_to_login
    from django.core.exceptions import PermissionDenied
    from django.urls import reverse

    from accounts.permissions import _current_page, htmx_redirect, is_htmx

    @wraps(view)
    def wrapper(request: HttpRequest, *args, **kwargs) -> HttpResponse:
        if request.user.is_authenticated:
            raise PermissionDenied(STAFF_REFUSED)
        startup = services.current_startup(request)
        if startup is None:
            login = redirect_to_login(_current_page(request), reverse("startups:portal_login_page"))
            return htmx_redirect(request, login.url, status=401) if is_htmx(request) else login
        request.startup = startup
        return view(request, *args, **kwargs)

    return wrapper
