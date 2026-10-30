"""Optional documents on the New activity form (ASSUMPTIONS 327–328): one service call
(documents.services.create_activity_with_documents) saves the activity and its documents in one transaction, with the
upload rules of the Documents tab; only types that make sense before the activity are offered."""

from pathlib import Path

import pytest
from django.conf import settings
from django.core.exceptions import PermissionDenied, ValidationError
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import Client
from django.urls import reverse
from django.utils import timezone
from django_q.models import OrmQ

from activities.models import Activity
from documents import services as document_services
from documents.models import Document, DocumentStatus, DocumentType
from notifications.models import Notification

pytestmark = pytest.mark.django_db

CREATE = reverse("activities:create")
NewDocument = document_services.NewDocument
PDF = b"%PDF-1.4\n1 0 obj << >> endobj\ntrailer << >>\n%%EOF\n"
PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 32


def pdf(name: str = "Invitation.pdf") -> SimpleUploadedFile:
    return SimpleUploadedFile(name, PDF)


def doc_type(name: str) -> DocumentType:
    return DocumentType.objects.get(name=name)


def stored_files() -> list[Path]:
    return [path for path in Path(settings.MEDIA_ROOT).rglob("*") if path.is_file()]


@pytest.fixture
def create(director, valid_data):
    def make(documents: list[NewDocument], **overrides) -> Activity:
        return document_services.create_activity_with_documents(director, valid_data(**overrides), documents)

    return make


# --- Which types are offered -------------------------------------------------------------------------------------


def test_only_types_that_make_sense_before_the_activity_are_offered(roles) -> None:
    names = set(document_services.types_allowed_at_creation().values_list("name", flat=True))
    assert names == {"Activity Design", "Invitation Letter", "Pre-registration Form", "Request Letter"}


def test_settings_and_deactivation_change_the_offer(roles, settings) -> None:
    settings.DOCUMENT_TYPES_AFTER_ACTIVITY = ["Certificate"]  # adding Evaluation Form later needs only this setting
    DocumentType.objects.filter(name="Request Letter").update(is_active=False)
    names = set(document_services.types_allowed_at_creation().values_list("name", flat=True))
    assert "Evaluation Form" in names and "Request Letter" not in names and "Attendance Sheet" not in names


# --- The service ---------------------------------------------------------------------------------------------------


def test_no_documents_creates_the_activity_exactly_as_before(create) -> None:
    activity = create([])
    assert activity.status.name == "Draft" and activity.audit_logs.count() == 1
    assert not Document.objects.exists() and not stored_files()


def test_one_document_is_uploaded_with_the_upload_rules(create, director) -> None:
    activity = create([NewDocument(doc_type("Invitation Letter"), pdf(), "  Invitation to the workshop ")])
    document = Document.objects.get()
    assert document.activity == activity and document.uploaded_by == director
    assert document.status.status_name == DocumentStatus.FOR_REVIEW and document.reviewed_at is None  # waiting for review
    assert document.title == "Invitation to the workshop" and document.file_extension == "pdf"
    assert document.file_path.name.startswith("documents/") and document.file_path.storage.exists(document.file_path.name)
    assert activity.audit_logs.count() == 1
    assert not OrmQ.objects.exists()  # nothing is indexed before approval
    checklist = document_services.document_checklist(activity)
    item = next(item for item in checklist["required"] if item.document_type.name == "Invitation Letter")
    assert item.documents == [document]


def test_several_documents(create) -> None:
    activity = create([
        NewDocument(doc_type("Invitation Letter"), pdf()),
        NewDocument(doc_type("Activity Design"), SimpleUploadedFile("Design.png", PNG)),
        NewDocument(doc_type("Pre-registration Form"), pdf("Form.pdf"), "Form"),
    ])
    assert activity.documents.count() == 3
    assert sorted(activity.documents.values_list("title", flat=True)) == ["Activity Design", "Form", "Invitation Letter"]
    assert len(stored_files()) == 3


def test_an_invalid_file_in_row_2_saves_nothing_and_notifies_nobody(create) -> None:
    with pytest.raises(document_services.NewActivityError) as caught:
        create([
            NewDocument(doc_type("Invitation Letter"), pdf()),
            NewDocument(doc_type("Activity Design"), SimpleUploadedFile("Design.pdf", b"not a pdf")),
        ], title="Rolled back")
    assert caught.value.row_errors == {1: {"file": "The file's content is not a valid PDF file."}}
    assert caught.value.activity_error is None
    assert not Activity.objects.filter(title="Rolled back").exists()
    assert not Document.objects.exists() and not stored_files()
    assert not Notification.objects.exists() and not OrmQ.objects.exists()


def test_a_failure_while_saving_removes_the_files_already_stored(create, monkeypatch) -> None:
    """If an upload fails inside the transaction (after row 1 was stored), the database rolls back and row 1's file
    is deleted again."""
    real_upload = document_services.upload_document
    calls = []

    def flaky_upload(*args, **kwargs):
        calls.append(1)
        if len(calls) == 2:
            raise ValidationError({"document_type": ["This document type is not active."]})
        return real_upload(*args, **kwargs)

    monkeypatch.setattr(document_services, "upload_document", flaky_upload)
    with pytest.raises(document_services.NewActivityError) as caught:
        create([NewDocument(doc_type("Invitation Letter"), pdf()), NewDocument(doc_type("Request Letter"), pdf("R.pdf"))])
    assert caught.value.row_errors == {1: {"document_type": "This document type is not active."}}
    assert not Activity.objects.exists() and not Document.objects.exists() and not stored_files()
    assert not Notification.objects.exists()


