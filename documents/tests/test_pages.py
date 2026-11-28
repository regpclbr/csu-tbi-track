"""Phase F4: the Documents pages (the activity's Documents tab, the repository and review queue, the document page and
the review actions). The rules are tested with the services; these tests check that the pages call them, show their
messages, show only the allowed buttons and respect the permissions."""

import re
from datetime import date

import pytest
from django.test import Client
from django.urls import reverse

from documents import pages, services
from documents.models import Document, DocumentType
from notifications.models import Notification

from .conftest import make_file

pytestmark = pytest.mark.django_db

HTMX = {"HTTP_HX_REQUEST": "true"}


@pytest.fixture
def login():
    def log_in(member) -> Client:
        client = Client()
        client.force_login(member)
        return client

    return log_in


def messages_of(response) -> list[str]:
    return [str(message) for message in response.context["messages"]]


def tab(activity, **params) -> str:
    query = "&".join(f"{key}={value}" for key, value in {"tab": "documents", **params}.items())
    return f"{reverse('activities:detail', args=[activity.pk])}?{query}"


def action(name: str, document: Document) -> str:
    return reverse(f"documents:{name}", args=[document.pk])


# --- Documents tab ---------------------------------------------------------------------------------------------


def test_tab_shows_the_checklist_with_states_and_due_dates(director, head, login, scheduled, upload) -> None:
    waiting = upload(director, scheduled)  # Request Letter
    returned = upload(head, scheduled, document_type=DocumentType.objects.get(name="Activity Design"))
    services.return_document(director, returned, "Wrong date.")
    approved = upload(director, scheduled, document_type=DocumentType.objects.get(name="Invitation Letter"))  # optional
    services.approve_document(director, approved)
    html = login(head).get(tab(scheduled)).content.decode()
    assert 'id="documents-tab"' in html and "coming in phase" not in html
    required = html.split('id="required-title"')[1].split('id="optional-title"')[0]
    for name in ("Activity Design", "Request Letter", "Attendance Sheet"):
        assert f">{name}</h3>" in required
    for name in ("Invitation Letter", "Accomplishment Report"):  # optional since ASSUMPTIONS 386
        assert f">{name}</h3>" not in required
    assert "Due <time datetime=\"2026-11-05\">Nov 5, 2026</time>" in html  # Request Letter: start - 5
    for label in ("Waiting for review", "Returned", "Approved", "Missing"):
        assert label in html
    assert waiting.title in html and "Other documents" in html


def test_overdue_badge_after_the_due_date(director, login, make_activity) -> None:
    past = make_activity(start=date(2026, 1, 20))  # every required due date has passed and nothing is approved
    html = login(director).get(tab(past)).content.decode()
    # Activity Design and Request Letter (the Attendance Sheet counts only once Conducted, ASSUMPTIONS 425)
    # + the colour key's Overdue sample (ASSUMPTIONS 472)
    assert html.count('<span class="badge badge-danger">') == 3 and "Overdue" in html


def test_type_parameter_preselects_the_upload_type(director, login, scheduled, doc_type) -> None:
    html = login(director).get(tab(scheduled, type=doc_type.pk)).content.decode()
    assert re.search(rf'<option value="{doc_type.pk}" selected>Request Letter', html)
    assert "PDF, DOCX, XLSX, JPG, PNG" in html and "MB" in html
    certificate_type = DocumentType.objects.get(name="Certificate")
    assert f'<option value="{certificate_type.pk}"' not in html  # evidence has its own upload


def test_any_staff_member_can_upload(view_only, login, scheduled, doc_type) -> None:
    response = login(view_only).post(reverse("activities:document_upload", args=[scheduled.pk]),
                                       {"document_type": doc_type.pk, "title": "", "file": make_file()}, follow=True)
    document = Document.objects.get()
    assert document.uploaded_by == view_only and document.title == "Request Letter"
    assert response.redirect_chain[-1][0].endswith("?tab=documents")
    assert '"Request Letter" was uploaded. It is waiting for review.' in messages_of(response)


