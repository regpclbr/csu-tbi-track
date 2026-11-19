"""Documents on the New activity form (ASSUMPTIONS 327–328, 405): one service call
(documents.services.create_activity_with_documents) saves the activity and its documents in one transaction, with the
upload rules of the Documents tab; only types that make sense before the activity are offered. The Activity Design and
the Request Letter are required (rows 0 and 1 here, like the form sends them); the rules that follow from them are in
documents/tests/test_creation_documents.py."""

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


def required_rows() -> list[NewDocument]:
    """The two required rows (ASSUMPTIONS 405), always first, as the form sends them."""
    return [NewDocument(doc_type("Activity Design"), pdf("Design.pdf")), NewDocument(doc_type("Request Letter"), pdf("Request.pdf"))]


def required_post() -> dict:
    """The form's required fields `required-<type id>-file`."""
    return {f"required-{doc_type(name).pk}-file": pdf(f"{name}.pdf") for name in ("Activity Design", "Request Letter")}


@pytest.fixture
def create(director, valid_data):
    """Rows after the two required ones (row indexes in errors therefore start at 2)."""
    def make(documents: list[NewDocument], **overrides) -> Activity:
        return document_services.create_activity_with_documents(director, valid_data(**overrides), required_rows() + documents)

    return make


# --- Which types are offered -------------------------------------------------------------------------------------


def test_only_types_that_make_sense_before_the_activity_are_offered(roles) -> None:
    names = set(document_services.types_allowed_at_creation().values_list("name", flat=True))
    assert names == {"Activity Design", "Invitation Letter", "Pre-registration Form", "Request Letter", "Other"}


def test_settings_and_deactivation_change_the_offer(roles, settings) -> None:
    settings.DOCUMENT_TYPES_AFTER_ACTIVITY = ["Certificate"]  # adding Evaluation Form later needs only this setting
    DocumentType.objects.filter(name="Request Letter").update(is_active=False)
    names = set(document_services.types_allowed_at_creation().values_list("name", flat=True))
    assert "Evaluation Form" in names and "Request Letter" not in names and "Attendance Sheet" not in names


# --- The service ---------------------------------------------------------------------------------------------------


def test_only_the_two_required_documents(create) -> None:
    activity = create([])
    assert activity.status.name == "Draft" and activity.audit_logs.count() == 1
    assert activity.creation_documents_required is True
    assert sorted(activity.documents.values_list("document_type__name", flat=True)) == ["Activity Design", "Request Letter"]
    assert len(stored_files()) == 2


def test_without_the_required_documents_nothing_is_created(director, valid_data) -> None:
    with pytest.raises(document_services.NewActivityError) as caught:
        document_services.create_activity_with_documents(director, valid_data(), [])
    assert caught.value.missing_types == ["Activity Design", "Request Letter"]
    assert not Activity.objects.exists() and not Document.objects.exists() and not stored_files()


def test_one_document_is_uploaded_with_the_upload_rules(create, director) -> None:
    activity = create([NewDocument(doc_type("Invitation Letter"), pdf(), "  Invitation to the workshop ")])
    document = Document.objects.get(document_type__name="Invitation Letter")
    assert document.activity == activity and document.uploaded_by == director
    assert document.status.status_name == DocumentStatus.FOR_REVIEW and document.reviewed_at is None  # waiting for review
    assert document.title == "Invitation to the workshop" and document.file_extension == "pdf"
    assert document.file_path.name.startswith("documents/") and document.file_path.storage.exists(document.file_path.name)
    assert activity.audit_logs.count() == 1
    assert not OrmQ.objects.exists()  # nothing is indexed before approval
    checklist = document_services.document_checklist(activity)  # optional since ASSUMPTIONS 386: an "other" document
    entry = next(entry for entry in checklist["optional"] if entry["document_type"].name == "Invitation Letter")
    assert entry["documents"] == [document]
    assert "Invitation Letter" not in {item.document_type.name for item in checklist["required"]}


def test_several_documents(create) -> None:
    activity = create([
        NewDocument(doc_type("Invitation Letter"), pdf()),
        NewDocument(doc_type("Activity Design"), SimpleUploadedFile("Design.png", PNG)),  # a second one is allowed
        NewDocument(doc_type("Pre-registration Form"), pdf("Form.pdf"), "Form"),
    ])
    assert activity.documents.count() == 5
    assert sorted(activity.documents.values_list("title", flat=True)) == [
        "Activity Design", "Activity Design", "Form", "Invitation Letter", "Request Letter"]
    assert len(stored_files()) == 5


