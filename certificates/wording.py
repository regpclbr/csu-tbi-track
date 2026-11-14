"""The certificate wording (ASSUMPTIONS 370): placeholders, optional parts in [ ... ], length limits, filling.

Plain text only. Each of the four fields (title, line before the name, main sentence, closing line) may use these
placeholders, which the system fills in:

    {name} {activity} {date} {venue} {given_date} {organization}     and {topic} (Certificate of Appreciation only)

Text in square brackets prints only when every placeholder inside it has a value, e.g. "held on {date}[ at {venue}]."
leaves out " at ..." for an activity without a venue. Brackets cannot be nested and must be closed; { and } are used
only around a placeholder. Line breaks are refused (each field is one line on the certificate).

The filled text is returned as a plain string: the PDF template escapes it ({{ }}), so typed HTML prints as text.
Rules about who may change the wording and what it refreshes live in certificates.services.
"""

import re
from dataclasses import dataclass

from django.db import IntegrityError, transaction

from .models import CertificateText

CertificateType = CertificateText.CertificateType

FIELDS = ("title", "line_before_name", "main_sentence", "closing_line")
FIELD_LABELS = {
    "title": "Title",
    "line_before_name": "Line before the name",
    "main_sentence": "Main sentence",
    "closing_line": "Closing line",
}
REQUIRED = {"title", "main_sentence"}
# Owner decision (ASSUMPTIONS 370): limits that keep the A4 layout intact. The same numbers are the column sizes.
MAX_LENGTHS = {field: CertificateText._meta.get_field(field).max_length for field in FIELDS}

PLACEHOLDERS = {
    "name": "the recipient's printed name",
    "activity": "the activity title",
    "date": "the activity dates (e.g. November 5, 2026 to November 6, 2026)",
    "venue": "the venue",
    "topic": "the resource person's topic (Certificate of Appreciation only)",
    "given_date": "the date it is given (the activity's last day)",
    "organization": "the name of the office",
}
ONLY_FOR = {"topic": CertificateType.APPRECIATION}

# The wording printed before it became editable (seeded by migration certificates/0013; nothing changes until someone
# edits it). Smart quotes are plain characters.
DEFAULT_WORDING = {
    CertificateType.PARTICIPATION: {
        "title": "Certificate of Participation",
        "line_before_name": "is presented to",
        "main_sentence": "for participating in “{activity}” held on {date}[ at {venue}].",
        "closing_line": "Given on {given_date} at {organization}.",
    },
    CertificateType.APPRECIATION: {
        "title": "Certificate of Appreciation",
        "line_before_name": "is presented to",
        "main_sentence": (
            "in grateful recognition of their valuable contribution as resource person[ on “{topic}”] during "
            "“{activity}” held on {date}[ at {venue}]."
        ),
        "closing_line": "Given on {given_date} at {organization}.",
    },
}

_TOKEN = re.compile(r"\{([^{}]*)\}")
_SPACES = re.compile(r"\s{2,}")


def placeholder_list(certificate_type: str | None = None) -> list[dict]:
    """[{"token": "{name}", "meaning": ...}] for the help next to the fields (only the ones the type may use)."""
    return [
        {"token": "{" + key + "}", "meaning": meaning}
        for key, meaning in PLACEHOLDERS.items()
        if certificate_type is None or ONLY_FOR.get(key, certificate_type) == certificate_type
    ]


def _allowed_text(certificate_type: str) -> str:
    return ", ".join(item["token"] for item in placeholder_list(certificate_type))