def test_upload_errors_are_shown_under_their_fields(director, login, scheduled, doc_type) -> None:
    client = login(director)
    url = reverse("activities:document_upload", args=[scheduled.pk])
    response = client.post(url, {"document_type": doc_type.pk, "file": make_file("notes.txt", b"plain text")})
    html = response.content.decode()
    assert response.status_code == 400 and not Document.objects.exists()
    assert 'id="upload-file-error"' in html and "The document was not uploaded." in html
    assert re.search(r'<input id="upload-file"[^>]*aria-invalid="true"[^>]*autofocus', html)
    assert re.search(rf'<option value="{doc_type.pk}" selected>', html)  # the chosen type is kept
    assert re.findall(r'<a href="([^"]+)"[^>]*aria-current="page"', html)[0] == reverse("activities:list")  # sidebar

    response = client.post(url, {"document_type": "", "file": make_file()})
    assert response.status_code == 400 and "Choose the document type." in response.content.decode()


# --- Review: approve and return ----------------------------------------------------------------------------------


def test_buttons_follow_allowed_actions(director, head, view_only, login, scheduled, upload) -> None:
    document = upload(head, scheduled)
    as_uploader = login(head).get(tab(scheduled)).content.decode()  # no can_approve_documents: no review buttons
    assert action("approve", document) not in as_uploader and "/return/form/" not in as_uploader
    assert "/replace/form/" in as_uploader and "/delete/form/" in as_uploader
    as_reviewer = login(director).get(tab(scheduled)).content.decode()
    assert action("approve", document) in as_reviewer and "/return/form/" in as_reviewer
    # ASSUMPTIONS 437: the Director may also replace / delete another member's waiting document
    assert reverse("documents:action_form", args=[document.pk, "delete"]) in as_reviewer
    own = upload(director, scheduled, document_type=DocumentType.objects.get(name="Activity Design"))
    as_approver_uploader = login(director).get(tab(scheduled)).content.decode()
    assert action("approve", own) in as_approver_uploader  # self-approval is allowed (ASSUMPTIONS 344)
    assert reverse("documents:action_form", args=[own.pk, "return"]) not in as_approver_uploader  # never return your own
    read_only = login(view_only).get(tab(scheduled)).content.decode()
    assert action("approve", document) not in read_only and "/return/form/" not in read_only
    assert "/replace/form/" not in read_only and 'action="' + reverse("activities:document_upload", args=[scheduled.pk]) in read_only


def test_reviewer_approves(head, director, login, scheduled, upload) -> None:
    document = upload(head, scheduled)
    response = login(director).post(action("approve", document), {"next": tab(scheduled)}, follow=True)
    document.refresh_from_db()
    assert document.review_state == Document.APPROVED and document.reviewed_by == director
    assert response.redirect_chain[-1][0] == tab(scheduled)
    assert f'"{document.title}" was approved.' in messages_of(response)


def test_returning_your_own_upload_is_refused_with_the_service_message(director, login, scheduled, upload) -> None:
    document = upload(director, scheduled)
    response = login(director).post(action("return", document), {"reason": "Oops"}, follow=True)
    document.refresh_from_db()
    assert document.review_state == Document.WAITING_FOR_REVIEW
    assert "You cannot return a document you uploaded. Replace or delete it instead." in messages_of(response)


def test_member_without_permission_cannot_approve(view_only, director, login, scheduled, upload) -> None:
    document = upload(director, scheduled)
    response = login(view_only).post(action("approve", document))
    assert response.status_code == 403
    document.refresh_from_db()
    assert document.review_state == Document.WAITING_FOR_REVIEW


