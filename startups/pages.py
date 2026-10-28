"""Display helpers for the staff Startups pages (phase F5): the list and a startup's page. Display only: the list and
the history come from startups.services (list_startups, registrations_for); syncing and set-password links are the
services' too (can_manage_users). This module reads the filters and words the sync result.
"""

from dataclasses import dataclass, field
from urllib.parse import urlencode

from django.core.exceptions import ValidationError
from django.http import QueryDict

from config.parsing import parse_bool

from . import services
from .models import Startup


@dataclass
class StartupFilters:
    """The list's filters from the query string. A value that cannot be read is ignored with a note (as item 262)."""

    q: str = ""
    maturity_stage: str = ""
    is_active: bool | None = None
    page: str = "1"
    ignored: list[str] = field(default_factory=list)

    def for_service(self) -> dict:
        return {"q": self.q, "maturity_stage": self.maturity_stage, "is_active": self.is_active}

    def query(self) -> str:
        """The filters as a query string, without the page (for the pager)."""
        values = {"q": self.q, "maturity_stage": self.maturity_stage}
        if self.is_active is not None:
            values["is_active"] = "true" if self.is_active else "false"
        return urlencode({key: value for key, value in values.items() if value})

    @property
    def any_set(self) -> bool:
        return bool(self.q or self.maturity_stage or self.is_active is not None)


def parse_filters(query: QueryDict) -> StartupFilters:
    ignored: list[str] = []
    try:
        is_active = parse_bool(query.get("is_active"), "is_active")
    except ValidationError:  # anything but true/false
        ignored.append("active")
        is_active = None
    return StartupFilters(
        q=query.get("q", "").strip()[:100], maturity_stage=query.get("maturity_stage", "").strip()[:100],
        is_active=is_active, page=query.get("page", "1"), ignored=ignored,
    )


def startup_list(filters: StartupFilters) -> dict:
    result = services.list_startups(filters.for_service(), page=filters.page)
    return {"result": result, "startups": result.items}


def history(startup: Startup) -> dict:
    """The startup's registrations (services.registrations_for: linked, plus older public ones with its email) and
    how many were attended."""
    registrations = list(services.registrations_for(startup))
    return {"registrations": registrations, "attended": sum(r.attendance_confirmed for r in registrations)}


def sync_message(result: dict) -> tuple[str, str]:
    """The sync result in words, and whether it is a "success" or a "warning" (the deactivation guard held back)."""
    text = (
        f"Sync finished: {result['created']} new, {result['updated']} updated, {result['unchanged']} unchanged, "
        f"{result['deactivated']} deactivated, {result['skipped']} skipped."
    )
    if result.get("deactivation_skipped"):
        return f"{text} Nobody was deactivated: {result['deactivation_skipped_reason']}", "warning"
    return text, "success"
