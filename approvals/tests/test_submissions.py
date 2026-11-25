"""Submit to the Director (ASSUMPTIONS 437): submit, approve / return / resubmit, sign (PDF only, the original kept),
unapprove (and what it undoes), save to the activity's documents, separation of duties, history and notifications."""

from io import BytesIO

import pytest
from django.core.exceptions import PermissionDenied, ValidationError
from django.core.files.storage import default_storage
from pypdf import PdfReader

from activities.models import ActivityAuditLog
from approvals import services, signing
from approvals.models import Submission, SubmissionEvent, SubmissionEventIsAppendOnly
from documents.models import Document, DocumentType
from notifications.models import Notification

from .conftest import NOW, make_pdf, pdf_file

pytestmark = pytest.mark.django_db

S = Submission.Status


def actions(submission) -> list[str]:
    return list(submission.events.order_by("pk").values_list("action", flat=True))


# --- Submitting ----------------------------------------------------------------------------------------------------


def test_submit_stores_the_file_and_tells_every_other_approver(submit, staff, director, make_member) -> None:
    second = make_member("Director", email="second.director@example.com")
    submission = submit(staff, note="Please sign")
    assert submission.status == S.SUBMITTED and submission.file_extension == "pdf"
    assert submission.file_path.name.startswith("submissions/2026/submission-") and "Letter" not in submission.file_path.name
    told = set(Notification.objects.filter(type=Notification.SUBMISSION_WAITING).values_list("member", flat=True))
    assert told == {director.pk, second.pk}
    assert actions(submission) == ["submitted"]


def test_the_file_checks_are_the_documents_ones(submit, staff) -> None:
    with pytest.raises(ValidationError) as error:
        services.submit(staff, pdf_file("fake.pdf", b"not a pdf"), "", "")
    assert set(error.value.message_dict) == {"file", "title"}


def test_refused_when_nobody_else_can_approve(director) -> None:
    with pytest.raises(services.SubmissionError, match="Nobody else"):
        services.submit(director, pdf_file(), "My own letter")  # the only approver submits


def test_generated_files_can_be_submitted_too(director, staff) -> None:
    submission = services.submit_generated(staff, make_pdf(), "report.pdf", "Activity Report")
    assert submission.file_extension == "pdf" and submission.file_name == "report.pdf"


# --- Approve, return, resubmit -----------------------------------------------------------------------------------


def test_approve_tells_the_submitter(submit, staff, director) -> None:
    submission = services.approve_submission(director, submit(staff), now=NOW)
    assert submission.status == S.APPROVED and submission.reviewed_by == director
    assert Notification.objects.get(type=Notification.SUBMISSION_APPROVED).member == staff
    assert actions(submission) == ["submitted", "approved"]


def test_nobody_approves_or_returns_their_own_submission(submit, director, make_member) -> None:
    make_member("Director", email="second.director@example.com")
    own = submit(director)
    with pytest.raises(services.SubmissionError, match="your own"):
        services.approve_submission(director, own)
    with pytest.raises(services.SubmissionError, match="your own"):
        services.return_submission(director, own, "No")
    assert not services.allowed_actions(director, own)["approve"]


def test_only_approvers_approve(submit, staff, head) -> None:
    submission = submit(staff)
    with pytest.raises(PermissionDenied):
        services.approve_submission(head, submission)


def test_return_needs_a_reason_and_the_submitter_resubmits(submit, staff, director) -> None:
    submission = submit(staff)
    with pytest.raises(ValidationError):
        services.return_submission(director, submission, " ")
    submission = services.return_submission(director, submission, "Add the budget")
    assert submission.status == S.RETURNED and submission.review_reason == "Add the budget"
    assert "Add the budget" in Notification.objects.get(type=Notification.SUBMISSION_RETURNED).message
    old_name = submission.file_path.name
    with pytest.raises(PermissionDenied):
        services.resubmit(director, submission, pdf_file("v2.pdf"))
    again = services.resubmit(staff, submission, pdf_file("v2.pdf"), "Budget added")
    assert again.status == S.SUBMITTED and again.reviewed_by is None and again.file_name == "v2.pdf"
    assert again.file_path.name != old_name
    assert actions(again) == ["submitted", "returned", "resubmitted"]


