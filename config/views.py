"""Project-wide views: error pages, the staff lookups, /healthz/ and /robots.txt."""

from django.db import connection
from django.http import HttpRequest, HttpResponse
from django.shortcuts import render
from django.views import defaults
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_GET

from accounts.permissions import staff_login_required

from .json import JsonResponse, is_api_path
from .lookups import staff_lookups


def permission_denied(request: HttpRequest, exception: Exception | None = None) -> HttpResponse:
    """403 with a clear reason: JSON for the API, an HTML page (errors/403.html) otherwise."""
    message = str(exception) if exception and str(exception) else "You do not have permission to do this."
    if is_api_path(request.path):
        return JsonResponse({"error": message}, status=403)
    return render(request, "errors/403.html", {"message": message}, status=403)


NOT_FOUND = "Not found."
SERVER_ERROR = "Something went wrong on the server. Please try again later."


def page_not_found(request: HttpRequest, exception: Exception | None = None) -> HttpResponse:
    """404 (used when DEBUG is off): JSON for the API, Django's normal page otherwise.

    Our own Http404 messages ("Activity not found.", "The file is missing.") are shown; Django's
    unknown-URL details are not."""
    if is_api_path(request.path):
        message = exception.args[0] if exception and exception.args and isinstance(exception.args[0], str) else NOT_FOUND
        return JsonResponse({"error": message}, status=404)
    return defaults.page_not_found(request, exception, template_name="errors/404.html")


def server_error(request: HttpRequest) -> HttpResponse:
    """500 (used when DEBUG is off): JSON for the API, Django's normal page otherwise. Never the
    error itself (it is in the log)."""
    if is_api_path(request.path):
        return JsonResponse({"error": SERVER_ERROR}, status=500)
    # errors/500.html is rendered without the request (no database, no user): it must not need them.
    return defaults.server_error(request, template_name="errors/500.html")


@staff_login_required
@require_GET
def lookups_api(request: HttpRequest) -> JsonResponse:
    """GET: id and name of activity types, participant categories, document types, activity and
    document statuses, and roles (any active staff member)."""
    return JsonResponse(staff_lookups())


@never_cache
@require_GET
def healthz(request: HttpRequest) -> HttpResponse:
    """GET /healthz/: "ok" when the app answers and the database does (Docker's health check, docs/DEPLOY.md).
    No login, no cookie, no detail about the error (503 "database unavailable" only)."""
    try:
        with connection.cursor() as cursor:
            cursor.execute("SELECT 1")
    except Exception:
        return HttpResponse("database unavailable\n", status=503, content_type="text/plain; charset=utf-8")
    return HttpResponse("ok\n", content_type="text/plain; charset=utf-8")


ROBOTS_TXT = "User-agent: *\nDisallow: /\n"


@require_GET
def robots_txt(request: HttpRequest) -> HttpResponse:
    """GET /robots.txt: no crawler should visit any page (ASSUMPTIONS 392)."""
    return HttpResponse(ROBOTS_TXT, content_type="text/plain; charset=utf-8")
