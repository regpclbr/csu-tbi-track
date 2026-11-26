"""Documents on the New activity form (ASSUMPTIONS 327–328, 405): one service call
(documents.services.create_activity_with_documents) saves the activity and its documents in one transaction, with the
upload rules of the Documents tab; only types that make sense before the activity are offered. The Activity Design and
the Request Letter are optional (ASSUMPTIONS 417; rows 0 and 1 here when given, like the form sends them); with both the
activity is created as Scheduled, otherwise as Draft (ASSUMPTIONS 431, no "Create as" choice); the rules that follow from them are in documents/tests/test_creation_documents.py."""

from pathlib import Path

import pytest

from conftest import typed_schedule_fields
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

from .test_pages import messages_of

pytestmark = pytest.mark.django_db

CREATE = reverse("activities:create")
NewDocument = document_services.NewDocument
PDF = b"%PDF-1.4\n1 0 obj << >> endobj\ntrailer << >>\n%%EOF\n"
PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 32
DESIGN_TITLE = "Activity Design - Product Development Workshop"
REQUEST_TITLE = "Request Letter - Product Development Workshop"


def pdf(name: str = "Invitation.pdf") -> SimpleUploadedFile:
    return SimpleUploadedFile(name, PDF)


def doc_type(name: str) -> DocumentType:
    return DocumentType.objects.get(name=name)


def stored_files() -> list[Path]:
    return [path for path in Path(settings.MEDIA_ROOT).rglob("*") if path.is_file()]


def required_rows() -> list[NewDocument]:
    """The two required rows (ASSUMPTIONS 405), always first, as the form sends them, with their required titles (410)."""
    return [NewDocument(doc_type("Activity Design"), pdf("Design.pdf"), DESIGN_TITLE),
            NewDocument(doc_type("Request Letter"), pdf("Request.pdf"), REQUEST_TITLE)]


def required_post() -> dict:
    """The form's required fields `required-<type id>-file|title`."""
    data = {}
    for name, title in (("Activity Design", DESIGN_TITLE), ("Request Letter", REQUEST_TITLE)):
        data[f"required-{doc_type(name).pk}-file"] = pdf(f"{name}.pdf")
        data[f"required-{doc_type(name).pk}-title"] = title
    return data


@pytest.fixture
def create(director, valid_data):
    """Created with both documents (so Scheduled, ASSUMPTIONS 431); rows after the two (row indexes in errors therefore
    start at 2)."""
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
    assert activity.status.name == "Scheduled" and activity.audit_logs.filter(field_changed="Status").count() == 2  # Draft, then Scheduled (409)
    assert activity.creation_documents_required is True
    assert sorted(activity.documents.values_list("document_type__name", flat=True)) == ["Activity Design", "Request Letter"]
    assert len(stored_files()) == 2


def test_without_documents_it_is_created_as_a_draft(director, valid_data) -> None:
    """ASSUMPTIONS 417 / 431: the documents are optional; without them it is a Draft; the creation rule applies later."""
    activity = document_services.create_activity_with_documents(director, valid_data(), [])
    assert activity.status.name == "Draft" and activity.creation_documents_required is True
    assert list(activity.audit_logs.filter(field_changed="Status").values_list("field_changed", "old_value", "new_value")) == [("Status", "", "Draft")]
    assert not Document.objects.exists() and not stored_files()


def test_both_documents_make_it_scheduled_automatically(director, valid_data) -> None:
    """ASSUMPTIONS 431: no choice; both documents -> Scheduled through change_status by the creator (two History rows)."""
    activity = document_services.create_activity_with_documents(director, valid_data(), required_rows())
    assert activity.status.name == "Scheduled" and activity.not_scheduled_reason == ""
    rows = list(activity.audit_logs.filter(field_changed="Status").order_by("log_id").values_list("field_changed", "old_value", "new_value", "changed_by"))
    assert rows == [("Status", "", "Draft", director.pk), ("Status", "Draft", "Scheduled", director.pk)]