def test_return_needs_a_reason_shown_in_the_modal(head, director, login, scheduled, upload) -> None:
    document = upload(head, scheduled)
    client = login(director)
    form = client.get(reverse("documents:action_form", args=[document.pk, "return"]) + "?next=/documents/", **HTMX)
    html = form.content.decode()
    assert "documents/_action_form.html" in [t.name for t in form.templates] and "layouts/app.html" not in [t.name for t in form.templates]
    assert f'maxlength="{services.RETURN_REASON_MAX_LENGTH}"' in html and 'value="/documents/"' in html

    response = client.post(action("return", document), {"reason": "  "}, **HTMX)
    assert response.status_code == 200 and "Give a reason for returning the document." in response.content.decode()
    assert 'aria-invalid="true"' in response.content.decode()
    response = client.post(action("return", document), {"reason": "x" * (services.RETURN_REASON_MAX_LENGTH + 1)})
    assert response.status_code == 400 and f"at most {services.RETURN_REASON_MAX_LENGTH} characters" in response.content.decode()
    document.refresh_from_db()
    assert document.review_state == Document.WAITING_FOR_REVIEW


def test_return_then_approving_is_refused_until_replaced(head, director, login, scheduled, upload) -> None:
    document = upload(head, scheduled)
    reviewer = login(director)
    response = reviewer.post(action("return", document), {"reason": "Please sign it.", "next": "/documents/"}, **HTMX)
    assert response["HX-Redirect"] == "/documents/"
    document.refresh_from_db()
    assert document.review_state == Document.RETURNED
    assert Notification.objects.get(member=head, type=Notification.DOCUMENT_RETURNED).message.endswith("Please sign it.")
    assert action("approve", document) not in reviewer.get(tab(scheduled)).content.decode()  # no button any more

    response = reviewer.post(action("approve", document), follow=True)
    assert "It can be reviewed after the file is replaced." in " ".join(messages_of(response))

    uploader = login(head)
    response = uploader.post(action("replace", document), {"file": make_file("signed.pdf")}, follow=True)
    document.refresh_from_db()
    assert document.review_state == Document.WAITING_FOR_REVIEW and document.file_name == "signed.pdf"
    assert "It is waiting for review again." in " ".join(messages_of(response))
    reviewer.post(action("approve", document))
    document.refresh_from_db()
    assert document.review_state == Document.APPROVED


def test_replace_errors_and_refusals(head, director, login, scheduled, upload) -> None:
    document = upload(director, scheduled)
    response = login(director).post(action("replace", document), {"file": make_file("bad.txt", b"no")}, **HTMX)
    assert response.status_code == 200 and 'id="replace-file-error"' in response.content.decode()
    response = login(head).post(action("replace", document), {"file": make_file()}, follow=True)
    assert "Only the member who uploaded this document or the Director can replace it." in messages_of(response)


def test_delete_by_the_uploader_only_and_never_approved(head, director, login, scheduled, upload) -> None:
    document = upload(director, scheduled)
    response = login(head).post(action("delete", document), follow=True)
    assert Document.objects.filter(pk=document.pk).exists()
    assert "Only the member who uploaded this document or the Director can delete it." in messages_of(response)
    response = login(director).post(action("delete", document), {"next": action("detail", document)}, follow=True)
    assert not Document.objects.filter(pk=document.pk).exists()
    assert response.redirect_chain[-1][0] == tab(scheduled)  # not the deleted document's page

    approved = upload(director, scheduled)
    services.approve_document(director, approved)
    response = login(director).post(action("delete", approved), follow=True)
    assert Document.objects.filter(pk=approved.pk).exists()
    assert "An approved document cannot be deleted. Upload a new document instead." in messages_of(response)


def test_next_must_be_a_page_of_this_site(head, director, login, scheduled, upload) -> None:
    document = upload(head, scheduled)
    response = login(director).post(action("approve", document), {"next": "https://evil.example/"})
    assert response["Location"] == action("detail", document)


def test_action_form_without_javascript_is_a_page(head, director, login, scheduled, upload) -> None:
    document = upload(head, scheduled)
    response = login(director).get(reverse("documents:action_form", args=[document.pk, "return"]))
    assert "layouts/app.html" in [t.name for t in response.templates] and 'name="reason"' in response.content.decode()


# --- Repository, review queue, document page ---------------------------------------------------------------------


