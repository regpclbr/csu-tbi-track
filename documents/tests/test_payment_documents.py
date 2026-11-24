"""Payment documents for the Supply Office (ASSUMPTIONS 425, NAVIGATU UAT): Activity Report, Attendance Sheet and Event
Photos, due 5 days after the end date; the new types only for activities ending on or after PAYMENT_DOCUMENTS_FROM;
they count only once Conducted; the "For payment (Supply Office)" group; the payment packet (Approved files only)."""

import importlib
import io
import zipfile
from datetime import date, datetime

import pytest
from django.apps import apps as django_apps
from django.core.exceptions import ValidationError
from django.test import Client
from django.urls import reverse
from django.utils import timezone

from accounts.seed import DOCUMENT_TYPES
from activities import attention
from activities.models import Activity, ActivityStatus
from documents import services
from documents.models import DocumentType
from reports import metrics

from .conftest import JPG_BYTES, PNG_BYTES, make_file

pytestmark = pytest.mark.django_db

NEW_END = date(2026, 12, 4)  # after PAYMENT_DOCUMENTS_FROM (2026-11-22)
OLD_END = date(2026, 11, 5)  # before it
AFTER_DUE = timezone.make_aware(datetime(2026, 12, 10, 9, 0))  # NEW_END + 6 days: past the due date (end + 5)
PAYMENT = ["Activity Report", "Attendance Sheet", "Event Photos"]


def payment_types() -> dict[str, DocumentType]:
    return {t.name: t for t in DocumentType.objects.filter(name__in=PAYMENT)}


def required_names(activity: Activity, now=AFTER_DUE) -> list[str]:
    return sorted(item.document_type.name for item in services.document_checklist(activity, now)["required"])


def client_for(member) -> Client:
    client = Client()
    client.force_login(member)
    return client


# --- The types and their due rule ------------------------------------------------------------------------------


def test_seed_creates_the_payment_types_due_five_days_after_the_end(roles) -> None:
    assert DOCUMENT_TYPES["Activity Report"] == DOCUMENT_TYPES["Event Photos"] == DOCUMENT_TYPES["Attendance Sheet"] == (
        True, DocumentType.DueReference.END_DATE, 5)
    for name, document_type in payment_types().items():
        assert document_type.is_required and document_type.due_reference == "end_date" and document_type.due_offset_days == 5, name


def test_the_activity_report_is_the_per_activity_report(roles) -> None:
    """ASSUMPTIONS 430 (replaces 425's "kept separate"): the per-activity report is the Activity Report; the Accomplishment
    Report is the quarterly / semi-annual / annual report in Reports, never a document type of a new database."""
    assert "Activity Report" in PAYMENT and not DocumentType.objects.filter(name="Accomplishment Report").exists()


def test_data_migration_adds_the_types_and_moves_only_the_old_attendance_sheet_rule(roles) -> None:
    migration = importlib.import_module("documents.migrations.0006_payment_documents")
    DocumentType.objects.filter(name__in=["Activity Report", "Event Photos"]).delete()
    DocumentType.objects.filter(name="Attendance Sheet").update(due_offset_days=0)
    migration.forwards(django_apps, None)
    types = payment_types()
    assert set(types) == set(PAYMENT) and all(t.due_offset_days == 5 for t in types.values())
    # A rule changed in the Django admin is never touched, in either direction.
    DocumentType.objects.filter(name="Attendance Sheet").update(due_offset_days=2)
    migration.forwards(django_apps, None)
    migration.backwards(django_apps, None)
    assert DocumentType.objects.get(name="Attendance Sheet").due_offset_days == 2
    assert not DocumentType.objects.filter(name__in=["Activity Report", "Event Photos"]).exists()  # unused: removed


def test_data_migration_adds_nothing_to_an_empty_database(roles) -> None:
    migration = importlib.import_module("documents.migrations.0006_payment_documents")
    DocumentType.objects.filter(name__in=["Activity Report", "Event Photos"]).delete()
    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(DocumentType.objects, "exists", lambda: False)  # as on a new database before seed_data
        migration.forwards(django_apps, None)
    assert not DocumentType.objects.filter(name__in=["Activity Report", "Event Photos"]).exists()


# --- Which activities need them ----------------------------------------------------------------------------------