@pytest.mark.parametrize("given", [[], ["Activity Design"], ["Request Letter"]])
def test_one_or_no_document_creates_a_draft_and_keeps_the_upload(director, valid_data, given) -> None:
    """ASSUMPTIONS 431: never refused for missing documents any more; the given one is saved and waits for review."""
    rows = [row for row in required_rows() if row.document_type.name in given]
    activity = document_services.create_activity_with_documents(director, valid_data(title="One"), rows)
    assert activity.status.name == "Draft" and activity.not_scheduled_reason == ""
    assert sorted(activity.documents.values_list("document_type__name", flat=True)) == given
    assert len(stored_files()) == len(given)
    assert list(activity.audit_logs.filter(field_changed="Status").values_list("new_value", flat=True)) == ["Draft"]


def test_a_refused_scheduled_keeps_the_activity_and_its_uploads_as_draft(director, valid_data, monkeypatch) -> None:
    """ASSUMPTIONS 431: when change_status refuses Scheduled for another reason (simulated: the transition table allows
    nothing from Draft), the activity, its documents, files and notifications stay; it is a Draft with the reason."""
    from activities import services as activity_services

    monkeypatch.setitem(activity_services.ALLOWED_STATUS_MOVES, "Draft", set())
    rows = required_rows() + [NewDocument(doc_type("Invitation Letter"), pdf())]
    activity = document_services.create_activity_with_documents(director, valid_data(title="Kept"), rows)
    activity.refresh_from_db()
    assert activity.status.name == "Draft"
    assert activity.documents.count() == 3 and len(stored_files()) == 3
    assert activity.not_scheduled_reason.startswith("Cannot change status from Draft to Scheduled.")
    assert list(activity.audit_logs.filter(field_changed="Status").values_list("new_value", flat=True)) == ["Draft"]


def test_a_refused_scheduled_is_said_in_the_success_message(browser, post_data, monkeypatch) -> None:
    from activities import services as activity_services

    monkeypatch.setitem(activity_services.ALLOWED_STATUS_MOVES, "Draft", set())
    data = post_data(title="Refused move")
    data.update(required_post())
    response = browser.post(CREATE, data, follow=True)
    activity = Activity.objects.get(title="Refused move")
    assert activity.status.name == "Draft" and activity.documents.count() == 2
    assert response.redirect_chain[-1][0] == reverse("activities:detail", args=[activity.pk])
    [text] = messages_of(response)
    assert text.startswith('"Refused move" was created as Draft with 2 documents. It could not be '
                           "Scheduled: Cannot change status from Draft to Scheduled.")


def test_a_draft_becomes_scheduled_when_the_last_document_is_uploaded(director, valid_data) -> None:
    """ASSUMPTIONS 431: the later upload moves it by itself (System), with the reason in History; the creator uploaded
    it, so nobody is notified about it."""
    from activities import services as activity_services

    activity = document_services.create_activity_with_documents(director, valid_data(), required_rows()[:1])
    with pytest.raises(activity_services.ActivityError, match="Upload the Request Letter before scheduling"):
        activity_services.change_status(director, activity, "Scheduled")  # the hand move keeps its rule
    document = document_services.upload_document(director, activity, doc_type("Request Letter"), pdf("r.pdf"), "")
    activity.refresh_from_db()
    assert document.scheduled_activity is True and activity.status.name == "Scheduled"
    row = activity.audit_logs.filter(field_changed="Status").order_by("-log_id").values_list("field_changed", "old_value", "new_value", "changed_by").first()
    assert row == ("Status", "Draft", "Scheduled (both documents uploaded)", None)
    assert not Notification.objects.filter(type=Notification.ACTIVITY_SCHEDULED).exists()


def test_a_file_without_a_title_is_still_refused(director, valid_data) -> None:
    """ASSUMPTIONS 410 kept: also on a Draft."""
    rows = required_rows()[:1]
    rows[0].title = " "
    with pytest.raises(document_services.NewActivityError) as caught:
        document_services.create_activity_with_documents(director, valid_data(), rows)
    assert caught.value.row_errors == {0: {"title": "Enter a title for the Activity Design."}}
    assert not Activity.objects.exists()


