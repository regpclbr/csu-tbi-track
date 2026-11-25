"""ASSUMPTIONS 437: every change to a registration or evaluation form's fields is in the activity's History with who and
when, also when the Director edits a form another member made."""

import pytest

from activities.models import ActivityAuditLog
from registrations import services
from registrations.models import FormType

from .conftest import form_of

pytestmark = pytest.mark.django_db


def rows(activity, label: str = "Registration form") -> list[tuple]:
    return list(ActivityAuditLog.objects.filter(activity=activity, field_changed=label).order_by("pk")
                .values_list("changed_by", "old_value", "new_value"))


@pytest.fixture
def staff(make_member):
    return make_member("Admin Staff", email="staff.forms@example.com")


def test_creating_and_editing_fields_is_logged_by_whoever_does_it(new_activity, defaults, staff, director) -> None:
    form = services.create_form(staff, new_activity, FormType.REGISTRATION)
    assert rows(new_activity)[-1][0] == staff.pk and rows(new_activity)[-1][2].startswith("Created (")
    field = services.add_field(director, form, field_label="Company", field_type="short_text", is_required=False)
    assert rows(new_activity)[-1] == (director.pk, "", 'Field "Company" (short text, optional) added')
    services.update_field(director, field, is_required=True)
    assert rows(new_activity)[-1] == (director.pk, 'Field "Company" (short text, optional)',
                                      'Field "Company" (short text, required)')
    services.remove_field(director, field)
    assert rows(new_activity)[-1] == (director.pk, 'Field "Company" (short text, required)', "Removed")


def test_an_unchanged_edit_writes_nothing(new_activity, defaults, director) -> None:
    form = services.create_form(director, new_activity, FormType.EVALUATION)
    field = form.fields.first()
    before = len(rows(new_activity, "Evaluation form"))
    services.update_field(director, field, field_label=field.field_label)
    assert len(rows(new_activity, "Evaluation form")) == before


def test_reordering_is_logged_with_the_old_and_new_order(new_activity, with_forms, director) -> None:
    with_forms(new_activity)
    form = form_of(new_activity, FormType.EVALUATION)
    ids = list(form.fields.order_by("sort_order").values_list("pk", flat=True))
    services.reorder_fields(director, form, list(reversed(ids)))
    who, old, new = rows(new_activity, "Evaluation form")[-1]
    assert who == director.pk and old.startswith("Order: ") and new.startswith("Order: ") and old != new
