"""NAVIGATU's 5-day rule (ASSUMPTIONS 416): the Activity Design is due 5 days before the start date, like the Request
Letter. seed_data creates it with -5 (create-only); migration documents/0005 moves only a row still on the old -10."""

import importlib
from datetime import timedelta

import pytest
from django.apps import apps

from documents import services
from documents.models import DocumentType

from .conftest import TEST_NOW

pytestmark = pytest.mark.django_db

migration = importlib.import_module("documents.migrations.0005_activity_design_due_five_days")


def rule(name: str = "Activity Design") -> tuple:
    row = DocumentType.objects.get(name=name)
    return row.is_required, row.due_reference, row.due_offset_days


def test_seeded_activity_design_is_due_five_days_before_the_start(roles, make_activity) -> None:
    assert rule() == (True, "start_date", -5) == rule("Request Letter")
    activity = make_activity()
    items = {item.document_type.name: item for item in services.document_checklist(activity, now=TEST_NOW)["required"]}
    assert items["Activity Design"].due_date == activity.start_date - timedelta(days=5)
    assert items["Activity Design"].due_date == items["Request Letter"].due_date


def test_migration_moves_the_old_ten_day_rule_to_five_and_back(roles) -> None:
    """A database seeded before: the Activity Design still has start_date / -10."""
    DocumentType.objects.filter(name="Activity Design").update(due_offset_days=-10)
    others_before = list(DocumentType.objects.exclude(name="Activity Design").values())

    migration.five_days_before(apps, None)
    assert rule() == (True, "start_date", -5)
    assert list(DocumentType.objects.exclude(name="Activity Design").values()) == others_before  # nothing else

    migration.ten_days_before(apps, None)  # the reverse puts the old rule back
    assert rule() == (True, "start_date", -10)


@pytest.mark.parametrize("reference, offset", [("start_date", -7), ("start_date", -5), ("end_date", -10)])
def test_migration_leaves_an_admin_choice_alone(roles, reference, offset) -> None:
    """Changed in the Django admin (not exactly start_date / -10): never touched."""
    DocumentType.objects.filter(name="Activity Design").update(due_reference=reference, due_offset_days=offset)
    before = list(DocumentType.objects.values())
    migration.five_days_before(apps, None)
    assert list(DocumentType.objects.values()) == before


def test_reverse_leaves_anything_but_five_days_alone(roles) -> None:
    DocumentType.objects.filter(name="Activity Design").update(due_offset_days=-7)
    migration.ten_days_before(apps, None)
    assert rule() == (True, "start_date", -7)


def test_migration_does_nothing_without_the_row(roles) -> None:
    DocumentType.objects.filter(name="Activity Design").delete()
    before = list(DocumentType.objects.values())
    migration.five_days_before(apps, None)
    migration.ten_days_before(apps, None)
    assert list(DocumentType.objects.values()) == before