def test_new_types_only_for_activities_ending_on_or_after_the_release_date(make_activity, settings) -> None:
    new = make_activity(ActivityStatus.CONDUCTED, start=NEW_END, end=NEW_END)
    old = make_activity(ActivityStatus.CONDUCTED, start=OLD_END, end=OLD_END)
    on_the_day = make_activity(ActivityStatus.CONDUCTED, start=settings.PAYMENT_DOCUMENTS_FROM, end=settings.PAYMENT_DOCUMENTS_FROM)
    assert {"Activity Report", "Event Photos", "Attendance Sheet"} <= set(required_names(new))
    assert {"Activity Report", "Event Photos", "Attendance Sheet"} <= set(required_names(on_the_day))
    assert "Attendance Sheet" in required_names(old)  # required for every activity, as before
    assert not {"Activity Report", "Event Photos"} & set(required_names(old))
    optional = [entry["document_type"].name for entry in services.document_checklist(old, AFTER_DUE)["optional"]]
    assert "Activity Report" in optional  # listed with the optional ones (an upload there is kept and shown)


def test_payment_documents_count_only_once_conducted(make_activity) -> None:
    ongoing = make_activity(ActivityStatus.ONGOING, start=NEW_END, end=NEW_END)
    conducted = make_activity(ActivityStatus.CONDUCTED, start=NEW_END, end=NEW_END)
    before = {i.document_type.name: i for i in services.document_checklist(ongoing, AFTER_DUE)["required"]}
    after = {i.document_type.name: i for i in services.document_checklist(conducted, AFTER_DUE)["required"]}
    for name in PAYMENT:
        assert not before[name].is_overdue and services.reminder_kind(before[name], AFTER_DUE.date()) is None
        assert after[name].is_overdue and after[name].due_date == date(2026, 12, 9)
    on_due = {i.document_type.name: i for i in services.document_checklist(
        conducted, timezone.make_aware(datetime(2026, 12, 9, 23, 0)))["required"]}
    assert not on_due["Event Photos"].is_overdue


def test_needs_attention_lists_payment_documents_on_their_own_line_after_conducted(make_activity) -> None:
    conducted = make_activity(ActivityStatus.CONDUCTED, start=NEW_END, end=NEW_END)
    ongoing = make_activity(ActivityStatus.ONGOING, start=NEW_END, end=NEW_END)
    texts = [item.text for item in attention.activity_attention(conducted, AFTER_DUE)]
    assert "Overdue payment documents for the Supply Office: Activity Report, Attendance Sheet, Event Photos." in texts
    assert not any("payment documents" in item.text for item in attention.activity_attention(ongoing, AFTER_DUE))


def test_reminders_skip_payment_documents_before_conducted(make_activity) -> None:
    from notifications.models import Notification

    ongoing = make_activity(ActivityStatus.ONGOING, start=NEW_END, end=NEW_END)
    services.send_document_reminders(AFTER_DUE)
    assert not Notification.objects.filter(activity=ongoing, document_type__name__in=PAYMENT).exists()
    conducted = make_activity(ActivityStatus.CONDUCTED, start=NEW_END, end=NEW_END, title="Done")
    services.send_document_reminders(AFTER_DUE)
    assert set(Notification.objects.filter(activity=conducted, type=Notification.DOCUMENT_OVERDUE)
               .values_list("document_type__name", flat=True)) >= set(PAYMENT)


def test_sql_compliance_matches_the_checklist_with_payment_documents(make_activity) -> None:
    make_activity(ActivityStatus.CONDUCTED, start=NEW_END, end=NEW_END, title="New")
    make_activity(ActivityStatus.CONDUCTED, start=OLD_END, end=OLD_END, title="Old")
    make_activity(ActivityStatus.ONGOING, start=NEW_END, end=NEW_END, title="Still ongoing")
    sql = {row["activity_id"]: row for row in metrics.document_compliance(Activity.objects.all(), AFTER_DUE)["activities"]}
    for activity in Activity.objects.all():
        expected = sorted((i.document_type.name, i.state, i.is_overdue)
                          for i in services.document_checklist(activity, AFTER_DUE)["required"])
        assert sorted((i["document_type"], i["state"], i["is_overdue"]) for i in sql[activity.pk]["items"]) == expected
    filters = metrics.ReportFilters(date(2026, 1, 1), date(2026, 12, 31))
    expected_overdue = sum(i.is_overdue for a in Activity.objects.exclude(status__name=ActivityStatus.DRAFT)
                           for i in services.document_checklist(a, AFTER_DUE)["required"])
    assert metrics.documents_now(filters, AFTER_DUE)["overdue"] == expected_overdue
    assert len(metrics.document_attention(filters, AFTER_DUE)["overdue"]) == expected_overdue


