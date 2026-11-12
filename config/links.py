"""Internal "come back here" links (ASSUMPTIONS 359): the page the member is on, and a check that a `next` value is a
path on this site. Display only."""

from urllib.parse import urlsplit

from django.http import HttpRequest
from django.utils.http import url_has_allowed_host_and_scheme


def is_internal_path(request: HttpRequest, target: str) -> bool:
    """True only for a path on this site ("/documents/?view=all"): never a full or scheme-relative address
    ("https://…", "//evil.example"), a backslash trick ("/\\evil.example") or another scheme ("javascript:…")."""
    if not target or not target.startswith("/") or target.startswith(("//", "/\\")):
        return False
    return url_has_allowed_host_and_scheme(target, allowed_hosts={request.get_host()}, require_https=request.is_secure())


def current_page(request: HttpRequest) -> str:
    """The page the member is looking at, as a path with its query (filters and view kept). For an HTMX request
    that is the browser's page (the HX-Current-URL header), not the partial's URL."""
    if request.headers.get("HX-Request") == "true" and (current := request.headers.get("HX-Current-URL")):
        parts = urlsplit(current)
        if parts.netloc in ("", request.get_host()):
            path = parts.path + (f"?{parts.query}" if parts.query else "")
            if is_internal_path(request, path):
                return path
    return request.get_full_path()


def activity_tab_url(activity_id: int, tab: str) -> str:
    """The activity page on one tab: /activities/<id>/?tab=<tab>. Every action of a tab ends here (ASSUMPTIONS 362)."""
    from django.urls import reverse

    return f"{reverse('activities:detail', args=[activity_id])}?tab={tab}"


def post_only_or_tab(tab: str):
    """Decorator for the POST-only actions under /activities/<activity_id>/... (instead of require_POST): a GET (an
    action address reloaded, bookmarked or typed) is sent to the activity's tab instead of an error; for HTMX the
    redirect is an HX-Redirect. Other methods: 405. The view must take activity_id from the URL."""
    from functools import wraps

    from django.http import HttpResponse, HttpResponseNotAllowed
    from django.shortcuts import redirect

    def decorator(view):
        @wraps(view)
        def wrapper(request: HttpRequest, *args, **kwargs):
            if request.method in ("GET", "HEAD"):
                target = activity_tab_url(kwargs["activity_id"], tab)
                if request.headers.get("HX-Request") == "true":
                    response = HttpResponse()
                    response["HX-Redirect"] = target
                    return response
                return redirect(target)
            if request.method != "POST":
                return HttpResponseNotAllowed(["POST"])
            return view(request, *args, **kwargs)

        return wrapper

    return decorator