def test_an_invalid_file_in_row_2_saves_nothing_and_notifies_nobody(create) -> None:
    with pytest.raises(document_services.NewActivityError) as caught:
        create([
            NewDocument(doc_type("Invitation Letter"), pdf()),
            NewDocument(doc_type("Activity Design"), SimpleUploadedFile("Design.pdf", b"not a pdf")),
        ], title="Rolled back")
    assert caught.value.row_errors == {3: {"file": "The file's content is not a valid PDF file."}}
    assert caught.value.activity_error is None
    assert not Activity.objects.filter(title="Rolled back").exists()
    assert not Document.objects.exists() and not stored_files()
    assert not Notification.objects.exists() and not OrmQ.objects.exists()


def test_a_failure_while_saving_removes_the_files_already_stored(create, monkeypatch) -> None:
    """If an upload fails inside the transaction (after row 0, the Activity Design, was stored), the database rolls
    back and row 0's file is deleted again."""
    real_upload = document_services.upload_document
    calls = []

    def flaky_upload(*args, **kwargs):
        calls.append(1)
        if len(calls) == 2:
            raise ValidationError({"document_type": ["This document type is not active."]})
        return real_upload(*args, **kwargs)

    monkeypatch.setattr(document_services, "upload_document", flaky_upload)
    with pytest.raises(document_services.NewActivityError) as caught:
        create([NewDocument(doc_type("Invitation Letter"), pdf())])
    assert caught.value.row_errors == {1: {"document_type": "This document type is not active."}}
    assert not Activity.objects.exists() and not Document.objects.exists() and not stored_files()
    assert not Notification.objects.exists()


@pytest.mark.parametrize("name", ["Attendance Sheet", "Accomplishment Report", "Certificate", "Evaluation Form"])
def test_a_type_not_allowed_at_creation_is_refused_by_the_service(create, name) -> None:
    with pytest.raises(document_services.NewActivityError) as caught:
        create([NewDocument(doc_type(name), pdf())])
    assert "cannot be uploaded when creating an activity" in caught.value.row_errors[2]["document_type"]
    assert not Activity.objects.exists() and not stored_files()


def test_activity_errors_and_row_errors_come_back_together(create) -> None:
    with pytest.raises(document_services.NewActivityError) as caught:
        create([NewDocument(None, None)], max_slots=0)
    assert caught.value.activity_error.message_dict == {"max_slots": ["Max slots must be at least 1."]}
    assert caught.value.row_errors == {2: {"document_type": "Choose a document type.", "file": "Choose a file to upload."}}
    assert not Activity.objects.exists()


def test_needs_can_create_activity(view_only, valid_data) -> None:
    with pytest.raises(PermissionDenied):
        document_services.create_activity_with_documents(view_only, valid_data(), [])
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
            "start_time": values["start_time"].strftime("%H:%M"), "end_time": values["end_time"].strftime("%H:%M"), "venue": values["venue"], "max_slots": values["max_slots"],
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
    assert 'enctype="multipart/form-data"' in html and "Other documents (optional)" in html
    for name in ("Activity Design", "Request Letter"):  # their own required fields, not in the dropdown
        assert f'name="required-{doc_type(name).pk}-file"' in html and f">{name}</option>" not in html
    for name in ("Invitation Letter", "Pre-registration Form", "Other"):
        assert f">{name}</option>" in html
    for name in ("Attendance Sheet", "Accomplishment Report", "Certificate", "Evaluation Form"):
        assert f">{name}</option>" not in html
    assert 'x-for="key in added"' in html and "Add a document" in html
    assert "max: 8" in html and "up to 10 documents" in html  # the limit of 10 counts the two required files
    activity = document_services.create_activity_with_documents(director, valid_data(), required_rows())
    edit = browser.get(reverse("activities:edit", args=[activity.pk])).content.decode()
    assert "Other documents (optional)" not in edit and "required-" not in edit


def test_posting_documents_opens_the_documents_tab(browser, post_data) -> None:
    data = post_data(title="With files")
    data.update(required_post())
    data.update({"documents-0-type": doc_type("Invitation Letter").pk, "documents-0-file": pdf(), "documents-0-title": "",
                 "documents-3-type": "", "documents-3-title": ""})  # a row left empty is ignored
    response = browser.post(CREATE, data, follow=True)
    activity = Activity.objects.get(title="With files")
    assert response.redirect_chain[-1][0] == reverse("activities:detail", args=[activity.pk]) + "?tab=documents"
    html = response.content.decode()
    assert "was created as Draft with 3 documents waiting for review." in html
    assert activity.documents.count() == 3 and "Waiting for review" in html
    assert activity.creation_documents_required is True


