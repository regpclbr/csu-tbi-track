"""Deleting an activity (ASSUMPTIONS 389, 432): the can_delete_activities flag, the refusals (Issued certificate, approved
certificate evidence, cited document, wrong title; approved documents are deleted with it), what is deleted and kept, the stored files removed after the commit,
the ACTIVITY_DELETION record, certificate numbers never given again, queued tasks for deleted certificates, the page
(menu, dialog, POST) and AI answers that point to a deleted activity or file."""

import re
from html import unescape

import pytest
from django.core.exceptions import PermissionDenied, ValidationError
from django.core.files.base import ContentFile
from django.db import transaction
from django.test import Client
from django.urls import reverse

from accounts.models import Role
from activities import deletion
from activities.models import Activity, ActivityAuditLog, ActivityDeletion, ActivityResourcePerson, ResourcePerson
from assistant.models import AIChat, AIChatMessage, AIMessageSource, DocumentEmbedding
from certificates import services
from certificates.models import ActivitySignatory, Certificate, CertificateNumberSequence, CertificateSignatory
from documents import services as document_services
from documents.models import Document, DocumentType
from documents.tests.conftest import make_file
from notifications.models import Notification
from registrations.models import EvaluationSubmission, FormAnswer, FormTemplate, Registration

pytestmark = pytest.mark.django_db
S = Certificate.Status


@pytest.fixture
def sysadmin(make_member):
    return make_member(Role.SYSTEM_ADMINISTRATOR)


def client_for(member) -> Client:
    client = Client()
    client.force_login(member)
    return client


def stored(field_file) -> bool:
    return bool(field_file.name) and field_file.storage.exists(field_file.name)


@pytest.fixture
def full(generated, director, evaluate):
    """A Conducted activity with 3 registrations (2 attended), 1 evaluation, 3 certificates (2 waiting, 1 approved not
    issued, with a PDF), 1 document (an approved copy, ASSUMPTIONS 432), 1 evidence file waiting for review, a
    notification and History rows."""
    activity, people = generated
    evaluate(people[0])
    certificates = list(Certificate.objects.filter(activity=activity).order_by("pk"))
    approved = certificates[-1]
    approved.file_path.save(f"{approved.certificate_number}.pdf", ContentFile(b"%PDF-1.7 fake"), save=False)
    Certificate.objects.filter(pk=approved.pk).update(status=S.APPROVED, file_path=approved.file_path.name)
    document = document_services.upload_document(director, activity, DocumentType.objects.get(name="Request Letter"),
                                                 make_file())
    evidence = services.upload_evidence(director, certificates[0], make_file("receipt.pdf"))
    Notification.objects.create(member=director, activity=activity, type=Notification.ACTIVITY_CREATED, message="x")
    return {"activity": activity, "people": people, "certificates": certificates, "approved": approved,
            "document": document, "evidence": evidence}


# --- The flag ----------------------------------------------------------------------------------------------------


def test_only_the_system_administrator_has_the_flag(roles) -> None:
    assert {name for name, role in roles.items() if role.can_delete_activities} == {Role.SYSTEM_ADMINISTRATOR}


def test_without_the_flag_nobody_deletes(full, director, make_member) -> None:
    activity = full["activity"]
    for member in (director, make_member("Admin Staff")):
        with pytest.raises(PermissionDenied):
            deletion.delete_activity(member, activity, activity.title)
        url = reverse("activities:delete", args=[activity.pk])
        assert client_for(member).get(url).status_code == 403
        assert client_for(member).post(url, {"title": activity.title}).status_code == 403
    assert Activity.objects.filter(pk=activity.pk).exists()


# --- Refusals ----------------------------------------------------------------------------------------------------


def test_refused_with_an_issued_certificate(full, sysadmin) -> None:
    activity = full["activity"]
    Certificate.objects.filter(pk=full["certificates"][0].pk).update(status=S.ISSUED)
    reason = deletion.deletion_refused_reason(activity)
    assert reason == "This activity can't be deleted: it has 1 issued certificate. Real records are kept."
    with pytest.raises(ValidationError, match="1 issued certificate"):
        deletion.delete_activity(sysadmin, activity, activity.title)
    assert Activity.objects.filter(pk=activity.pk).exists() and not ActivityDeletion.objects.exists()


