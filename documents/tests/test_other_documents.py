"""Other (optional) documents (ASSUMPTIONS 386, 387): the Invitation Letter and the Accomplishment Report are optional
(migrations documents/0003 and 0004, seed_data), uploaded ones stay and are listed under "Other documents", the "Other"
type needs a title, the Documents tab's "Upload other document" button, focus and grouped dropdown, and the row layout."""

import importlib
import re
from datetime import date

import pytest
from django.apps import apps
from django.core.exceptions import ValidationError
from django.test import Client
from django.urls import reverse

from activities import attention
from documents import services
from documents.models import Document, DocumentType
from reports import metrics

from .conftest import TEST_NOW, at, make_file

pytestmark = pytest.mark.django_db

migration = importlib.import_module("documents.migrations.0003_invitation_letter_optional")
migration_0004 = importlib.import_module("documents.migrations.0004_accomplishment_report_optional")
REQUIRED = {"Activity Design", "Request Letter", "Attendance Sheet"}


def type_named(name: str) -> DocumentType:
    return DocumentType.objects.get(name=name)


def tab(activity, **params) -> str:
    query = "&".join(f"{key}={value}" for key, value in {"tab": "documents", **params}.items())
    return f"{reverse('activities:detail', args=[activity.pk])}?{query}"


@pytest.fixture
def login():
    def log_in(member) -> Client:
        client = Client()
        client.force_login(member)
        return client

    return log_in


# --- The data migration ------------------------------------------------------------------------------------------


def test_migration_makes_only_the_invitation_letter_optional_and_keeps_its_files(roles, director, make_activity,
                                                                                  upload) -> None:
    """A database seeded before: the Invitation Letter was required (start - 7) and has a file."""
    DocumentType.objects.filter(name="Invitation Letter").update(is_required=True, due_reference="start_date",
                                                                 due_offset_days=-7)
    activity = make_activity()
    document = upload(director, activity, document_type=type_named("Invitation Letter"))
    others_before = list(DocumentType.objects.exclude(name="Invitation Letter").values())

    migration.make_optional(apps, None)

    invitation = type_named("Invitation Letter")
    assert (invitation.is_required, invitation.due_reference, invitation.due_offset_days) == (False, None, None)
    assert list(DocumentType.objects.exclude(name="Invitation Letter").values()) == others_before  # nothing else
    document.refresh_from_db()
    assert document.file_path.storage.exists(document.file_path.name)  # the file and its row stay
    checklist = services.document_checklist(activity, now=TEST_NOW)
    assert "Invitation Letter" not in {item.document_type.name for item in checklist["required"]}
    optional = {entry["document_type"].name: entry["documents"] for entry in checklist["optional"]}
    assert optional["Invitation Letter"] == [document]

    migration.make_required_again(apps, None)  # the reverse puts the old rule back
    invitation.refresh_from_db()
    assert (invitation.is_required, invitation.due_reference, invitation.due_offset_days) == (True, "start_date", -7)


def test_migration_0004_makes_only_the_accomplishment_report_optional_and_keeps_its_files(
        roles, director, make_activity, upload) -> None:
    """A database migrated to 0003 before: the Accomplishment Report was required (end + 7) and has a file."""
    DocumentType.objects.filter(name="Accomplishment Report").update(is_required=True, due_reference="end_date",
                                                                     due_offset_days=7)
    activity = make_activity()
    document = upload(director, activity, document_type=type_named("Accomplishment Report"))
    others_before = list(DocumentType.objects.exclude(name="Accomplishment Report").values())

    migration_0004.make_optional(apps, None)

    report = type_named("Accomplishment Report")
    assert (report.is_required, report.due_reference, report.due_offset_days) == (False, None, None)
    assert list(DocumentType.objects.exclude(name="Accomplishment Report").values()) == others_before  # nothing else
    document.refresh_from_db()
    assert document.file_path.storage.exists(document.file_path.name)  # the file and its row stay
    checklist = services.document_checklist(activity, now=TEST_NOW)
    assert {item.document_type.name for item in checklist["required"]} == REQUIRED
    optional = {entry["document_type"].name: entry["documents"] for entry in checklist["optional"]}
    assert optional["Accomplishment Report"] == [document]

    migration_0004.make_required_again(apps, None)  # the reverse puts the old rule back
    report.refresh_from_db()
    assert (report.is_required, report.due_reference, report.due_offset_days) == (True, "end_date", 7)


