"""TRF is the "Trust Fund", not "Thrust Fund" (ASSUMPTIONS 451): the seed and the guarded data step of migration
activities/0016 (a name the Admin changed is left alone)."""

import importlib

import pytest
from django.apps import apps

from accounts.seed import FUND_SOURCES
from activities.models import FundSource

pytestmark = pytest.mark.django_db

migration = importlib.import_module("activities.migrations.0016_evaluation_closed_by_hand_trust_fund")


def test_the_seed_says_trust_fund(roles) -> None:
    assert ("TRF", "Trust Fund") in [(code, name) for code, name in FUND_SOURCES]
    assert FundSource.objects.get(code="TRF").name == "Trust Fund"
    assert str(FundSource.objects.get(code="TRF")) == "TRF – Trust Fund"


def test_the_migration_renames_only_the_old_name(roles) -> None:
    FundSource.objects.filter(code="TRF").update(name="Thrust Fund")
    migration.rename_trust_fund(apps, None)
    assert FundSource.objects.get(code="TRF").name == "Trust Fund"
    FundSource.objects.filter(code="TRF").update(name="Trust Fund (Special Account)")  # changed by the Admin
    migration.rename_trust_fund(apps, None)
    assert FundSource.objects.get(code="TRF").name == "Trust Fund (Special Account)"
    migration.undo_rename(apps, None)
    assert FundSource.objects.get(code="TRF").name == "Trust Fund (Special Account)"