def test_approved_documents_never_block_but_approved_evidence_does(full, sysadmin, director) -> None:
    """ASSUMPTIONS 432 (owner): approved copies are deleted with the activity; certificate evidence keeps its approval."""
    activity = full["activity"]
    assert Document.objects.get(pk=full["document"].pk).status.status_name == "Approved"  # an approved copy
    assert deletion.deletion_refused_reason(activity) is None
    document_services.approve_document(director, full["evidence"])
    assert deletion.deletion_refused_reason(activity) == (
        "This activity can't be deleted: it has 1 approved certificate evidence file. Real records are kept.")
    Certificate.objects.filter(pk=full["certificates"][0].pk).update(status=S.ISSUED)
    assert "1 issued certificate and 1 approved certificate evidence file" in deletion.deletion_refused_reason(activity)
    with pytest.raises(ValidationError):
        deletion.delete_activity(sysadmin, activity, activity.title)
    assert stored(Document.objects.get(pk=full["document"].pk).file_path)


def test_approved_copies_are_deleted_with_their_files_and_counted(make_member, sysadmin, valid_data,
                                                                  django_capture_on_commit_callbacks) -> None:
    from activities import services as activity_services

    director = make_member("Director", email="d3@example.com")
    activity = activity_services.create_activity(director, valid_data(title="Made by mistake"))
    documents = [document_services.upload_document(director, activity, DocumentType.objects.get(name=name), make_file(),
                                                   name) for name in ("Activity Design", "Request Letter")]
    files = [d.file_path for d in documents]
    summary = deletion.deletion_summary(activity)
    assert (summary.documents, summary.documents_approved, summary.document_files) == (2, 2, 2)
    assert "2 documents (approved) and their 2 files" in deletion.what_will_be_deleted(summary)
    with django_capture_on_commit_callbacks(execute=True):
        record = deletion.delete_activity(sysadmin, activity, "Made by mistake")
    assert not Document.objects.filter(pk__in=[d.pk for d in documents]).exists()
    assert not any(f.storage.exists(f.name) for f in files)
    assert (record.counts["documents"], record.counts["documents_approved"], record.counts["document_files"]) == (2, 2, 2)


def test_refused_with_a_document_an_ai_answer_cites(full, sysadmin) -> None:
    activity = full["activity"]
    chat = AIChat.objects.create(member=sysadmin, title="Q")
    message = AIChatMessage.objects.create(chat=chat, role=AIChatMessage.Role.ASSISTANT, content="It says [1].")
    AIMessageSource.objects.create(message=message, document=full["document"], chunk_index=0)
    assert "1 document cited by the AI Assistant" in deletion.deletion_refused_reason(activity)


def test_the_typed_title_must_match(full, sysadmin) -> None:
    activity = full["activity"]
    for typed in ("", "wrong", activity.title.upper() + "x"):
        with pytest.raises(ValidationError) as refused:
            deletion.delete_activity(sysadmin, activity, typed)
        assert refused.value.message_dict == {"title": [deletion.TITLE_MISMATCH]}
    assert Activity.objects.filter(pk=activity.pk).exists()


# --- A successful delete -----------------------------------------------------------------------------------------