def test_repository_lists_and_filters(director, head, login, scheduled, upload) -> None:
    first = upload(director, scheduled, title="Signed invitation")
    second = upload(head, scheduled, document_type=DocumentType.objects.get(name="Activity Design"), title="Design v2")
    services.approve_document(director, second)
    client = login(director)
    html = client.get(reverse("documents:list")).content.decode()  # "By activity" (ASSUMPTIONS 352)
    assert first.title in html and second.title in html and "1 activity" in html
    html = client.get(reverse("documents:list"), {"view": "all"}).content.decode()
    assert first.title in html and second.title in html and "2 documents" in html
    html = client.get(reverse("documents:list"), {"view": "all", "state": "approved"}, **HTMX).content.decode()
    assert second.title in html and first.title not in html and "<!doctype" not in html
    html = client.get(reverse("documents:list"), {"view": "all", "uploaded_by": director.pk, "q": "signed"}).content.decode()
    assert first.title in html and second.title not in html
    html = client.get(reverse("documents:list"), {"searchable": "maybe", "activity": "x"}).content.decode()
    assert "Some filters could not be read and were ignored: activity, AI search." in html


def test_repository_never_lists_certificate_evidence(director, login, scheduled, upload, certificate_for) -> None:
    certificate = certificate_for(scheduled)
    services.upload_document(director, scheduled, DocumentType.objects.get(name="Certificate"), make_file(), "Signed copy",
                             related_certificate=certificate)
    assert "Signed copy" not in login(director).get(reverse("documents:list")).content.decode()


def test_review_queue_shows_mine_to_review_with_the_count(director, head, view_only, login, scheduled, upload) -> None:
    mine = upload(director, scheduled, title="My upload")
    theirs = upload(head, scheduled, title="Their upload")
    returned = upload(head, scheduled, title="Returned one")
    services.return_document(director, returned, "Fix it.")
    html = login(director).get(reverse("documents:review")).content.decode()
    assert theirs.title in html and mine.title in html and returned.title not in html  # own uploads too (344)
    assert re.search(r'Waiting for my review\s*<span[^>]*>2<span class="sr-only"> documents', html)
    assert login(view_only).get(reverse("documents:review")).status_code == 403


def test_sidebar_always_links_to_the_repository(director, head, login, scheduled, upload) -> None:
    upload(head, scheduled)
    html = login(director).get(reverse("home")).content.decode()
    assert 'href="/documents/"' in html and 'href="/documents/review/"' not in html.split("<main")[0]


def test_document_page_shows_who_and_when(director, head, login, scheduled, upload) -> None:
    document = upload(head, scheduled)
    services.return_document(director, document, "Wrong logo.")
    html = login(head).get(action("detail", document)).content.decode()
    assert document.title in html and document.file_name in html and "Returned" in html
    assert f"Returned by {director.name}" in html and f"by {head.name}" in html
    assert reverse("documents:download", args=[document.pk]) in html and "/replace/form/" in html


def test_downloads_need_a_staff_login(director, login, scheduled, upload, client) -> None:
    document = upload(director, scheduled)
    response = client.get(reverse("documents:download", args=[document.pk]))
    assert response.status_code == 302 and "/accounts/login/" in response["Location"]
    response = login(director).get(reverse("documents:download", args=[document.pk]))
    assert response.status_code == 200 and response["Content-Disposition"].startswith("attachment")
    assert client.get(reverse("documents:list")).status_code == 302


def test_display_state_of_a_checklist_item(director, head, scheduled, upload) -> None:
    document = upload(head, scheduled)
    item = services.document_checklist(scheduled)["required"]
    request_letter = next(i for i in item if i.document_type.name == "Request Letter")
    assert pages.checklist_state(request_letter) == ("Waiting for review", "info")
    services.return_document(director, document, "No.")
    request_letter = next(i for i in services.document_checklist(scheduled)["required"] if i.document_type.name == "Request Letter")
    assert pages.checklist_state(request_letter) == ("Returned", "warning")