@pytest.mark.parametrize("name", ["Attendance Sheet", "Accomplishment Report", "Certificate", "Evaluation Form"])
def test_a_type_not_allowed_at_creation_is_refused_by_the_service(create, name) -> None:
    with pytest.raises(document_services.NewActivityError) as caught:
        create([NewDocument(doc_type(name), pdf())])
    assert "cannot be uploaded when creating an activity" in caught.value.row_errors[0]["document_type"]
    assert not Activity.objects.exists() and not stored_files()


def test_activity_errors_and_row_errors_come_back_together(create) -> None:
    with pytest.raises(document_services.NewActivityError) as caught:
        create([NewDocument(None, None)], max_slots=0)
    assert caught.value.activity_error.message_dict == {"max_slots": ["Max slots must be at least 1."]}
    assert caught.value.row_errors == {0: {"document_type": "Choose a document type.", "file": "Choose a file to upload."}}
    assert not Activity.objects.exists()


def test_needs_can_create_activity(admin_staff, valid_data) -> None:
    with pytest.raises(PermissionDenied):
        document_services.create_activity_with_documents(admin_staff, valid_data(), [])
    assert not Activity.objects.exists()


def test_at_most_ten_documents(create) -> None:
    with pytest.raises(ValidationError):
        create([NewDocument(doc_type("Invitation Letter"), pdf(f"{n}.pdf")) for n in range(11)])
    assert not Activity.objects.exists()


# --- The page ------------------------------------------------------------------------------------------------------


@pytest.fixture
def post_data(valid_data):
    def make(**overrides) -> dict:
        values = valid_data(**overrides)
        return {
            "title": values["title"], "description": values["description"],
            "activity_type": values["activity_type"].pk,
            "participant_categories": [category.pk for category in values["participant_categories"]],
            "start_date": values["start_date"].isoformat(), "end_date": values["end_date"].isoformat(),
            "time_schedule": values["time_schedule"], "venue": values["venue"], "max_slots": values["max_slots"],
            "registration_deadline": timezone.localtime(values["registration_deadline"]).strftime("%Y-%m-%dT%H:%M"),
            "evaluation_deadline": timezone.localtime(values["evaluation_deadline"]).strftime("%Y-%m-%dT%H:%M"),
            "budget_amount": str(values["budget_amount"]),
        }

    return make


@pytest.fixture
def browser(director) -> Client:
    client = Client()
    client.force_login(director)
    return client


def test_new_page_offers_only_the_allowed_types_and_edit_has_no_section(browser, director, valid_data) -> None:
    html = browser.get(CREATE).content.decode()
    assert 'enctype="multipart/form-data"' in html and "Documents (optional)" in html
    for name in ("Invitation Letter", "Activity Design", "Request Letter", "Pre-registration Form"):
        assert f">{name}</option>" in html
    for name in ("Attendance Sheet", "Accomplishment Report", "Certificate", "Evaluation Form"):
        assert f">{name}</option>" not in html
    assert 'x-for="key in added"' in html and "Add a document" in html
    activity = document_services.create_activity_with_documents(director, valid_data(), [])
    assert "Documents (optional)" not in browser.get(reverse("activities:edit", args=[activity.pk])).content.decode()


def test_posting_documents_opens_the_documents_tab(browser, post_data) -> None:
    data = post_data(title="With files")
    data.update({"documents-0-type": doc_type("Invitation Letter").pk, "documents-0-file": pdf(), "documents-0-title": "",
                 "documents-3-type": "", "documents-3-title": ""})  # a row left empty is ignored
    response = browser.post(CREATE, data, follow=True)
    activity = Activity.objects.get(title="With files")
    assert response.redirect_chain[-1][0] == reverse("activities:detail", args=[activity.pk]) + "?tab=documents"
    html = response.content.decode()
    assert "was created as Draft with 1 document waiting for review." in html
    assert activity.documents.count() == 1 and "Waiting for review" in html


def test_a_bad_file_shows_its_error_under_the_row_and_asks_to_choose_again(browser, post_data) -> None:
    data = post_data(title="Not saved")
    data.update({"documents-0-type": doc_type("Invitation Letter").pk, "documents-0-file": pdf(),
                 "documents-1-type": doc_type("Activity Design").pk, "documents-1-file": SimpleUploadedFile("a.pdf", b"x"),
                 "documents-1-title": "My design"})
    response = browser.post(CREATE, data)
    html = response.content.decode()
    assert response.status_code == 200 and not Activity.objects.exists() and not stored_files()
    assert 'id="doc-1-file-error" class="field-error">The file&#x27;s content is not a valid PDF file.' in html
    assert "please choose the files again" in html and "Please correct the documents marked below." in html
    assert 'value="My design"' in html  # the type and title are kept, only the files must be chosen again
    assert f'<option value="{doc_type("Activity Design").pk}" selected>' in html


def test_field_errors_and_row_errors_show_together(browser, post_data) -> None:
    data = post_data(title="")
    data.update({"documents-0-type": doc_type("Attendance Sheet").pk, "documents-0-file": pdf()})
    html = browser.post(CREATE, data).content.decode()
    assert "This field is required." in html and "cannot be uploaded when creating an activity" in html
    assert not Activity.objects.exists()