def test_one_document_is_uploaded_with_the_upload_rules(create, director) -> None:
    activity = create([NewDocument(doc_type("Invitation Letter"), pdf(), "  Invitation to the workshop ")])
    document = Document.objects.get(document_type__name="Invitation Letter")
    assert document.activity == activity and document.uploaded_by == director
    assert document.status.status_name == DocumentStatus.APPROVED and document.reviewed_by is None  # approved copy (432)
    assert document.title == "Invitation to the workshop" and document.file_extension == "pdf"
    assert document.file_path.name.startswith("documents/") and document.file_path.storage.exists(document.file_path.name)
    assert activity.audit_logs.filter(field_changed="Status").count() == 2
    assert activity.audit_logs.filter(field_changed="Documents").count() == 3  # each approved copy (432)
    checklist = document_services.document_checklist(activity)  # optional since ASSUMPTIONS 386: an "other" document
    entry = next(entry for entry in checklist["optional"] if entry["document_type"].name == "Invitation Letter")
    assert entry["documents"] == [document]
    assert "Invitation Letter" not in {item.document_type.name for item in checklist["required"]}


def test_several_documents(create) -> None:
    activity = create([
        NewDocument(doc_type("Invitation Letter"), pdf()),
        NewDocument(doc_type("Activity Design"), SimpleUploadedFile("Design.png", PNG), "Design v2"),  # a second one is allowed
        NewDocument(doc_type("Pre-registration Form"), pdf("Form.pdf"), "Form"),
    ])
    assert activity.documents.count() == 5
    assert sorted(activity.documents.values_list("title", flat=True)) == [
        DESIGN_TITLE, "Design v2", "Form", "Invitation Letter", REQUEST_TITLE]
    assert len(stored_files()) == 5