def test_delete_removes_every_record_and_file_and_keeps_a_record(full, sysadmin, design,
                                                                  django_capture_on_commit_callbacks) -> None:
    activity, approved = full["activity"], full["approved"]
    activity_id, title = activity.pk, activity.title
    files = [Document.objects.get(pk=full["document"].pk).file_path, Document.objects.get(pk=full["evidence"].pk).file_path,
             Certificate.objects.get(pk=approved.pk).file_path]
    assert all(stored(f) for f in files)
    numbers = sorted(Certificate.objects.filter(activity=activity).values_list("certificate_number", flat=True))
    speaker = ResourcePerson.objects.get(activity_links__activity=activity)
    history = ActivityAuditLog.objects.filter(activity=activity).count()
    assert history > 0

    with django_capture_on_commit_callbacks(execute=True):
        record = deletion.delete_activity(sysadmin, activity, f"  {title}  ")  # outer spaces ignored

    assert not Activity.objects.filter(pk=activity_id).exists()
    for model, lookup in ((Registration, "activity_id"), (Certificate, "activity_id"), (Document, "activity_id"),
                          (FormTemplate, "activity_id"), (Notification, "activity_id"), (ActivitySignatory, "activity_id"),
                          (ActivityResourcePerson, "activity_id"), (ActivityAuditLog, "activity_id"),
                          (EvaluationSubmission, "registration__activity_id")):
        assert not model.objects.filter(**{lookup: activity_id}).exists(), model.__name__
    assert not FormAnswer.objects.exists() and not DocumentEmbedding.objects.exists()
    assert not any(f.storage.exists(f.name) for f in files)  # removed from media after the commit
    # Kept: the shared lists.
    assert ResourcePerson.objects.filter(pk=speaker.pk).exists()
    assert CertificateSignatory.objects.filter(pk__in=[design[1].pk, design[2].pk]).count() == 2
    # The record survives the activity.
    record.refresh_from_db()
    assert (record.deleted_activity_id, record.activity_title, record.deleted_by) == (activity_id, title, sysadmin)
    expected = {"registrations": 3, "attended": 2, "evaluations": 1, "certificates_waiting": 2,
                "certificates_approved": 1, "documents": 1, "documents_approved": 1, "document_files": 1,
                "evidence": 1, "files": 3, "history": history}
    assert {key: record.counts[key] for key in expected} == expected
    assert record.counts["forms"] >= 1 and record.counts["notifications"] >= 1
    assert record.certificate_numbers == ", ".join(numbers)


def test_a_missing_file_does_not_stop_the_delete(full, sysadmin, django_capture_on_commit_callbacks, caplog) -> None:
    document = Document.objects.get(pk=full["document"].pk)
    document.file_path.storage.delete(document.file_path.name)  # already gone
    with django_capture_on_commit_callbacks(execute=True), caplog.at_level("WARNING", logger="activities"):
        deletion.delete_activity(sysadmin, full["activity"], full["activity"].title)
    assert "was already missing" in caplog.text


def test_a_failed_delete_keeps_every_file(full, sysadmin, monkeypatch, django_capture_on_commit_callbacks) -> None:
    """The files are removed only after the commit: a delete that fails half-way loses nothing."""
    document = Document.objects.get(pk=full["document"].pk)

    def explode(activity_id: int) -> int:
        raise RuntimeError("database trouble")

    monkeypatch.setattr(ActivityAuditLog.objects, "delete_for_deleted_activity", explode)
    with django_capture_on_commit_callbacks(execute=True), pytest.raises(RuntimeError):
        deletion.delete_activity(sysadmin, full["activity"], full["activity"].title)
    assert stored(document.file_path) and Activity.objects.filter(pk=full["activity"].pk).exists()


def test_the_audit_log_line_has_ids_and_counts(full, sysadmin, caplog) -> None:
    with caplog.at_level("INFO", logger="activities.audit"):
        record = deletion.delete_activity(sysadmin, full["activity"], full["activity"].title)
    assert f"Activity deleted: activity_id={record.deleted_activity_id}" in caplog.text
    assert f"by member_id={sysadmin.pk}" in caplog.text


# --- Certificate numbers are never given again ---------------------------------------------------------------------


def next_number() -> str:
    with transaction.atomic():
        return next(services._certificate_numbers())


def test_deleted_numbers_are_skipped(full, sysadmin) -> None:
    highest = max(Certificate.objects.values_list("certificate_number", flat=True))
    deletion.delete_activity(sysadmin, full["activity"], full["activity"].title)
    assert not Certificate.objects.exists()
    following = next_number()
    assert int(following.rsplit("-", 1)[1]) == int(highest.rsplit("-", 1)[1]) + 1


def test_a_certificate_deleted_by_undoing_attendance_is_skipped_too(generated) -> None:
    """The old rule (highest existing + 1) gave a deleted newest number again; the sequence never does."""
    newest = Certificate.objects.order_by("-certificate_number").first()
    number = newest.certificate_number
    Certificate.objects.filter(pk=newest.pk).delete()
    assert next_number() != number
    year = int(number.split("-")[1])
    assert CertificateNumberSequence.objects.get(year=year).last_number > int(number.rsplit("-", 1)[1])


def test_queued_tasks_for_a_deleted_certificate_end_quietly(full, sysadmin) -> None:
    ids = [c.pk for c in full["certificates"]]
    deletion.delete_activity(sysadmin, full["activity"], full["activity"].title)
    for certificate_id in ids:
        assert services.prepare_certificate(certificate_id) == services.CERTIFICATE_GONE
        assert services.send_certificate(certificate_id) == services.CERTIFICATE_GONE


