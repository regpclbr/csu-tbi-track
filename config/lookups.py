"""Lookup rows for the frontend's filters and form choices: id and name only.

Active only where the table has is_active (activity types, document types): an inactive row cannot
be chosen for a new activity or upload. ROLE, the statuses and PARTICIPANT_CATEGORY have no
is_active, so every row is listed.
"""

from accounts.models import Role
from activities.models import ActivityStatus, ActivityType, ParticipantCategory
from documents.models import DocumentStatus, DocumentType


def _rows(queryset, name_field: str = "name") -> list[dict]:
    return [{"id": pk, "name": name} for pk, name in queryset.values_list("pk", name_field)]


def staff_lookups() -> dict[str, list[dict]]:
    """Every lookup list, each sorted by name (statuses in their workflow order, i.e. by ID)."""
    return {
        "activity_types": _rows(ActivityType.objects.filter(is_active=True).order_by("name")),
        "participant_categories": _rows(ParticipantCategory.objects.order_by("name")),
        "document_types": _rows(DocumentType.objects.filter(is_active=True).order_by("name")),
        "activity_statuses": _rows(ActivityStatus.objects.order_by("pk")),
        "document_statuses": _rows(DocumentStatus.objects.order_by("pk"), "status_name"),
        "roles": _rows(Role.objects.order_by("role_name"), "role_name"),
    }