@pytest.mark.parametrize("module, name", [(migration, "Invitation Letter"), (migration_0004, "Accomplishment Report")])
def test_migrations_do_nothing_without_the_row(roles, module, name) -> None:
    DocumentType.objects.filter(name=name).delete()
    before = list(DocumentType.objects.values())
    module.make_optional(apps, None)
    assert list(DocumentType.objects.values()) == before


# --- Nothing counts the Invitation Letter as required any more ------------------------------------------------------


def test_activities_with_and_without_the_optional_documents(director, make_activity, upload) -> None:
    """Every count reads DOCUMENT_TYPE.is_required: checklist, Documents page, Needs attention, compliance, S3. Neither
    the Invitation Letter nor the Accomplishment Report counts, uploaded or not."""
    optional_names = ("Invitation Letter", "Accomplishment Report")
    with_files = make_activity(title="With both", start=date(2026, 11, 10))
    without = make_activity(title="Without them", start=date(2026, 11, 10))
    uploaded = [upload(director, with_files, document_type=type_named(name)) for name in optional_names]
    late = at(date(2026, 12, 30))  # every due date of the old rules has passed (Accomplishment Report: end + 7)
    for activity in (with_files, without):
        required = {item.document_type.name for item in services.document_checklist(activity, now=late)["required"]}
        assert required == REQUIRED
        texts = " ".join(item.text for item in attention.activity_attention(activity, late))
        assert not any(name in texts for name in optional_names)
    overview = {entry.activity: entry for entry in services.activity_document_overview([with_files, without], late)}
    assert sorted(d.pk for d in overview[with_files].other) == sorted(d.pk for d in uploaded)
    assert overview[with_files].missing_count == overview[without].missing_count == len(REQUIRED)
    period = metrics.ReportFilters(date(2026, 1, 1), date(2026, 12, 31))
    compliance = metrics.document_compliance(metrics.period_activities(period), late)
    assert set(compliance["document_types"]) == REQUIRED  # the compliance report's columns
    rows = metrics.document_attention(period, late)
    assert not any(row["document_type"] in optional_names for row in rows["overdue"] + rows["due_soon"])
    assert metrics.documents_now(period, late)["overdue"] == 2 * len(REQUIRED)  # dashboard S3: 3 per activity


# --- The "Other" type needs a title -------------------------------------------------------------------------------


def test_other_needs_a_title(director, scheduled, upload) -> None:
    other = type_named(DocumentType.OTHER)
    with pytest.raises(ValidationError) as refused:
        upload(director, scheduled, document_type=other, title="   ")
    assert refused.value.message_dict == {"title": [services.OTHER_TITLE_REQUIRED]}
    assert services.OTHER_TITLE_REQUIRED == "Give this document a title, for example: MOA with ABC Corp."
    assert not Document.objects.exists()
    assert upload(director, scheduled, document_type=other, title=" MOA with ABC Corp. ").title == "MOA with ABC Corp."


def test_other_types_keep_the_optional_title(director, scheduled, upload) -> None:
    assert upload(director, scheduled, document_type=type_named("Invitation Letter")).title == "Invitation Letter"
    assert upload(director, scheduled).title == "Request Letter"


def test_title_and_file_errors_come_together(director, scheduled, upload) -> None:
    with pytest.raises(ValidationError) as refused:
        upload(director, scheduled, file=make_file("notes.txt", b"plain text"), document_type=type_named("Other"))
    assert set(refused.value.message_dict) == {"title", "file"}


def test_the_tab_shows_the_title_error_under_its_field(director, login, scheduled) -> None:
    response = login(director).post(reverse("activities:document_upload", args=[scheduled.pk]),
                                    {"document_type": type_named("Other").pk, "title": "", "file": make_file()})
    html = response.content.decode()
    assert response.status_code == 400 and not Document.objects.exists()
    assert 'id="upload-title-error"' in html and "Give this document a title, for example: MOA with ABC Corp." in html
    assert re.search(r'<input id="upload-title"[^>]*aria-invalid="true"[^>]*autofocus', html)


