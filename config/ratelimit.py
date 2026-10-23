"""The shared rate limiter: counts attempts per (scope, kind, value) in a fixed time window.

Counters live in Django's cache (the database cache, like the login lockout), so every server process
shares them. Used by the public registration API and the staff "forgot password" form. The IP address
always comes from config.network.client_ip().
"""

from django.core.cache import cache


def _key(scope: str, kind: str, value: str) -> str:
    return f"rate-limit:{scope}:{kind}:{value}"


def count_attempt(scope: str, kind: str, value: str, limit: int, window_minutes: int) -> bool:
    """Count one attempt for `value` (an IP address or a lowercased email) in `scope`.

    The window starts with the first attempt and lasts `window_minutes`. Returns True while `value`
    is still within `limit` attempts in the current window (this attempt included), False once over it.
    """
    key, seconds = _key(scope, kind, value), window_minutes * 60
    cache.add(key, 0, seconds)  # starts a new window only if none is running
    try:
        attempts = cache.incr(key)
    except ValueError:  # the window expired between add() and incr()
        cache.set(key, 1, seconds)
        attempts = 1
    return attempts <= limit
