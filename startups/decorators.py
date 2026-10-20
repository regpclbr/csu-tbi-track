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
