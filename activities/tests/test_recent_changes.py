"""recent_audit_entries (the dashboard's "Recent changes") and audit_sentence (one row as a sentence)."""

import pytest

from activities import services
from activities.formatting import audit_sentence
from activities.models import ActivityAuditLog

pytestmark = pytest.mark.django_db


def test_recent_entries_are_the_newest_across_activities(director, valid_data) -> None:
    first = services.create_activity(director, valid_data(title="First"))
    second = services.create_activity(director, valid_data(title="Second"))
    services.update_activity(director, first, {"venue": "TBI Lounge"})
    entries = services.recent_audit_entries(limit=2)
    assert [(e.activity.title, e.field_changed) for e in entries] == [("First", "Venue"), ("Second", "Status")]
    assert len(services.recent_audit_entries()) == 3  # default limit 5, only 3 rows exist


def sentence(activity, label: str) -> str:
    entry = ActivityAuditLog.objects.filter(activity=activity, field_changed=label).latest("log_id")
    return audit_sentence(entry)


def test_sentences_for_each_kind_of_change(director, new_activity) -> None:
    services.change_status(director, new_activity, "Scheduled")
    services.update_activity(director, new_activity, {"venue": "TBI Lounge", "description": "New text"})
    person = services.create_resource_person(director, "Dr. Reyes")
    services.add_resource_person(director, new_activity, person)
    services.remove_resource_person(director, new_activity, person)
    who, title = "Test Director", "Pitching Workshop"
    created = ActivityAuditLog.objects.filter(activity=new_activity, field_changed="Status").earliest("log_id")
    assert audit_sentence(created) == f"{who} created {title} as Draft"
    assert sentence(new_activity, "Status") == f"{who} changed the status of {title} from Draft to Scheduled"
    assert sentence(new_activity, "Venue") == f"{who} changed the venue of {title} to TBI Lounge"
    assert sentence(new_activity, "Description") == f"{who} changed the description of {title}"
    assert sentence(new_activity, "Resource Person Added") == f"{who} added Dr. Reyes as a resource person to {title}"
    assert sentence(new_activity, "Resource Person Removed") == f"{who} removed Dr. Reyes as a resource person from {title}"


def test_budget_uses_the_stored_readable_value_and_long_values_are_cut(director, new_activity) -> None:
    from decimal import Decimal

    services.update_activity(director, new_activity, {"budget_amount": Decimal("45000"), "venue": "V" * 100})
    assert sentence(new_activity, "Budget Amount").endswith("to ₱45,000.00")
    assert sentence(new_activity, "Venue").endswith("V…")
