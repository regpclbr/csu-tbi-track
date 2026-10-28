"""Display helpers for the Admin Console pages (phase F6). Display only: the member actions are accounts.services',
the certificate design actions certificates.services', the checks accounts.system_status. This module reads the
members filters and prepares what the templates show.
"""

from dataclasses import dataclass, field
from urllib.parse import urlencode

from django.core.exceptions import ValidationError
from django.http import QueryDict
from django.urls import reverse

from certificates.models import CertificateSignatory, CertificateTemplate
from config.parsing import parse_bool

from .models import Role

# The tab row of every console page: (label, URL name, icon). Startups live on their own page (sync, set-password links).
TABS = [
    ("Members", "accounts:console", "users"),
    ("Roles", "accounts:console_roles", "shield"),
    ("Certificate design", "accounts:console_certificates", "award"),
    ("System status", "accounts:console_system", "server"),
]


def tabs(active: str) -> list[dict]:
    return [{"label": label, "url": reverse(name), "icon": icon, "active": name == active} for label, name, icon in TABS]


@dataclass
class MemberFilters:
    """The members list's filters from the query string. A value that cannot be read is ignored with a note."""

    q: str = ""
    role: str = ""  # a role ID, as text
    is_active: bool | None = None
    page: str = "1"
    ignored: list[str] = field(default_factory=list)

    def for_service(self) -> dict:
        return {"q": self.q, "role": int(self.role) if self.role else None, "is_active": self.is_active}

    def query(self) -> str:
        values = {"q": self.q, "role": self.role}
        if self.is_active is not None:
            values["is_active"] = "true" if self.is_active else "false"
        return urlencode({key: value for key, value in values.items() if value})

    @property
    def any_set(self) -> bool:
        return bool(self.q or self.role or self.is_active is not None)


def parse_member_filters(query: QueryDict) -> MemberFilters:
    ignored: list[str] = []
    try:
        is_active = parse_bool(query.get("is_active"), "is_active")
    except ValidationError:
        ignored.append("active")
        is_active = None
    role = query.get("role", "").strip()
    if role and not (role.isdigit() and Role.objects.filter(pk=role).exists()):
        ignored.append("role")
        role = ""
    return MemberFilters(q=query.get("q", "").strip()[:100], role=role, is_active=is_active, page=query.get("page", "1"), ignored=ignored)


def roles() -> list[Role]:
    return list(Role.objects.order_by("role_name"))


def role_from(value: str | None) -> Role | None:
    value = (value or "").strip()
    return Role.objects.filter(pk=value).first() if value.isdigit() else None


def certificate_design() -> dict:
    """Templates (newest first) and signatories by position, with the active one of each position."""
    templates = list(CertificateTemplate.objects.select_related("uploaded_by").order_by("-is_active", "-created_at"))
    positions = []
    for value, label in CertificateSignatory.Position.choices:
        people = list(CertificateSignatory.objects.filter(position=value).order_by("-is_active", "-created_at"))
        positions.append({"value": value, "label": label, "active": next((s for s in people if s.is_active), None), "signatories": people})
    return {
        "templates": templates,
        "active_template": next((t for t in templates if t.is_active), None),
        "positions": positions,
        "position_choices": CertificateSignatory.Position.choices,
    }