# --- The Documents tab --------------------------------------------------------------------------------------------


def test_upload_other_document_button_focuses_the_type(director, login, scheduled) -> None:
    html = login(director).get(tab(scheduled)).content.decode()
    optional_card = html.split('id="optional-title"')[1].split('id="upload"')[0]
    link = f'{reverse("activities:detail", args=[scheduled.pk])}?tab=documents&amp;focus=type#upload'
    assert f'href="{link}"' in optional_card and "Upload other document" in optional_card
    assert ("Optional types: Accomplishment Report, Evaluation Form, Invitation Letter, Other, Pre-registration Form."
            in optional_card)
    assert not re.search(r'<select id="upload-type"[^>]*autofocus', html)  # no focus on a plain visit
    focused = login(director).get(tab(scheduled, focus="type")).content.decode()
    assert re.search(r'<select id="upload-type"[^>]*autofocus', focused)


def test_dropdown_is_grouped_required_then_other(director, login, scheduled) -> None:
    html = login(director).get(tab(scheduled)).content.decode()
    select = html.split('<select id="upload-type"')[1].split("</select>")[0]
    groups = re.findall(r'<optgroup label="([^"]+)">(.*?)</optgroup>', select, re.S)
    names = {label: re.findall(r">([^<]+)</option>", body) for label, body in groups}
    assert list(names) == ["Required", "Other documents"]
    assert names["Required"] == ["Activity Design", "Attendance Sheet", "Request Letter"]
    assert names["Other documents"] == ["Accomplishment Report", "Evaluation Form", "Invitation Letter", "Other",
                                        "Pre-registration Form"]
    assert "(required)" not in select and "Certificate" not in select  # evidence has its own upload


def test_upload_links_preselect_their_type_in_either_group(director, login, scheduled) -> None:
    client = login(director)
    html = client.get(tab(scheduled)).content.decode()
    design = type_named("Activity Design")
    assert f'?tab=documents&amp;type={design.pk}#upload' in html  # the required row's Upload button
    for document_type in (design, type_named("Invitation Letter")):
        html = client.get(tab(scheduled, type=document_type.pk)).content.decode()
        assert re.search(rf'<option value="{document_type.pk}" selected>{document_type.name}</option>', html)


def test_an_uploaded_invitation_letter_is_listed_under_other_documents(director, login, scheduled, upload) -> None:
    upload(director, scheduled, document_type=type_named("Invitation Letter"), title="Signed invitation")
    html = login(director).get(tab(scheduled)).content.decode()
    required_card = html.split('id="required-title"')[1].split('id="optional-title"')[0]
    optional_card = html.split('id="optional-title"')[1].split('id="upload"')[0]
    assert "Invitation Letter" not in required_card
    assert ">Invitation Letter</h3>" in optional_card and "Signed invitation" in optional_card


def test_document_rows_give_the_title_the_free_width(director, login, scheduled, upload) -> None:
    """ASSUMPTIONS 387 (bug: "Accomplishment report" one or two letters per line): the title block starts at 16rem and
    grows (never min-w-0 + flex-1 alone); badges and buttons are one wrapping group on the right. Same template in
    both cards. Checked in Chromium by the opt-in test_document_rows_browser.py."""
    upload(director, scheduled, document_type=type_named("Other"), title="Accomplishment report")
    upload(director, scheduled)  # a Required row
    html = login(director).get(tab(scheduled)).content.decode()
    titles = re.findall(r'<div class="([^"]*)" data-document-title>', html)
    sides = re.findall(r'<div class="([^"]*)" data-document-side>', html)
    assert len(titles) == len(sides) == 2
    for classes in titles:
        assert {"min-w-0", "grow", "basis-64"} <= set(classes.split()) and "flex-1" not in classes.split()
    for classes in sides:
        assert {"ml-auto", "flex", "flex-wrap", "justify-end"} <= set(classes.split())
    rows = re.findall(r'<li class="([^"]*)" data-document-row>', html)
    assert rows and all("flex-wrap" in row.split() for row in rows)
