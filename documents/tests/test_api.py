"""JSON API: upload, repository (filters, search, pages, review state), detail, review queue,
approve/return/replace/delete, checklist. Date-times are shown in Manila time (+08:00)."""

import pytest
from django.urls import reverse

from documents import services
from documents.models import Document, DocumentStatus, DocumentType

from .conftest import PNG_BYTES, make_file

pytestmark = pytest.mark.django_db

REPOSITORY = "/documents/api/"
QUEUE = "/documents/api/review-queue/"


def api(name: str, *args) -> str:
    return reverse(f"documents:{name}", args=args)


# --- Upload -------------------------------------------------------------------------------------


def test_upload_through_the_api(client, view_only, scheduled, doc_type) -> None:
    client.force_login(view_only)
    response = client.post(
        api("api_upload", scheduled.pk), {"document_type": doc_type.pk, "title": "Signed", "file": make_file()}
    )
    assert response.status_code == 201
    data = response.json()["document"]
    assert data["status"] == "For Review" and data["review_state"] == "waiting_for_review"
    assert data["title"] == "Signed" and data["file_name"] == "Invitation.pdf"
    assert data["uploaded_at"].endswith("+08:00")  # Manila time
    assert data["allowed_actions"] == {"approve": False, "return": False, "replace": True, "delete": True}
    assert data["download_url"] == api("download", data["document_id"])


def test_upload_errors_are_400(client, director, scheduled, doc_type) -> None:
    client.force_login(director)
    fake = client.post(api("api_upload", scheduled.pk), {"document_type": doc_type.pk, "file": make_file("x.pdf", b"nope")})
    assert fake.status_code == 400 and "file" in fake.json()["errors"]
    no_type = client.post(api("api_upload", scheduled.pk), {"file": make_file()})
    assert no_type.status_code == 400 and "document_type" in no_type.json()["errors"]
    assert not Document.objects.exists()


def test_api_requires_login(client, scheduled) -> None:
    assert client.get(REPOSITORY).status_code == 401
    assert client.post(api("api_upload", scheduled.pk)).status_code == 401


# --- Review actions -------------------------------------------------------------------------------


def test_approve_return_replace_delete_through_the_api(client, upload, director, view_only, scheduled) -> None:
    document = upload(view_only, scheduled)

    client.force_login(director)
    returned = client.post(api("api_return", document.pk), {"reason": "Unsigned"}, content_type="application/json")
    assert returned.status_code == 200 and returned.json()["document"]["review_state"] == "returned"
    refused = client.post(api("api_approve", document.pk))
    assert refused.status_code == 400 and "returned" in refused.json()["error"]

    client.force_login(view_only)
    replaced = client.post(api("api_replace", document.pk), {"file": make_file("scan.png", PNG_BYTES)})
    assert replaced.status_code == 200 and replaced.json()["document"]["review_state"] == "waiting_for_review"
    assert client.post(api("api_approve", document.pk)).status_code == 403  # Admin Staff cannot review

    client.force_login(director)
    approved = client.post(api("api_approve", document.pk))
    assert approved.status_code == 200
    body = approved.json()["document"]
    assert body["status"] == "Approved" and body["reviewed_by"]["member_id"] == director.pk
    assert body["reviewed_at"].endswith("+08:00")

    client.force_login(view_only)
    assert client.post(api("api_delete", document.pk)).status_code == 400  # approved: never deleted
    assert client.post(api("api_replace", document.pk), {"file": make_file()}).status_code == 400


def test_self_approval_through_the_api_is_allowed(client, upload, director, scheduled) -> None:
    """ASSUMPTIONS 344: an approver may approve their own upload; returning it is refused (400)."""
    document = upload(director, scheduled)
    client.force_login(director)
    response = client.post(api("api_return", document.pk), {"reason": "x"}, content_type="application/json")
    assert response.status_code == 400 and "Replace or delete it instead" in response.json()["error"]
    response = client.post(api("api_approve", document.pk))
    assert response.status_code == 200 and response.json()["document"]["reviewed_by"]["member_id"] == director.pk


def test_return_without_reason_is_400(client, upload, director, view_only, scheduled) -> None:
    document = upload(view_only, scheduled)
    client.force_login(director)
    response = client.post(api("api_return", document.pk), {}, content_type="application/json")
    assert response.status_code == 400 and "reason" in response.json()["errors"]


def test_delete_through_the_api(client, upload, view_only, scheduled) -> None:
    document = upload(view_only, scheduled)
    client.force_login(view_only)
    assert client.post(api("api_delete", document.pk)).json() == {"deleted": document.pk}
    assert client.get(api("api_detail", document.pk)).status_code == 404


# --- Review queue -------------------------------------------------------------------------------------


