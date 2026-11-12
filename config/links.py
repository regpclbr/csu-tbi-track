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