def test_history_is_append_only(submit, staff) -> None:
    event = submit(staff).events.first()
    with pytest.raises(SubmissionEventIsAppendOnly):
        event.save()
    with pytest.raises(SubmissionEventIsAppendOnly):
        SubmissionEvent.objects.all().delete()


# --- Signing ------------------------------------------------------------------------------------------------------


def test_signing_stamps_a_copy_and_keeps_the_original(submit, staff, director, director_signature) -> None:
    original = make_pdf()
    submission = submit(staff, file=pdf_file(content=original))
    approved = services.approve_submission(director, submission, sign=True,
                                           placement=signing.Placement(signing.NOTED), now=NOW)
    with approved.file_path.open("rb") as handle:
        assert handle.read() == original  # the original never changes
    with approved.signed_file_path.open("rb") as handle:
        signed = handle.read()
    assert signed != original
    page = PdfReader(BytesIO(signed)).pages[0]
    assert "/TBISignature" in page["/Resources"]["/XObject"] and "Stamped in CSU-TBI Track" in page.extract_text()
    assert approved.signed_by == director and approved.signatory == director_signature
    assert actions(approved) == ["submitted", "approved", "signed"]


def test_noted_is_found_and_a_place_can_be_chosen(director_signature) -> None:
    data = make_pdf(pages=2)
    assert signing.noted_page(data) == 2  # the last one
    plain = make_pdf([("No line here", 400)])
    assert signing.noted_page(plain) is None
    image = director_signature.signature_image_path.open("rb").read()
    stamped = signing.stamp(plain, image, signing.Placement("position", 1, "bottom-right"), NOW.date())
    assert "Stamped in CSU-TBI Track" in PdfReader(BytesIO(stamped)).pages[0].extract_text()
    with pytest.raises(signing.SigningError, match="Noted"):
        signing.stamp(plain, director_signature.signature_image_path.open("rb").read(), signing.Placement(signing.NOTED),
                      NOW.date())
    with pytest.raises(signing.SigningError, match="page between 1 and 1"):
        signing.stamp(plain, director_signature.signature_image_path.open("rb").read(),
                      signing.Placement("position", 3, "bottom-right"), NOW.date())


def test_only_pdfs_are_signed(submit, staff, director, director_signature) -> None:
    from documents.tests.conftest import office_bytes

    docx = submit(staff, file=pdf_file("letter.docx", office_bytes("docx")))
    assert services.sign_refused_reason(director, docx) == services.SIGN_PDF_ONLY
    with pytest.raises(services.SubmissionError, match="Only PDF"):
        services.approve_submission(director, docx, sign=True)
    assert Submission.objects.get(pk=docx.pk).status == S.SUBMITTED  # nothing approved half-signed


def test_signing_needs_your_own_linked_signature(submit, staff, director, make_member) -> None:
    submission = submit(staff)
    assert services.sign_refused_reason(director, submission) == services.SIGN_NO_SIGNATORY
    with pytest.raises(services.SubmissionError, match="not linked"):
        services.approve_submission(director, submission, sign=True)


def test_an_encrypted_pdf_is_not_signed(submit, staff, director, director_signature) -> None:
    from pypdf import PdfWriter

    writer = PdfWriter(clone_from=PdfReader(BytesIO(make_pdf())))
    writer.encrypt("secret")
    buffer = BytesIO()
    writer.write(buffer)
    submission = submit(staff, file=pdf_file(content=buffer.getvalue()))
    with pytest.raises(services.SubmissionError, match="password"):
        services.approve_submission(director, submission, sign=True)
    assert services.approve_submission(director, submission).status == S.APPROVED  # approved without signing


def test_one_account_per_signatory_and_new_versions_keep_the_link(director, director_signature, make_member) -> None:
    from certificates import services as certificate_services
    from certificates.models import CertificateSignatory

    other = CertificateSignatory.objects.create(name="Someone", position="VP")
    with pytest.raises(certificate_services.CertificateError, match="already linked"):
        certificate_services.link_signatory_account(director, other, director)
    certificate_services.edit_signatory(director, director_signature, "Dr. Director", "Director, TBI")
    assert certificate_services.signatory_for(director).position == "Director, TBI"
    staff = make_member("Admin Staff", email="no.sign@example.com")
    with pytest.raises(PermissionDenied):
        certificate_services.link_signatory_account(staff, other, staff)


# --- Unapprove ----------------------------------------------------------------------------------------------------