def test_an_invalid_file_in_row_2_saves_nothing_and_notifies_nobody(create) -> None:
    with pytest.raises(document_services.NewActivityError) as caught:
        create([
            NewDocument(doc_type("Invitation Letter"), pdf()),
            NewDocument(doc_type("Activity Design"), SimpleUploadedFile("Design.pdf", b"not a pdf"), "Design v2"),
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


@pytest.mark.parametrize("name", ["Attendance Sheet", "Certificate", "Evaluation Form"])  # no Accomplishment Report (430)
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
            **typed_schedule_fields(values), "venue": values["venue"], "max_slots": values["max_slots"],
            "fund_source": values["fund_source"].pk,
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
    for name in ("Attendance Sheet", "Certificate", "Evaluation Form"):
        assert f">{name}</option>" not in html
    assert 'x-for="key in added"' in html and "Add a document" in html
    assert "max: 8" in html and "up to 10 documents" in html  # the limit of 10 counts the two required files
    activity = document_services.create_activity_with_documents(director, valid_data(), required_rows())
    edit = browser.get(reverse("activities:edit", args=[activity.pk])).content.decode()
    assert "Other documents (optional)" not in edit and "required-" not in edit


def test_posting_documents_saves_them_and_opens_the_forms_tab(browser, post_data) -> None:
    data = post_data(title="With files")
    data.update(required_post())
    data.update({"documents-0-type": doc_type("Invitation Letter").pk, "documents-0-file": pdf(), "documents-0-title": "",
                 "documents-3-type": "", "documents-3-title": ""})  # a row left empty is ignored
    response = browser.post(CREATE, data, follow=True)
    activity = Activity.objects.get(title="With files")
    assert response.redirect_chain[-1][0] == reverse("activities:detail", args=[activity.pk]) + "?tab=forms"
    html = response.content.decode()
    assert "was created as Scheduled with 3 documents." in html
    assert activity.documents.count() == 3 and "Create from defaults" in html  # the next step (ASSUMPTIONS 409)
    assert all(d.review_state == Document.APPROVED for d in activity.documents.select_related("status"))  # 432
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
    assert "Choose them again before creating the activity" in html and "Please correct the documents marked below." in html
    for name in ("Invitation Letter (Invitation.pdf)", "Activity Design (Activity Design.pdf)", "Pre-registration Form (a.pdf)"):
        assert f"<li>{name}</li>" in html
    assert 'id="doc-0-file-again" class="field-help text-warning">Choose “Invitation.pdf” again.' in html
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


# --- The two fixed rows on the page; the documents decide the status (ASSUMPTIONS 405, 417, 431) ----------------------


def test_the_page_has_no_create_as_and_one_primary_button(browser) -> None:
    html = browser.get(CREATE).content.decode()
    assert "Create as" not in html and 'name="create_as"' not in html
    assert ("Upload the approved copies. With the Activity Design and the Request Letter, the activity is Scheduled when "
            "you create it. Without them it stays Draft until both are uploaded.") in html
    form = html.split('<form data-enter-submit="off" method="post" novalidate')[1].split("</form>")[0]  # ASSUMPTIONS 450  # the "Add a person" dialog has its own
    assert form.count("btn btn-primary") == 1  # one primary button on the form: Create activity (ASSUMPTIONS 413)


def test_posting_without_files_creates_a_draft_and_opens_the_overview(browser, post_data) -> None:
    response = browser.post(CREATE, post_data(title="No files"), follow=True)
    activity = Activity.objects.get(title="No files")
    assert activity.status.name == "Draft" and not stored_files()
    assert response.redirect_chain[-1][0] == reverse("activities:detail", args=[activity.pk])
    assert messages_of(response) == [
        '"No files" was created as Draft. Upload the Activity Design and the Request Letter on the Documents tab: '
        "it becomes Scheduled as soon as both documents are uploaded."]


def test_one_file_on_the_page_creates_a_draft_with_it(browser, post_data) -> None:
    data = post_data(title="Only the design", venue="CSU Hall B")
    design = doc_type("Activity Design").pk
    data.update({f"required-{design}-file": pdf("design.pdf"), f"required-{design}-title": "Design v1",
                 "documents-0-type": doc_type("Invitation Letter").pk, "documents-0-file": pdf("invite.pdf"),
                 "documents-0-title": "Invite"})
    response = browser.post(CREATE, data, follow=True)
    activity = Activity.objects.get(title="Only the design")
    assert activity.status.name == "Draft" and activity.documents.count() == 2 and len(stored_files()) == 2
    assert response.redirect_chain[-1][0] == reverse("activities:detail", args=[activity.pk])
    assert messages_of(response) == [
        '"Only the design" was created as Draft with 2 documents. Upload the Request Letter on the '
        "Documents tab: it becomes Scheduled as soon as both documents are uploaded."]


def test_scheduled_with_both_on_the_page_opens_the_forms_tab(browser, post_data) -> None:
    data = post_data(title="Ready")
    data.update(required_post())
    response = browser.post(CREATE, data, follow=True)
    activity = Activity.objects.get(title="Ready")
    assert activity.status.name == "Scheduled"
    assert response.redirect_chain[-1][0] == reverse("activities:detail", args=[activity.pk]) + "?tab=forms"


def test_a_title_without_a_file_asks_for_the_file(browser, post_data) -> None:
    data = post_data(title="Title only")
    design = doc_type("Activity Design").pk
    data[f"required-{design}-title"] = "Design v1"
    html = browser.post(CREATE, data).content.decode()
    assert f'id="required-{design}-file-error" class="field-error">Choose a file to upload.' in html
    assert 'value="Design v1"' in html and not Activity.objects.exists()


def test_a_bad_required_file_is_refused_like_any_upload(browser, post_data, settings) -> None:
    data = post_data(title="Bad design")
    data.update(required_post())
    design = doc_type("Activity Design").pk
    data[f"required-{design}-file"] = SimpleUploadedFile("design.pdf", b"renamed text file")  # checked by real content
    data[f"required-{design}-title"] = "Design v2"
    html = browser.post(CREATE, data).content.decode()
    assert f'id="required-{design}-file-error" class="field-error">The file&#x27;s content is not a valid PDF file.' in html
    assert 'value="Design v2"' in html and "Choose them again" in html
    assert not Activity.objects.exists() and not Document.objects.exists() and not stored_files()
    settings.DOCUMENT_MAX_UPLOAD_MB = 0  # the same size limit as the Documents tab
    data = post_data(title="Too big")
    data.update(required_post())
    assert "larger than 0 MB" in browser.post(CREATE, data).content.decode()
    assert not Activity.objects.exists()


def test_the_required_files_are_saved_as_approved_copies(browser, post_data, director) -> None:
    data = post_data(title="Both files")
    data.update(required_post())
    data[f"required-{doc_type('Request Letter').pk}-title"] = "Request to the Dean"
    browser.post(CREATE, data)
    activity = Activity.objects.get(title="Both files")
    documents = {d.document_type.name: d for d in activity.documents.select_related("document_type", "status")}
    assert set(documents) == {"Activity Design", "Request Letter"}
    assert documents["Request Letter"].title == "Request to the Dean"
    assert documents["Activity Design"].title == DESIGN_TITLE
    assert all(d.review_state == Document.APPROVED and d.uploaded_by == director for d in documents.values())  # 432


# --- The required titles (ASSUMPTIONS 410, NAVIGATU) ---------------------------------------------------------------


@pytest.mark.parametrize("blank", ["", "   ", "\t \n"])
@pytest.mark.parametrize("index, name", [(0, "Activity Design"), (1, "Request Letter")])
def test_a_blank_required_title_is_refused_and_nothing_is_created(director, valid_data, blank, index, name) -> None:
    rows = required_rows()
    rows[index].title = blank
    with pytest.raises(document_services.NewActivityError) as refused:
        document_services.create_activity_with_documents(director, valid_data(title="No title"), rows)
    assert refused.value.row_errors == {index: {"title": f"Enter a title for the {name}."}}
    assert not Activity.objects.exists() and not Document.objects.exists() and not stored_files()
    assert not Notification.objects.exists() and not OrmQ.objects.exists()


def test_a_required_title_has_the_normal_length_limit(director, valid_data) -> None:
    rows = required_rows()
    rows[0].title = "x" * 256
    with pytest.raises(document_services.NewActivityError) as refused:
        document_services.create_activity_with_documents(director, valid_data(), rows)
    assert refused.value.row_errors == {0: {"title": "The title can be at most 255 characters."}}
    assert not Activity.objects.exists()
    rows = required_rows()
    rows[0].title = "x" * 255
    activity = document_services.create_activity_with_documents(director, valid_data(), rows)
    assert activity.documents.get(document_type__name="Activity Design").title == "x" * 255


def test_a_valid_required_title_is_saved_as_the_document_title(director, valid_data) -> None:
    rows = required_rows()
    rows[1].title = "  Request to the Dean  "
    activity = document_services.create_activity_with_documents(director, valid_data(), rows)
    titles = dict(activity.documents.values_list("document_type__name", "title"))
    assert titles == {"Activity Design": DESIGN_TITLE, "Request Letter": "Request to the Dean"}


def test_the_page_says_the_title_is_needed_with_a_file_and_shows_an_example_placeholder(browser) -> None:
    """Since ASSUMPTIONS 417 the two rows are optional: no star, no `required`; the title rule (410) is the help text."""
    html = browser.get(CREATE).content.decode()
    for name in ("Activity Design", "Request Letter"):
        pk = doc_type(name).pk
        field = html.split(f'for="required-{pk}-title"')[1].split("</div>")[0]
        assert "Title</label>" in field and "*" not in field and " required" not in field and 'maxlength="255"' in field
        assert "Required when you choose a file." in field
        assert f'placeholder="e.g. {name} - Product Development Workshop"' in field and 'value=""' in field
        file_field = html.split(f'for="required-{pk}-file"')[1].split("</div>")[0]
        assert "File</label>" in file_field and " required " not in file_field
    assert html.count("Needed for Scheduled") == 2


def test_a_blank_title_on_the_page_shows_its_error_and_keeps_the_other_typed_title(browser, post_data) -> None:
    data = post_data(title="Blank title")
    data.update(required_post())
    design, request = doc_type("Activity Design").pk, doc_type("Request Letter").pk
    data[f"required-{design}-title"] = "   "
    data[f"required-{request}-title"] = "Request to the Dean"
    response = browser.post(CREATE, data)
    html = response.content.decode()
    assert response.status_code == 200 and not Activity.objects.exists() and not stored_files()
    assert f'id="required-{design}-title-error" class="field-error">Enter a title for the Activity Design.' in html
    assert f'id="required-{request}-title-error"' not in html
    assert 'value="Request to the Dean"' in html and "Choose them again" in html


def test_a_file_error_keeps_both_typed_titles(browser, post_data) -> None:
    data = post_data(title="Bad file")
    data.update(required_post())
    data[f"required-{doc_type('Request Letter').pk}-file"] = SimpleUploadedFile("r.pdf", b"not a pdf")
    html = browser.post(CREATE, data).content.decode()
    assert not Activity.objects.exists()
    assert f'value="{DESIGN_TITLE}"' in html and f'value="{REQUEST_TITLE}"' in html


def test_the_documents_tab_keeps_the_title_optional(director, valid_data) -> None:
    """Only the New activity form requires these titles: a later upload on the Documents tab may leave it empty."""
    activity = document_services.create_activity_with_documents(director, valid_data(), required_rows())
    document = document_services.upload_document(director, activity, doc_type("Activity Design"), pdf("v2.pdf"), "  ")
    assert document.title == "Activity Design"
