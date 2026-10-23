"""Project-wide views: error pages, the public page placeholder and the staff lookups."""

from django.http import HttpRequest, HttpResponse
from django.shortcuts import render
from django.views.decorators.http import require_GET

from accounts.permissions import staff_login_required

from .json import JsonResponse
from .lookups import staff_lookups


def permission_denied(request: HttpRequest, exception: Exception | None = None) -> HttpResponse:
    """403 with a clear reason: JSON for the API, plain text for pages."""
    message = str(exception) if exception and str(exception) else "You do not have permission to do this."
    if "/api/" in request.path:
        return JsonResponse({"error": message}, status=403)
    return HttpResponse(f"403 Forbidden: {message}", status=403, content_type="text/plain; charset=utf-8")


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