def test_a_bad_file_shows_its_error_under_the_row_and_asks_to_choose_again(browser, post_data) -> None:
    data = post_data(title="Not saved")
    data.update(required_post())
    data.update({"documents-0-type": doc_type("Invitation Letter").pk, "documents-0-file": pdf(),
                 "documents-1-type": doc_type("Pre-registration Form").pk, "documents-1-file": SimpleUploadedFile("a.pdf", b"x"),
                 "documents-1-title": "My form"})
    response = browser.post(CREATE, data)
    html = response.content.decode()
    assert response.status_code == 200 and not Activity.objects.exists() and not stored_files()
    assert 'id="doc-1-file-error" class="field-error">The file&#x27;s content is not a valid PDF file.' in html
    assert "please choose the files again" in html and "Please correct the documents marked below." in html
    assert 'value="My form"' in html  # the type and title are kept, only the files must be chosen again
    assert f'<option value="{doc_type("Pre-registration Form").pk}" selected>' in html


def test_field_errors_and_row_errors_show_together(browser, post_data) -> None:
    data = post_data(title="")
    data.update({"documents-0-type": doc_type("Attendance Sheet").pk, "documents-0-file": pdf()})
    html = browser.post(CREATE, data).content.decode()
    assert "This field is required." in html and "cannot be uploaded when creating an activity" in html
    assert not Activity.objects.exists()


def test_other_type_needs_a_title_at_creation_too(create, roles) -> None:
    """The title rule of the Documents tab (services.title_error, ASSUMPTIONS 386) also applies on the New activity form."""
    with pytest.raises(document_services.NewActivityError) as refused:
        create([NewDocument(doc_type("Other"), pdf(), "   ")])
    assert refused.value.row_errors == {2: {"title": document_services.OTHER_TITLE_REQUIRED}}
    assert not Document.objects.exists() and not stored_files()
    create([NewDocument(doc_type("Other"), pdf(), "MOA with ABC Corp.")])
    assert Document.objects.get(document_type__name="Other").title == "MOA with ABC Corp."


# --- The required fields on the page (ASSUMPTIONS 405) -------------------------------------------------------------


def test_posting_without_the_required_files_shows_both_errors_and_saves_nothing(browser, post_data) -> None:
    response = browser.post(CREATE, post_data(title="No files"))
    html = response.content.decode()
    assert response.status_code == 200 and not Activity.objects.exists() and not stored_files()
    for name in ("Activity Design", "Request Letter"):
        pk = doc_type(name).pk
        assert f'id="required-{pk}-file-error" class="field-error">Choose a file to upload.' in html
    assert "Please correct the documents marked below." in html


def test_a_bad_required_file_is_refused_like_any_upload(browser, post_data, settings) -> None:
    data = post_data(title="Bad design")
    data.update(required_post())
    design = doc_type("Activity Design").pk
    data[f"required-{design}-file"] = SimpleUploadedFile("design.pdf", b"renamed text file")  # checked by real content
    data[f"required-{design}-title"] = "Design v2"
    html = browser.post(CREATE, data).content.decode()
    assert f'id="required-{design}-file-error" class="field-error">The file&#x27;s content is not a valid PDF file.' in html
    assert 'value="Design v2"' in html and "please choose the files again" in html
    assert not Activity.objects.exists() and not Document.objects.exists() and not stored_files()
    settings.DOCUMENT_MAX_UPLOAD_MB = 0  # the same size limit as the Documents tab
    data = post_data(title="Too big")
    data.update(required_post())
    assert "larger than 0 MB" in browser.post(CREATE, data).content.decode()
    assert not Activity.objects.exists()


def test_the_required_files_are_saved_waiting_for_approval(browser, post_data, director) -> None:
    data = post_data(title="Both files")
    data.update(required_post())
    data[f"required-{doc_type('Request Letter').pk}-title"] = "Request to the Dean"
    browser.post(CREATE, data)
    activity = Activity.objects.get(title="Both files")
    documents = {d.document_type.name: d for d in activity.documents.select_related("document_type", "status")}
    assert set(documents) == {"Activity Design", "Request Letter"}
    assert documents["Request Letter"].title == "Request to the Dean"
    assert documents["Activity Design"].title == "Activity Design"
    assert all(d.review_state == Document.WAITING_FOR_REVIEW and d.uploaded_by == director for d in documents.values())
