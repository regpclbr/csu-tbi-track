"""Response headers for pages whose URL carries a personal token (phase F3): the evaluation page and the
startup set-password page."""

from collections.abc import Callable
from functools import wraps

from django.http import HttpRequest, HttpResponse
from django.utils.cache import add_never_cache_headers

# Not "no-referrer": with it, browsers send "Origin: null" when the page posts its own form, and Django's CSRF
# check refuses the evaluation and the new password (on HTTPS it also needs the Referer). "same-origin" sends
# nothing to any other site, so the token in the URL never leaves this site (ASSUMPTIONS 287).
TOKEN_PAGE_REFERRER_POLICY = "same-origin"


def private_token_page(view: Callable) -> Callable:
    """The page's URL is a personal link, so:

    - Referrer-Policy: same-origin (TOKEN_PAGE_REFERRER_POLICY): the URL (with its token) is never sent to another
      site, whatever the project-wide SECURE_REFERRER_POLICY says;
    - X-Robots-Tag: noindex, nofollow: never in a search engine (base.html also has the robots meta tag);
    - Cache-Control: no-store (never_cache): a shared computer's browser does not keep the page.
    """

    @wraps(view)
    def wrapper(request: HttpRequest, *args, **kwargs) -> HttpResponse:
        response = view(request, *args, **kwargs)
        response["Referrer-Policy"] = TOKEN_PAGE_REFERRER_POLICY
        response["X-Robots-Tag"] = "noindex, nofollow"
        add_never_cache_headers(response)
        return response

    return wrapper
