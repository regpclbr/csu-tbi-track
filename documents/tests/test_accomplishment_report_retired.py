"""The per-activity "Accomplishment Report" leaves the checklist (ASSUMPTIONS 430): migration documents/0007 deactivates
the row only while it is as 0004 left it, old uploads stay listed under "Other documents" and downloadable, nothing
counts it, it is never offered for upload, and seed_data never creates or reactivates it."""

import importlib
from datetime import date

import pytest
from django.apps import apps
from django.core.exceptions import ValidationError
from django.test import Client
from django.urls import reverse

from accounts.seed import DOCUMENT_TYPES, RETIRED_DOCUMENT_TYPES, load_lookups
from documents import services
from documents.models import DocumentType
from reports import metrics

from .conftest import TEST_NOW, at

pytestmark = pytest.mark.django_db

migration = importlib.import_module("documents.migrations.0007_accomplishment_report_inactive")
NAME = "Accomplishment Report"


def old_row(**changes) -> DocumentType:
    """The row as documents/0004 left it in an existing database (active, optional, no due rule)."""
    return DocumentType.objects.create(name=NAME, **changes)


def test_migration_deactivates_only_the_untouched_row_and_the_reverse_restores_it(roles) -> None:
    row = old_row()
    others_before = list(DocumentType.objects.exclude(name=NAME).values())
    migration.deactivate(apps, None)
    row.refresh_from_db()
    assert row.is_active is False and row.is_required is False
    assert list(DocumentType.objects.exclude(name=NAME).values()) == others_before  # nothing else changes
    migration.reactivate(apps, None)
    row.refresh_from_db()
    assert row.is_active is True


@pytest.mark.parametrize("changes", [
    {"is_required": True, "due_reference": "end_date", "due_offset_days": 7},  # made required again in the admin
    {"description": "ours"} | {"is_required": True, "due_reference": "end_date", "due_offset_days": 3},
])
def test_migration_never_touches_a_row_the_admin_changed(roles, changes) -> None:
    row = old_row(**changes)
    migration.deactivate(apps, None)
    row.refresh_from_db()
    assert row.is_active is True


def test_migration_does_nothing_without_the_row(roles) -> None:
    before = list(DocumentType.objects.values())
    migration.deactivate(apps, None)
    assert list(DocumentType.objects.values()) == before


def test_seed_never_creates_or_reactivates_it(roles) -> None:
    assert NAME in RETIRED_DOCUMENT_TYPES and NAME not in DOCUMENT_TYPES
    assert not DocumentType.objects.filter(name=NAME).exists()  # a new database (roles = load_lookups)
    row = old_row(is_active=False)
    load_lookups()  # a rerun
    row.refresh_from_db()
    assert row.is_active is False and DocumentType.objects.filter(name=NAME).count() == 1


def test_old_uploads_stay_visible_and_downloadable_but_count_nowhere(director, make_activity, upload) -> None:
    row = old_row()
    activity, without = make_activity(title="With an old report"), make_activity(title="Without")
    document = upload(director, activity, document_type=row)
    migration.deactivate(apps, None)

    checklist = services.document_checklist(activity, now=TEST_NOW)
    optional = {entry["document_type"].name: entry["documents"] for entry in checklist["optional"]}
    assert optional[NAME] == [document]  # listed under "Other documents"
    assert NAME not in {item.document_type.name for item in checklist["required"]}
    empty = services.document_checklist(without, now=TEST_NOW)
    assert NAME not in {entry["document_type"].name for entry in empty["optional"]}  # only where it has documents

    overview = {entry.activity: entry for entry in services.activity_document_overview([activity], TEST_NOW)}
    assert [d.pk for d in overview[activity].other] == [document.pk]

    period = metrics.ReportFilters(date(2026, 1, 1), date(2026, 12, 31))
    compliance = metrics.document_compliance(metrics.period_activities(period), at(date(2026, 12, 30)))
    assert NAME not in compliance["document_types"]  # the compliance report never counts it

    client = Client()
    client.force_login(director)
    response = client.get(reverse("documents:download", args=[document.pk]))
    assert response.status_code == 200 and "attachment" in response["Content-Disposition"]
    tab = client.get(f"{reverse('activities:detail', args=[activity.pk])}?tab=documents").content.decode()
    select = tab.split('<select id="upload-type"')[1].split("</select>")[0]
    assert NAME not in select  # never offered for a new upload
    assert NAME in tab  # but the old file is listed


def test_a_new_upload_of_the_inactive_type_is_refused(director, make_activity, upload) -> None:
    row = old_row(is_active=False)
    with pytest.raises(ValidationError, match="not active"):
        upload(director, make_activity(), document_type=row)
