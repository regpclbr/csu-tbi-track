"""The startup's "set your password" page (public, no login; phase F3). The Admin emails the link
(services.send_password_link); the page calls the same services as /api/startups/set-password/<token>/."""

from django.contrib.auth.password_validation import password_validators_help_texts
from django.core.exceptions import ValidationError
from django.http import HttpRequest, HttpResponse
from django.shortcuts import redirect, render
from django.views.decorators.http import require_GET, require_http_methods

from config.headers import private_token_page

from . import services


def _link_refused(request: HttpRequest, reason: str) -> HttpResponse:
    """Tampered, expired, used or replaced link: the service's reason (which says to ask the TBI for a new one).
    A link that was never valid answers 404; the others 200. Never the startup's name or email."""
    status = 404 if reason == services.LINK_INVALID else 200
    context = {
        "title": "This link cannot be used",
        "text": reason if reason != services.LINK_INVALID else f"{reason} Ask the TBI to send you a new one.",
        "icon": "link",
        "tone": "warning",
        "private_page": True,
    }
    return render(request, "public/notice.html", context, status=status)


@private_token_page
@require_http_methods(["GET", "POST"])
def set_password_page(request: HttpRequest, token: str) -> HttpResponse:
    """GET: the startup's name and email with the new-password form. POST: set the password
    (services.set_password_with_link: Django's password rules; the link then stops working)."""
    try:
        startup = services.startup_from_password_token(token)
    except services.StartupError as error:
        return _link_refused(request, str(error))

    errors: dict[str, str] = {}
    status = 200
    if request.method == "POST":
        try:
            services.set_password_with_link(token, request.POST.get("password", ""), request.POST.get("password_again", ""))
        except services.StartupError as error:  # used meanwhile (another tab)
            return _link_refused(request, str(error))
        except ValidationError as error:
            errors = {key: " ".join(messages) for key, messages in error.message_dict.items()}
            status = 400
        else:
            return redirect("startup_set_password_done")

    context = {
        "startup": startup,
        "errors": errors,
        "focus": next((key for key in ("password", "password_again") if key in errors), None),
        "password_rules": password_validators_help_texts(),
        "private_page": True,
    }
    return render(request, "public/set_password.html", context, status=status)


@private_token_page
@require_GET
def set_password_done(request: HttpRequest) -> HttpResponse:
    """"Your password is set." No token and no startup data in this URL."""
    return render(request, "public/set_password_done.html", {"private_page": True})