# --- The page --------------------------------------------------------------------------------------------------------


def test_the_menu_is_only_for_the_flag(full, sysadmin, director) -> None:
    url = reverse("activities:detail", args=[full["activity"].pk])
    delete_url = reverse("activities:delete", args=[full["activity"].pk])
    html = client_for(sysadmin).get(url).content.decode()
    assert 'aria-label="More actions for this activity"' in html and delete_url in html and "Delete activity…" in html
    assert 'id="delete-dialog"' in html
    html = client_for(director).get(url).content.decode()
    assert delete_url not in html and "More actions for this activity" not in html


def test_the_dialog_lists_what_goes_with_it(full, sysadmin) -> None:
    html = client_for(sysadmin).get(reverse("activities:delete", args=[full["activity"].pk]),
                                   HTTP_HX_REQUEST="true").content.decode()
    lines = [unescape(line) for line in re.findall(r"<li>([^<]+)</li>", html.split("data-delete-lines")[1].split("</ul>")[0])]
    assert "3 registrations (2 attended)" in lines and "1 evaluation" in lines
    assert "2 certificates waiting for approval" in lines
    assert "1 approved certificate not yet issued (resource persons' copies may already be printed)" in lines
    assert "1 document (approved) and its 1 file" in lines and "1 certificate evidence file" in lines
    assert "3 stored files removed from the server" in lines
    assert 'name="title"' in html and "Type the activity's title to confirm" in html


def test_a_refused_activity_shows_only_the_reason(full, sysadmin) -> None:
    Certificate.objects.filter(pk=full["certificates"][0].pk).update(status=S.ISSUED)
    html = client_for(sysadmin).get(reverse("activities:delete", args=[full["activity"].pk])).content.decode()
    assert "it has 1 issued certificate. Real records are kept." in html and 'name="title"' not in html


def test_posting_deletes_and_goes_to_the_list(full, sysadmin) -> None:
    activity = full["activity"]
    client = client_for(sysadmin)
    wrong = client.post(reverse("activities:delete", args=[activity.pk]), {"title": "nope"})
    assert wrong.status_code == 400 and deletion.TITLE_MISMATCH in unescape(wrong.content.decode())
    response = client.post(reverse("activities:delete", args=[activity.pk]), {"title": activity.title}, follow=True)
    assert response.redirect_chain[-1][0] == reverse("activities:list")
    assert f"“{activity.title}” was deleted." in " ".join(str(m) for m in response.context["messages"])
    assert client.get(reverse("activities:detail", args=[activity.pk])).status_code == 404


def test_the_admin_console_lists_deletions_read_only(full, sysadmin, director) -> None:
    record = deletion.delete_activity(sysadmin, full["activity"], full["activity"].title)
    url = reverse("accounts:console_deleted_activities")
    html = client_for(sysadmin).get(url).content.decode()
    assert record.activity_title in html and "3 registrations" in html and record.certificate_numbers in html
    assert "<form" not in html.split('id="deleted-title"')[1].split("</section>")[0]  # read only
    assert client_for(director).get(url).status_code == 403


# --- AI answers that point to deleted rows ---------------------------------------------------------------------------


def test_ai_answers_say_no_longer_available(full, sysadmin) -> None:
    from assistant import pages as assistant_pages

    activity, document = full["activity"], full["document"]
    chat = AIChat.objects.create(member=sysadmin, title="Files")
    message = AIChatMessage.objects.create(
        chat=chat, role=AIChatMessage.Role.ASSISTANT,
        content=f"See [activity:{activity.pk}].\n\n- **{activity.title}** [activity:{activity.pk}]\n- Letter [file:{document.pk}]",
    )
    deletion.delete_activity(sysadmin, activity, activity.title)
    message.refresh_from_db()
    view = assistant_pages.message_view(message)
    html = str(assistant_pages.answer_html(message.content, {}, assistant_pages.lookup_for([message])))
    assert assistant_pages.ACTIVITY_GONE in html and "href" not in html
    group = view["file_groups"][0]
    assert group["activity"] == {"missing": True} and group["cards"][0]["missing"] is True
    page = client_for(sysadmin).get(reverse("assistant:conversation", args=[chat.pk])).content.decode()
    assert "Activity no longer available" in page and "no longer available" in page
