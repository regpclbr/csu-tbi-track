"""Lookup data and the first Admin account, used by `manage.py seed_data` (and `seed_demo`).

Safe to run many times:
- Rows are matched by their unique name, so nothing is duplicated.
- Only the fields listed in `defaults` are (re)set on every run. Descriptions and
  `is_active` are only set when a row is first created, so an admin's edits are kept.
"""

import json
from dataclasses import dataclass

from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models.functions import Lower, Trim

from activities.models import ActivityStatus, ActivityType, ParticipantCategory
from documents.models import DocumentStatus, DocumentType

from .models import Role, TBIMember

ACTIVITY_STATUSES = ["Draft", "Scheduled", "Ongoing", "Conducted"]
PARTICIPANT_CATEGORIES = ["Faculty", "Incubatees", "MSMEs"]
ACTIVITY_TYPES = ["Workshop", "Training", "Seminar", "Mentoring", "Networking", "Forum"]
DOCUMENT_STATUSES = [DocumentStatus.FOR_REVIEW, DocumentStatus.APPROVED]

# role_name: (can_create_activity, can_manage_users, description)
ROLES: dict[str, tuple[bool, bool, str]] = {
    "Director": (True, False, "Director of the Technology Business Incubator."),
    "Incubation Project Head": (True, False, "Leads incubation programs for startups."),
    "Community Development Head": (True, False, "Leads community and MSME programs."),
    "Admin Staff": (False, False, "Supports activities and documents; cannot create activities."),
    Role.ADMIN: (True, True, "System administrator; manages user accounts."),
}

START = DocumentType.DueReference.START_DATE
END = DocumentType.DueReference.END_DATE

# name: (is_required, due_reference, due_offset_days). Due dates are placeholders until the TBI confirms them.
DOCUMENT_TYPES: dict[str, tuple[bool, str | None, int | None]] = {
    "Invitation Letter": (True, START, -7),
    "Activity Design": (True, START, -10),
    "Request Letter": (True, START, -5),
    "Attendance Sheet": (True, END, 0),
    "Accomplishment Report": (True, END, 7),
    "Pre-registration Form": (False, None, None),
    "Evaluation Form": (False, None, None),
    "Certificate": (False, None, None),
}


@dataclass
class SeedResult:
    """How many rows the seed created (the rest already existed)."""

    created: int = 0
    existing: int = 0

    def add(self, created: bool) -> None:
        if created:
            self.created += 1
        else:
            self.existing += 1


@transaction.atomic
def load_lookups() -> SeedResult:
    """Create or update all lookup rows. Returns how many were created."""
    result = SeedResult()

    for name in ACTIVITY_STATUSES:
        result.add(ActivityStatus.objects.get_or_create(name=name)[1])
    for name in PARTICIPANT_CATEGORIES:
        # Match ignoring case and spaces (the Admin may have added "msmes " already); never overwrite.
        existing = ParticipantCategory.objects.annotate(key=Lower(Trim("name"))).filter(key=name.lower()).exists()
        if not existing:
            ParticipantCategory.objects.create(name=name)
        result.add(not existing)
    for name in ACTIVITY_TYPES:
        result.add(ActivityType.objects.get_or_create(name=name)[1])
    for name in DOCUMENT_STATUSES:
        result.add(DocumentStatus.objects.get_or_create(status_name=name)[1])

    for role_name, (can_create, can_manage, description) in ROLES.items():
        _, created = Role.objects.update_or_create(
            role_name=role_name,
            defaults={"can_create_activity": can_create, "can_manage_users": can_manage},
            create_defaults={
                "can_create_activity": can_create,
                "can_manage_users": can_manage,
                "description": description,
            },
        )
        result.add(created)

    for name, (is_required, due_reference, due_offset_days) in DOCUMENT_TYPES.items():
        due_rule = {"is_required": is_required, "due_reference": due_reference, "due_offset_days": due_offset_days}
        _, created = DocumentType.objects.update_or_create(name=name, defaults=due_rule, create_defaults=due_rule)
        result.add(created)

    return result


# Default form fields copied into every new registration/evaluation form.
# PLACEHOLDERS: to be validated with the TBI. The same set is used for every activity type.
# (field_label, field_type, options, is_required)
DATA_PRIVACY_CONSENT = (
    "I agree to the processing of my personal data in accordance with the "
    "Data Privacy Act of 2012 (RA 10173)."
)
DEFAULT_FORM_FIELDS: dict[str, list[tuple[str, str, list[str], bool]]] = {
    "registration": [
        ("Affiliation / Organization", "short_text", [], True),
        ("Data Privacy consent", "checkbox", [DATA_PRIVACY_CONSENT], True),
    ],
    "evaluation": [
        ("Overall rating", "rating", [], True),
        ("Resource person rating", "rating", [], True),
        ("Organization / venue rating", "rating", [], True),
        ("Key learnings", "long_text", [], True),
        ("Suggestions", "long_text", [], False),
    ],
}


@transaction.atomic
def load_default_form_fields() -> SeedResult:
    """Create the placeholder default fields for every activity type, for both form types.

    Matched by (activity type, form type, label) and only created when missing, so running
    it again never duplicates them or overwrites the Admin's edits.
    """
    from registrations.models import DefaultFormField  # registrations depends on activities, not the other way

    result = SeedResult()
    for activity_type in ActivityType.objects.all():
        for form_type, fields in DEFAULT_FORM_FIELDS.items():
            for order, (label, field_type, options, required) in enumerate(fields, start=1):
                _, created = DefaultFormField.objects.get_or_create(
                    activity_type=activity_type,
                    form_type=form_type,
                    field_label=label,
                    defaults={
                        "field_type": field_type,
                        "options": json.dumps(options, ensure_ascii=False) if options else "",
                        "is_required": required,
                        "sort_order": order,
                    },
                )
                result.add(created)
    return result


class AdminAccountError(Exception):
    """Raised when the Admin account settings are invalid."""


def ensure_admin_account(email: str, password: str, name: str) -> tuple[TBIMember, bool]:
    """Create the first Admin account, or make sure an existing one has the Admin role.

    The password and must_change_password are only set when the account is created,
    so running the seed again never resets a password the admin has changed.
    """
    admin_role = Role.objects.get(role_name=Role.ADMIN)
    existing = TBIMember.objects.filter(email__iexact=email).first()
    if existing:
        if existing.role_id != admin_role.pk:
            existing.role = admin_role
            existing.save(update_fields=["role", "updated_at"])
        return existing, False

    try:
        validate_password(password, TBIMember(email=email, name=name))
    except ValidationError as exc:
        raise AdminAccountError("SEED_ADMIN_PASSWORD is too weak: " + " ".join(exc.messages)) from exc

    # create_superuser assigns the Admin role and sets must_change_password=False.
    return TBIMember.objects.create_superuser(email=email, name=name, password=password), True