def test_the_compliance_report_says_not_required_for_older_activities(make_activity) -> None:
    from reports import content

    make_activity(ActivityStatus.CONDUCTED, start=OLD_END, end=OLD_END, title="Old")
    make_activity(ActivityStatus.CONDUCTED, start=NEW_END, end=NEW_END, title="New")
    filters = metrics.ReportFilters(date(2026, 1, 1), date(2026, 12, 31))
    section = content._documents(filters, AFTER_DUE)
    table = section.tables[1]
    names = [column.label for column in table.columns]
    old_row = next(row for row in table.rows if row[0] == "Old")
    assert old_row[names.index("Activity Report")] == content.NOT_REQUIRED
    assert metrics.fully_compliant_count(filters) == 0


# --- Event Photos: pictures only, one or more -------------------------------------------------------------------


def test_event_photos_take_only_pictures_and_more_than_one(director, make_activity) -> None:
    activity = make_activity(ActivityStatus.CONDUCTED, start=NEW_END, end=NEW_END)
    photos = payment_types()["Event Photos"]
    with pytest.raises(ValidationError) as excinfo:
        services.upload_document(director, activity, photos, make_file("photos.pdf"))
    assert excinfo.value.message_dict["file"] == ["Event Photos: only JPG and PNG pictures can be uploaded."]
    services.upload_document(director, activity, photos, make_file("one.jpg", JPG_BYTES))
    services.upload_document(director, activity, photos, make_file("two.png", PNG_BYTES))
    assert activity.documents.filter(document_type=photos).count() == 2


# --- The Documents tab group and the payment packet --------------------------------------------------------------


def test_documents_tab_shows_the_for_payment_group(director, make_activity) -> None:
    activity = make_activity(ActivityStatus.CONDUCTED, start=NEW_END, end=NEW_END)
    html = client_for(director).get(f"{reverse('activities:detail', args=[activity.pk])}?tab=documents").content.decode()
    assert "For payment (Supply Office)" in html
    assert "No payment document is approved yet." in html and "Download payment packet" not in html


def _approved(director, activity, type_name, name="file.pdf", content=None):
    document = services.upload_document(director, activity, payment_types()[type_name],
                                        make_file(name, content or make_file().read()))
    return services.approve_document(director, document)


def test_packet_is_refused_before_conducted_and_without_approved_files(director, make_activity) -> None:
    activity = make_activity(ActivityStatus.ONGOING, start=NEW_END, end=NEW_END)
    with pytest.raises(services.DocumentError, match="once the activity is Conducted"):
        services.build_payment_packet(director, activity)
    response = client_for(director).get(reverse("activities:payment_packet", args=[activity.pk]))
    assert response.status_code == 302 and response["Location"].endswith("?tab=documents")
    Activity.objects.filter(pk=activity.pk).update(status=ActivityStatus.objects.get(name=ActivityStatus.CONDUCTED))
    activity.refresh_from_db()
    with pytest.raises(services.DocumentError, match="No payment document is approved yet"):
        services.build_payment_packet(director, activity)


def test_packet_zips_approved_files_only_and_lists_the_rest(director, head, make_activity) -> None:
    activity = make_activity(ActivityStatus.CONDUCTED, start=NEW_END, end=NEW_END, title="Pitch Day")
    _approved(director, activity, "Attendance Sheet")
    _approved(director, activity, "Event Photos", "a.jpg", JPG_BYTES)
    _approved(director, activity, "Event Photos", "b.png", PNG_BYTES)
    services.upload_document(head, activity, payment_types()["Activity Report"], make_file())  # waiting: not included
    response = client_for(head).get(reverse("activities:payment_packet", args=[activity.pk]))
    assert response.status_code == 200 and response["Content-Type"] == "application/zip"
    assert response["Cache-Control"] == "private, no-store"
    archive = zipfile.ZipFile(io.BytesIO(b"".join(response.streaming_content)))
    names = archive.namelist()
    assert sorted(n.split("/")[0] for n in names if "/" in n) == ["attendance-sheet", "event-photos", "event-photos"]
    contents = archive.read(services.PACKET_CONTENTS).decode()
    assert "Only APPROVED files are included." in contents
    assert "Activity Report:\r\n  Included: nothing\r\n  Waiting for approval (not included): 1 file(s)\r\n  MISSING: no approved file yet" in contents
    assert "Event Photos:\r\n  Included: 2 approved file(s)" in contents


def test_packet_needs_a_staff_login(make_activity) -> None:
    activity = make_activity(ActivityStatus.CONDUCTED, start=NEW_END, end=NEW_END)
    response = Client().get(reverse("activities:payment_packet", args=[activity.pk]))
    assert response.status_code == 302 and "/accounts/login/" in response["Location"]
