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

from activities.models import ActivityStatus, ActivityType, BudgetCategory, FundSource, ParticipantCategory
from documents.models import DocumentStatus, DocumentType

from .models import Role, TBIMember

ACTIVITY_STATUSES = ["Draft", "Scheduled", "Ongoing", "Conducted"]
PARTICIPANT_CATEGORIES = ["Faculty", "Incubatees", "MSMEs"]
ACTIVITY_TYPES = ["Workshop", "Training", "Seminar", "Mentoring", "Networking", "Forum"]
# ASSUMPTIONS 433 (NAVIGATU): created only when missing (the Admin edits / deactivates them in the Django admin).
FUND_SOURCES = [("RAF", "Regular Agency Fund"), ("IGF", "Internally Generated Fund"), ("TRF", "Thrust Fund"),
                ("BRF", "Business Related Fund")]
BUDGET_CATEGORIES = ["Meals/Snacks", "Honorarium", "Supplies", "Venue", "Transportation", "Printing", "Other"]
DOCUMENT_STATUSES = [DocumentStatus.FOR_REVIEW, DocumentStatus.APPROVED]

# role_name: (can_create_activity, can_manage_users, can_approve_documents, can_approve_certificates,
#             can_delete_activities, can_manage_signatories, description)
ROLES: dict[str, tuple[bool, bool, bool, bool, bool, bool, str]] = {
    # Only the Director approves or returns documents (ASSUMPTIONS 344) and approves certificates (ASSUMPTIONS 421,
    # NAVIGATU: "the Director reviews and approves the certificates"; was also both Project Heads, Admin Staff and the
    # System Administrator, items 361 and 388). Admin Staff create and edit activities too (ASSUMPTIONS 388). Only the
    # Director manages the signatory list and signature images (ASSUMPTIONS 418, NAVIGATU).
    Role.DIRECTOR: (True, False, True, True, False, True, "Director of the Technology Business Incubator."),
    "Incubation Project Head": (True, False, False, False, False, False, "Leads incubation programs for startups."),
    "Community Development Head": (True, False, False, False, False, False, "Leads community and MSME programs."),
    "Admin Staff": (
        True, False, False, False, False, False,
        "Supports activities and documents; creates and edits activities.",
    ),
    # Separation of duties (ASSUMPTIONS 85, 339): manages users, never creates activities or approves documents;
    # deletes activities without real records (ASSUMPTIONS 389). No longer approves certificates (ASSUMPTIONS 421; the
    # flag is still allowed with can_manage_users if an administrator turns it on).
    Role.SYSTEM_ADMINISTRATOR: (
        False, True, False, False, True, False,
        "System administrator: manages user accounts and system settings; does not create activities.",
    ),
}

START = DocumentType.DueReference.START_DATE
END = DocumentType.DueReference.END_DATE

# name: (is_required, due_reference, due_offset_days). Due dates are placeholders until the TBI confirms them.
# Used only when a type is CREATED: later changes (Django admin) are never undone by a rerun (ASSUMPTIONS 386).
# The Invitation Letter and the Accomplishment Report are optional (ASSUMPTIONS 386; migrations documents/0003 and
# 0004 for databases seeded before). The Activity Design is due 5 days before the start, like the Request Letter
# (NAVIGATU's 5-day rule, ASSUMPTIONS 416; migration documents/0005 for databases still on the old -10).
# No "Accomplishment Report" any more (ASSUMPTIONS 430): it is the quarterly / semi-annual / annual report made in
# Reports, not a document type. Migration documents/0007 deactivated the old row; a new database never gets it and
# seed_data never creates or reactivates it (RETIRED_DOCUMENT_TYPES).
DOCUMENT_TYPES: dict[str, tuple[bool, str | None, int | None]] = {
    "Invitation Letter": (False, None, None),
    "Activity Design": (True, START, -5),
    "Request Letter": (True, START, -5),
    "Attendance Sheet": (True, END, 5),  # also a payment document (ASSUMPTIONS 425; documents/0006 moves end + 0)
    "Activity Report": (True, END, 5),  # payment documents for the Supply Office (ASSUMPTIONS 425)
    "Event Photos": (True, END, 5),
    "Pre-registration Form": (False, None, None),
    "Evaluation Form": (False, None, None),
    "Certificate": (False, None, None),
    DocumentType.OTHER: (False, None, None),  # the catch-all optional type (its documents need a title)
}

RETIRED_DOCUMENT_TYPES = ("Accomplishment Report",)


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
    for order, (code, name) in enumerate(FUND_SOURCES, start=1):
        result.add(FundSource.objects.get_or_create(code=code, defaults={"name": name, "sort_order": order})[1])
    for order, name in enumerate(BUDGET_CATEGORIES, start=1):
        result.add(BudgetCategory.objects.get_or_create(name=name, defaults={"sort_order": order})[1])

    for role_name, (can_create, can_manage, can_approve, can_approve_certificates, can_delete, can_manage_signatories,
                    description) in ROLES.items():
        # can_create_activity, the two approval flags, can_delete_activities and can_manage_signatories are set only when
        # the role is created: the System Administrator switches them in the Admin Console, and running seed_data again
        # must not undo that (ASSUMPTIONS 344, 357, 388, 389, 418). can_manage_users is re-applied on every run, so a
        # System Administrator always exists.
        _, created = Role.objects.update_or_create(
            role_name=role_name,
            defaults={"can_manage_users": can_manage},
            create_defaults={
                "can_create_activity": can_create,
                "can_manage_users": can_manage,
                "can_approve_documents": can_approve,
                "can_approve_certificates": can_approve_certificates,
                "can_delete_activities": can_delete,
                "can_manage_signatories": can_manage_signatories,
                "description": description,
            },
        )
        result.add(created)

    for name, (is_required, due_reference, due_offset_days) in DOCUMENT_TYPES.items():
        due_rule = {"is_required": is_required, "due_reference": due_reference, "due_offset_days": due_offset_days}
        # Only when missing: whether a type is required and its due rule are the Admin's from then on (ASSUMPTIONS 386).
        _, created = DocumentType.objects.get_or_create(name=name, defaults=due_rule)
        result.add(created)

    return result


# Default form fields copied into every new registration/evaluation form.
# PLACEHOLDERS: to be validated with the TBI. The same set is used for every activity type.
# (field_label, field_type, options, is_required)
# No "Data Privacy consent" question any more (ASSUMPTIONS 394): every public form has the system's fixed privacy notice
# checkbox. Only NEW installs are affected: seed_data never deletes default fields that already exist.
DEFAULT_FORM_FIELDS: dict[str, list[tuple[str, str, list[str], bool]]] = {
    # No "Affiliation / Organization" any more (ASSUMPTIONS 426): Designation / Position and Office / Agency are fixed
    # REGISTRATION columns; migration registrations/0010 removed the old default rows.
    "registration": [],
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
    admin_role = Role.objects.get(role_name=Role.SYSTEM_ADMINISTRATOR)
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
