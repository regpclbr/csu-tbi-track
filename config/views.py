"""Project-wide error views."""

from django.http import HttpRequest, HttpResponse, JsonResponse


def permission_denied(request: HttpRequest, exception: Exception | None = None) -> HttpResponse:
    """403 with a clear reason: JSON for the API, plain text for pages."""
    message = str(exception) if exception and str(exception) else "You do not have permission to do this."
    if "/api/" in request.path:
        return JsonResponse({"error": message}, status=403)
    return HttpResponse(f"403 Forbidden: {message}", status=403, content_type="text/plain; charset=utf-8")
