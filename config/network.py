"""The client's IP address, used by the login lockout and the public registration rate limit.

Without a reverse proxy the client is REMOTE_ADDR. Behind one proxy (for example nginx), every
request arrives from the proxy's own address, so REMOTE_ADDR would be the same for everybody: one
person's failed logins would then lock everyone out. Set TRUSTED_PROXY_HEADER (e.g.
"X-Forwarded-For" or "X-Real-IP") to the header the proxy fills in, and only then.

Never turn it on without a proxy that sets the header itself: anyone can send the header, so the
"IP" would be whatever the client typed.
"""

import ipaddress

from django.conf import settings
from django.http import HttpRequest


def _meta_key(header: str) -> str:
    """'X-Forwarded-For' -> 'HTTP_X_FORWARDED_FOR' (how Django stores request headers in META)."""
    return "HTTP_" + header.strip().upper().replace("-", "_")


def _valid_ip(value: str) -> str | None:
    try:
        return str(ipaddress.ip_address(value.strip()))
    except ValueError:
        return None


def client_ip(request: HttpRequest) -> str:
    """The client's IP address: REMOTE_ADDR, or the trusted proxy header when it is configured.

    With X-Forwarded-For the LAST address is used: the proxy appends the address it saw, while
    the addresses before it come from the client and can be made up. (One proxy is assumed.)
    A missing or malformed header falls back to REMOTE_ADDR.
    """
    remote = request.META.get("REMOTE_ADDR", "unknown")
    header = settings.TRUSTED_PROXY_HEADER
    if not header:
        return remote
    values = [part for part in request.META.get(_meta_key(header), "").split(",") if part.strip()]
    return (_valid_ip(values[-1]) if values else None) or remote
