"""The checks every public registration POST gets, shared by the JSON endpoint (/api/register/<id>/) and the
HTML page (/register/<id>/), so both are limited and protected exactly the same way (phase F3)."""

from django.http import HttpRequest

from config.network import client_ip

from . import services


def guard_registration_request(request: HttpRequest, email, honeypot) -> None:
    """Rate limit (per IP address and per email) and honeypot check of one public registration attempt.

    The IP address comes only from config.network.client_ip(). Raises services.RegistrationRateLimited
    (show as 429) or services.RegistrationError (honeypot filled in). Call it before services.register();
    the service itself is never limited (startup portal, Django admin, demo seed).
    """
    services.guard_public_registration(client_ip(request), email, honeypot)
