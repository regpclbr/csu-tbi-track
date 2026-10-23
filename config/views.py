"""Project-wide views: error pages, the public page placeholder and the staff lookups."""

from django.http import HttpRequest, HttpResponse
from django.shortcuts import render
from django.views import defaults
from django.views.decorators.http import require_GET

from accounts.permissions import staff_login_required

from .json import JsonResponse, is_api_path
from .lookups import staff_lookups


def permission_denied(request: HttpRequest, exception: Exception | None = None) -> HttpResponse:
    """403 with a clear reason: JSON for the API, plain text for pages."""
    message = str(exception) if exception and str(exception) else "You do not have permission to do this."
    if is_api_path(request.path):
        return JsonResponse({"error": message}, status=403)
    return HttpResponse(f"403 Forbidden: {message}", status=403, content_type="text/plain; charset=utf-8")


NOT_FOUND = "Not found."
SERVER_ERROR = "Something went wrong on the server. Please try again later."


def page_not_found(request: HttpRequest, exception: Exception | None = None) -> HttpResponse:
    """404 (used when DEBUG is off): JSON for the API, Django's normal page otherwise.

    Our own Http404 messages ("Activity not found.", "The file is missing.") are shown; Django's
    unknown-URL details are not."""
    if is_api_path(request.path):
        message = exception.args[0] if exception and exception.args and isinstance(exception.args[0], str) else NOT_FOUND
        return JsonResponse({"error": message}, status=404)
    return defaults.page_not_found(request, exception)


def server_error(request: HttpRequest) -> HttpResponse:
    """500 (used when DEBUG is off): JSON for the API, Django's normal page otherwise. Never the
    error itself (it is in the log)."""
    if is_api_path(request.path):
        return JsonResponse({"error": SERVER_ERROR}, status=500)
    return defaults.server_error(request)


@require_GET
def page_placeholder(request: HttpRequest, **kwargs) -> HttpResponse:
    """The public pages (/register/<id>/, /evaluate/<token>/, /startups/set-password/<token>/) until
    the frontend phase builds them. It never reads the activity or the token, so it reveals nothing."""
    return render(request, "pages/placeholder.html")


@staff_login_required
@require_GET
def lookups_api(request: HttpRequest) -> JsonResponse:
    """GET: id and name of activity types, participant categories, document types, activity and
    document statuses, and roles (any active staff member)."""
    return JsonResponse(staff_lookups())