def test_unapprove_returns_it_and_deletes_the_signed_copy(submit, staff, director, director_signature,
                                                           django_capture_on_commit_callbacks) -> None:
    submission = services.approve_submission(director, submit(staff), sign=True, now=NOW)
    signed_name = submission.signed_file_path.name
    with django_capture_on_commit_callbacks(execute=True):
        submission = services.unapprove_submission(director, submission, "Wrong addressee")
    assert submission.status == S.RETURNED and submission.review_reason == "Wrong addressee"
    assert not submission.signed_file_path and submission.signed_by is None
    assert not default_storage.exists(signed_name) and default_storage.exists(submission.file_path.name)
    assert "Wrong addressee" in Notification.objects.get(type=Notification.SUBMISSION_UNAPPROVED).message
    assert actions(submission)[-1] == "unapproved"


def test_unapprove_needs_a_reason_and_an_approved_submission(submit, staff, director) -> None:
    submission = submit(staff)
    with pytest.raises(services.SubmissionError, match="Only an approved"):
        services.unapprove_submission(director, submission, "x")
    approved = services.approve_submission(director, submission)
    with pytest.raises(ValidationError):
        services.unapprove_submission(director, approved, "")


# --- Save to the activity's documents ---------------------------------------------------------------------------------


@pytest.fixture
def letter_type(roles) -> DocumentType:
    return DocumentType.objects.get(name="Invitation Letter")


def test_save_to_documents_makes_an_approved_document_with_history(submit, staff, director, director_signature,
                                                                    activity, letter_type) -> None:
    submission = services.approve_submission(director, submit(staff, activity=activity, document_type=letter_type),
                                             sign=True, now=NOW)
    document = services.save_to_documents(staff, submission)
    assert document.status.status_name == "Approved" and document.activity == activity
    assert document.file_name == "Letter-signed.pdf"  # the signed copy
    assert ActivityAuditLog.objects.filter(activity=activity, field_changed="Documents",
                                           new_value__contains="saved from an approved submission").exists()
    with pytest.raises(services.SubmissionError, match="already saved"):
        services.save_to_documents(staff, Submission.objects.get(pk=submission.pk))


def test_a_reviewed_type_is_approved_in_the_reviewers_name(submit, staff, director, activity, letter_type) -> None:
    DocumentType.objects.filter(pk=letter_type.pk).update(needs_review=True)
    letter_type.refresh_from_db()
    submission = services.approve_submission(director, submit(staff, activity=activity), now=NOW)
    document = services.save_to_documents(director, submission, letter_type)
    assert document.status.status_name == "Approved" and document.reviewed_by == director
    assert not Notification.objects.filter(type=Notification.DOCUMENT_WAITING_FOR_REVIEW).exists()


def test_unapprove_deletes_the_saved_document(submit, staff, director, activity, letter_type,
                                              django_capture_on_commit_callbacks) -> None:
    submission = services.approve_submission(director, submit(staff, activity=activity, document_type=letter_type))
    document = services.save_to_documents(staff, submission)
    with django_capture_on_commit_callbacks(execute=True):
        services.unapprove_submission(director, Submission.objects.get(pk=submission.pk), "Redo")
    assert not Document.objects.filter(pk=document.pk).exists()
    assert ActivityAuditLog.objects.filter(activity=activity, new_value__contains="its submission was unapproved").exists()


def test_unapprove_is_refused_when_the_saved_document_must_stay(submit, staff, director, activity) -> None:
    activity.creation_documents_required = True
    activity.save(update_fields=["creation_documents_required"])
    design = DocumentType.objects.get(name="Activity Design")
    submission = services.approve_submission(director, submit(staff, activity=activity, document_type=design))
    services.save_to_documents(staff, submission)
    with pytest.raises(services.SubmissionError, match="only Activity Design"):
        services.unapprove_submission(director, Submission.objects.get(pk=submission.pk), "Redo")
    assert Submission.objects.get(pk=submission.pk).status == S.APPROVED  # nothing changed


def test_save_needs_an_activity_and_an_approval(submit, staff, director, letter_type) -> None:
    submission = submit(staff)
    with pytest.raises(services.SubmissionError, match="Only an approved"):
        services.save_to_documents(staff, submission, letter_type)
    approved = services.approve_submission(director, submission)
    with pytest.raises(services.SubmissionError, match="not linked to an activity"):
        services.save_to_documents(staff, approved, letter_type)
