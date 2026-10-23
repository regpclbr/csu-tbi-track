"""Strict parsing of request values that every app shares."""

from django.core.exceptions import ValidationError

TRUE_FALSE_MESSAGE = "must be true or false."


def parse_bool(value, name: str, *, default: bool | None = None) -> bool | None:
    """True/False from JSON (true, false) or text ("true", "false", any case).

    Anything else (1, "yes", "maybe", a list) raises ValidationError({name: ...}), which the API
    turns into a 400. A missing value (None or "") returns `default`.
    """
    if value is None or value == "":
        return default
    if isinstance(value, bool):
        return value
    if isinstance(value, str) and value.strip().lower() in ("true", "false"):
        return value.strip().lower() == "true"
    raise ValidationError({name: [f"{name} {TRUE_FALSE_MESSAGE}"]})