def test_review_queue_api(client, upload, director, head, view_only, scheduled) -> None:
    waiting = upload(view_only, scheduled)
    returned = upload(view_only, scheduled)
    mine = upload(director, scheduled)  # an approver's own upload is in their queue (ASSUMPTIONS 344)
    services.return_document(director, returned, "Fix")

    client.force_login(director)
    ids = [row["document_id"] for row in client.get(QUEUE).json()["documents"]]
    assert ids == [waiting.pk, mine.pk]

    client.force_login(view_only)
    assert client.get(QUEUE).status_code == 403


# --- Repository ------------------------------------------------------------------------------------------


@pytest.fixture
def library(upload, director, head, view_only, make_activity):
    """Several documents across two activities."""
    first, second = make_activity(title="First"), make_activity(title="Second")
    design = DocumentType.objects.get(name="Activity Design")
    docs = {
        "invite": upload(director, first, title="Invite for first"),
        "design": upload(view_only, first, make_file("Budget Plan.pdf"), document_type=design),
        "other": upload(head, second, title="Invite for second"),
    }
    services.approve_document(director, docs["invite"])
    services.return_document(director, docs["other"], "Fix")
    return first, second, design, docs


def ids(response) -> set[int]:
    return {row["document_id"] for row in response.json()["documents"]}


def test_repository_filters_and_search(client, view_only, library) -> None:
    first, second, design, docs = library
    approved = DocumentStatus.objects.get(status_name="Approved")
    client.force_login(view_only)
    assert ids(client.get(REPOSITORY)) == {d.pk for d in docs.values()}
    assert ids(client.get(REPOSITORY, {"activity": first.pk})) == {docs["invite"].pk, docs["design"].pk}
    assert ids(client.get(REPOSITORY, {"document_type": design.pk})) == {docs["design"].pk}
    assert ids(client.get(REPOSITORY, {"status": approved.pk})) == {docs["invite"].pk}
    assert ids(client.get(REPOSITORY, {"uploaded_by": view_only.pk})) == {docs["design"].pk}
    assert ids(client.get(REPOSITORY, {"q": "budget"})) == {docs["design"].pk}  # file name
    assert ids(client.get(REPOSITORY, {"q": "SECOND"})) == {docs["other"].pk}  # title
    assert ids(client.get(REPOSITORY, {"review_state": "returned"})) == {docs["other"].pk}
    assert ids(client.get(REPOSITORY, {"review_state": "waiting_for_review"})) == {docs["design"].pk}


def test_repository_shows_review_state(client, view_only, library) -> None:
    *_, docs = library
    client.force_login(view_only)
    states = {row["document_id"]: row["review_state"] for row in client.get(REPOSITORY).json()["documents"]}
    assert states == {docs["invite"].pk: "approved", docs["design"].pk: "waiting_for_review", docs["other"].pk: "returned"}


def test_repository_bad_filters_are_400(client, view_only) -> None:
    client.force_login(view_only)
    assert client.get(REPOSITORY, {"activity": "abc"}).status_code == 400
    assert client.get(REPOSITORY, {"review_state": "rejected"}).status_code == 400


def test_repository_is_paginated(client, upload, view_only, scheduled) -> None:
    for _ in range(services.DEFAULT_PAGE_SIZE + 1):
        upload(view_only, scheduled)
    client.force_login(view_only)
    page_two = client.get(REPOSITORY, {"page": 2}).json()
    assert page_two["total"] == 21 and page_two["num_pages"] == 2 and len(page_two["documents"]) == 1


def test_repository_excludes_certificate_files(client, upload, director, view_only, scheduled, certificate_for) -> None:
    document = upload(director, scheduled)
    Document.objects.filter(pk=document.pk).update(related_certificate=certificate_for(scheduled))
    client.force_login(view_only)
    assert client.get(REPOSITORY).json()["total"] == 0
    assert client.get(api("api_detail", document.pk)).status_code == 404


# --- Detail and checklist ----------------------------------------------------------------------------------


def test_detail_shows_returned_state(client, upload, director, view_only, scheduled) -> None:
    document = upload(view_only, scheduled)
    services.return_document(director, document, "Fix")
    client.force_login(view_only)
    data = client.get(api("api_detail", document.pk)).json()["document"]
    assert data["review_state"] == "returned" and data["status"] == "For Review"
    assert data["reviewed_by"] == {"member_id": director.pk, "name": director.name}


def test_checklist_api(client, view_only, make_activity) -> None:
    from datetime import date

    activity = make_activity(start=date(2026, 11, 10))
    client.force_login(view_only)
    data = client.get(api("api_checklist", activity.pk)).json()
    request_letter = next(row for row in data["required"] if row["document_type"] == "Request Letter")
    assert request_letter["due_date"] == "2026-11-05"
    assert request_letter["state"] == "Missing" and request_letter["documents"] == []
    assert isinstance(request_letter["is_overdue"], bool)
    assert {row["document_type"] for row in data["optional"]} == {
        "Accomplishment Report", "Invitation Letter", "Pre-registration Form", "Evaluation Form", "Certificate", "Other"}