def check_field(field: str, value: str, certificate_type: str) -> str | None:
    """Why `value` cannot be saved in `field`, or None. Checks: required, one line, length, braces, placeholders known
    and allowed for the type, brackets closed and not nested."""
    label = FIELD_LABELS[field]
    if not value:
        return f"Enter the {label.lower()}." if field in REQUIRED else None
    if any(ch in value for ch in "\r\n") or any(ord(ch) < 32 for ch in value):
        return f"Write the {label.lower()} on one line."
    if len(value) > MAX_LENGTHS[field]:
        return f"The {label.lower()} can be at most {MAX_LENGTHS[field]} characters (now {len(value)})."
    for name in _TOKEN.findall(value):
        key = name.strip()
        if key not in PLACEHOLDERS:
            return f"Unknown placeholder {{{name}}}. You can use: {_allowed_text(certificate_type)}."
        if key != name:
            return f"Write the placeholder without spaces: {{{key}}}."
        if ONLY_FOR.get(key, certificate_type) != certificate_type:
            return f"{{{key}}} can only be used on a Certificate of Appreciation."
    if "{" in _TOKEN.sub("", value) or "}" in _TOKEN.sub("", value):
        return "Use { and } only around a placeholder, for example {activity}."
    depth = 0
    for ch in value:
        if ch == "[":
            if depth:
                return "Square brackets cannot be inside other square brackets."
            depth = 1
        elif ch == "]":
            if not depth:
                return "A ] has no [ before it."
            depth = 0
    if depth:
        return "A [ is not closed with ]."
    return None


def clean(values: dict, certificate_type: str) -> tuple[dict, dict]:
    """(cleaned values with outer spaces trimmed, {field: error}) for the four fields."""
    cleaned = {field: (values.get(field) or "").strip() for field in FIELDS}
    errors = {}
    for field in FIELDS:
        if error := check_field(field, cleaned[field], certificate_type):
            errors[field] = error
    return cleaned, errors


def fill(template: str, values: dict[str, str]) -> str:
    """The text with its placeholders filled in. A [ ... ] part is kept (without the brackets) only when every
    placeholder inside it has a value. Runs of spaces become one. The template must have passed check_field."""
    def replace(text: str) -> str:
        return _TOKEN.sub(lambda match: values.get(match.group(1), ""), text)

    out, part, optional = [], [], False
    for ch in template:
        if ch == "[":
            out.append(replace("".join(part)))
            part, optional = [], True
        elif ch == "]" and optional:
            text = "".join(part)
            if all(values.get(key) for key in _TOKEN.findall(text)):
                out.append(replace(text))
            part, optional = [], False
        else:
            part.append(ch)
    out.append(replace("".join(part)))
    return _SPACES.sub(" ", "".join(out)).strip()


@dataclass(frozen=True)
class FilledText:
    """The four lines as printed (plain text; the template escapes them)."""

    title: str
    line_before_name: str
    main_sentence: str
    closing_line: str


def fill_all(text, values: dict[str, str]) -> FilledText:
    """Fill every field of a CERTIFICATE_TEXT row (or any object / dict with the four fields)."""
    get = text.get if isinstance(text, dict) else lambda field: getattr(text, field)
    return FilledText(**{field: fill(get(field) or "", values) for field in FIELDS})


def default_text(certificate_type: str) -> CertificateText:
    """The current default wording of a type. Normally seeded by the migration; created from DEFAULT_WORDING when it is
    missing (a database emptied by a test), so a certificate always has a wording."""
    row = CertificateText.objects.filter(certificate_type=certificate_type, activity__isnull=True, is_current=True).first()
    if row is not None:
        return row
    try:
        with transaction.atomic():
            return CertificateText.objects.create(certificate_type=certificate_type, **DEFAULT_WORDING[certificate_type])
    except IntegrityError:  # created at the same moment by someone else
        return CertificateText.objects.get(certificate_type=certificate_type, activity__isnull=True, is_current=True)


def current_text_for(activity_id: int | None, certificate_type: str) -> CertificateText:
    """The wording a new or refreshed certificate gets: the activity's current custom text, else the default."""
    if activity_id is not None:
        custom = CertificateText.objects.filter(activity_id=activity_id, certificate_type=certificate_type,
                                                is_current=True).first()
        if custom is not None:
            return custom
    return default_text(certificate_type)
