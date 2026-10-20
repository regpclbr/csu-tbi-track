"""The ONLY place the system gets startup data from Navigatu.

Everything else calls `get_provider()` and works with plain `StartupRecord` objects, so it
never knows where the data comes from. Which class is used is set by
settings.STARTUP_PROVIDER (a dotted path); a real Navigatu client can be dropped in later
without touching any other code.
"""

import json
from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from django.conf import settings
from django.utils import timezone
from django.utils.module_loading import import_string


class ProviderNotAvailable(Exception):
    """The configured startup provider cannot be used. The message is safe to show."""


@dataclass(frozen=True)
class StartupRecord:
    """One startup as Navigatu describes it (plain data, not a model)."""

    navigatu_id: str
    name: str
    email: str
    contact_number: str = ""
    maturity_stage: str | None = None
    last_assessment_at: datetime | None = None
    is_active: bool = True


class StartupProvider(ABC):
    """What the rest of the system may ask Navigatu."""

    @abstractmethod
    def list_startups(self) -> list[StartupRecord]:
        """Every startup Navigatu knows about (active and inactive)."""

    @abstractmethod
    def get_startup(self, navigatu_id: str) -> StartupRecord | None:
        """One startup by its Navigatu ID, or None."""


class MockStartupProvider(StartupProvider):
    """Invented demo data from a JSON file (all emails end in @demo.example.com)."""

    data_file = Path(__file__).resolve().parent / "mock_data" / "navigatu_startups.json"

    def list_startups(self) -> list[StartupRecord]:
        with open(self.data_file, encoding="utf-8") as handle:
            rows = json.load(handle)
        return [self._record(row) for row in rows]

    def get_startup(self, navigatu_id: str) -> StartupRecord | None:
        return next((r for r in self.list_startups() if r.navigatu_id == navigatu_id), None)

    @staticmethod
    def _record(row: dict) -> StartupRecord:
        assessed = row.get("last_assessment_at")
        if assessed:
            assessed = datetime.fromisoformat(assessed)
            if timezone.is_naive(assessed):
                assessed = timezone.make_aware(assessed)
        return StartupRecord(
            navigatu_id=str(row["navigatu_id"]),
            name=row.get("name", ""),
            email=row.get("email", ""),
            contact_number=row.get("contact_number", ""),
            maturity_stage=row.get("maturity_stage") or None,
            last_assessment_at=assessed or None,
            is_active=bool(row.get("active", True)),
        )


class NavigatuStartupProvider(StartupProvider):
    """Placeholder for the real Navigatu connection (later phase, with Navigatu SSO)."""

    MESSAGE = "The Navigatu connection is not available yet."

    def list_startups(self) -> list[StartupRecord]:
        raise ProviderNotAvailable(self.MESSAGE)

    def get_startup(self, navigatu_id: str) -> StartupRecord | None:
        raise ProviderNotAvailable(self.MESSAGE)


def get_provider() -> StartupProvider:
    """The provider chosen in settings.STARTUP_PROVIDER."""
    try:
        provider_class = import_string(settings.STARTUP_PROVIDER)
    except ImportError as error:
        raise ProviderNotAvailable(f"Unknown startup provider: {settings.STARTUP_PROVIDER}.") from error
    return provider_class()
