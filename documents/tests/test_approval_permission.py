"""Only roles with can_approve_documents (the seeded Director) approve or return documents (ASSUMPTIONS 344).

The service rules are in test_review.py; these tests check the 403s on every review URL, what members without the
flag no longer see, and the "Document waiting for your review" notifications."""

import json

import pytest
from django.test import Client
from django.urls import reverse

from documents import services
from documents.models import DocumentStatus
from notifications.models import Notification

from .conftest import make_file

pytestmark = pytest.mark.django_db

HTMX = {"HTTP_HX_REQUEST": "true"}
WAITING = Notification.DOCUMENT_WAITING_FOR_REVIEW


@pytest.fixture
def login():
    def log_in(member) -> Client:
        client = Client()
        client.force_login(member)
        return client

    return log_in


def waiting_for(member) -> list[Notification]:
    return list(Notification.objects.filter(type=WAITING, member=member))


# --- 403 on every review URL ----------------------------------------------------------------------------------


@pytest.mark.parametrize("role", ["head", "view_only", "system_admin"])
def test_approve_and_return_pages_answer_403_without_the_flag(request, role, login, director, scheduled, upload) -> None:
    document = upload(director, scheduled)
    client = login(request.getfixturevalue(role))
    assert client.post(reverse("documents:approve", args=[document.pk])).status_code == 403
    assert client.post(reverse("documents:return", args=[document.pk]), {"reason": "No"}).status_code == 403
    assert client.post(reverse("documents:approve", args=[document.pk]), **HTMX).status_code == 403
    document.refresh_from_db()
    assert document.status.status_name == DocumentStatus.FOR_REVIEW and document.reviewed_at is None


@pytest.mark.parametrize("role", ["head", "view_only", "system_admin"])
def test_approve_and_return_api_answer_403_without_the_flag(request, role, login, director, scheduled, upload) -> None:
    document = upload(director, scheduled)
    client = login(request.getfixturevalue(role))
    response = client.post(reverse("documents:api_approve", args=[document.pk]))
    assert response.status_code == 403 and "approve or return documents" in response.json()["error"]
    response = client.post(
        reverse("documents:api_return", args=[document.pk]), json.dumps({"reason": "No"}), content_type="application/json"
    )
    assert response.status_code == 403
    document.refresh_from_db()
    assert document.reviewed_at is None


def test_review_tab_and_review_queue_api_answer_403_without_the_flag(head, login) -> None:
    client = login(head)
    assert client.get(reverse("documents:review")).status_code == 403
    assert client.get(reverse("documents:api_review_queue")).status_code == 403


def test_certificate_evidence_follows_the_same_rule(head, director, login, scheduled, certificate_for) -> None:
    from certificates import services as certificate_services

    evidence = certificate_services.upload_evidence(head, certificate_for(scheduled), make_file("proof.pdf"))
    url = reverse("certificates:api_evidence_action", args=[evidence.pk, "approve"])
    assert login(head).post(url).status_code == 403
    assert login(director).post(url).status_code == 200
    evidence.refresh_from_db()
    assert evidence.status.status_name == DocumentStatus.APPROVED and evidence.reviewed_by == director


def test_director_approves_from_the_page(director, head, login, scheduled, upload) -> None:
    document = upload(head, scheduled)
    response = login(director).post(reverse("documents:approve", args=[document.pk]), follow=True)
    assert f'"{document.title}" was approved.' in [str(m) for m in response.context["messages"]]
    document.refresh_from_db()
    assert document.status.status_name == DocumentStatus.APPROVED


def test_director_approves_their_own_upload_from_the_page(director, login, scheduled, upload) -> None:
    own = upload(director, scheduled)
    login(director).post(reverse("documents:approve", args=[own.pk]))
    own.refresh_from_db()
    assert own.status.status_name == DocumentStatus.APPROVED and own.reviewed_by == director


# --- Hidden for members without the flag -----------------------------------------------------------------------


def test_project_head_sees_no_review_tab_badge_or_review_buttons(head, director, view_only, login, scheduled, upload) -> None:
    document = upload(view_only, scheduled)
    client = login(head)
    html = client.get(reverse("documents:list")).content.decode()
    assert "Waiting for my review" not in html
    assert reverse("documents:review") not in html
    assert "documents waiting for your review" not in html  # the sidebar badge's screen-reader text
    detail = client.get(reverse("documents:detail", args=[document.pk])).content.decode()
    assert reverse("documents:approve", args=[document.pk]) not in detail
    assert reverse("documents:action_form", args=[document.pk, "return"]) not in detail


def test_director_sees_the_review_tab_badge_and_buttons(director, view_only, login, scheduled, upload) -> None:
    document = upload(view_only, scheduled)
    client = login(director)
    html = client.get(reverse("documents:list")).content.decode()
    assert "Waiting for my review" in html and reverse("documents:review") in html
    assert "documents waiting for your review" in html
    detail = client.get(reverse("documents:detail", args=[document.pk])).content.decode()
    assert reverse("documents:approve", args=[document.pk]) in detail


def test_dashboard_shows_my_review_count_only_to_approvers(director, head, view_only, login, scheduled, upload) -> None:
    upload(view_only, scheduled)
    assert "Documents to review" in login(director).get(reverse("home")).content.decode()
    html = login(head).get(reverse("home")).content.decode()
    assert "Documents to review" not in html and "waiting for your review" not in html


# --- "Document waiting for your review" notifications ------------------------------------------------------------


def test_upload_notifies_every_active_approver(director, second_director, view_only, head, scheduled, upload) -> None:
    document = upload(view_only, scheduled, title="Signed invitation")
    for approver in (director, second_director):
        [notification] = waiting_for(approver)
        assert notification.activity == scheduled and notification.document_type == document.document_type
        assert "Signed invitation" in notification.message and view_only.name in notification.message
        assert not notification.is_read
    assert waiting_for(head) == [] and waiting_for(view_only) == []  # no flag: nothing


def test_the_uploader_is_never_notified_about_their_own_upload(director, second_director, scheduled, upload) -> None:
    """The Director uploads: the other approver is told, the Director is not."""
    upload(director, scheduled)
    assert waiting_for(director) == []
    assert len(waiting_for(second_director)) == 1


def test_the_only_approver_uploading_notifies_nobody(director, scheduled, upload) -> None:
    upload(director, scheduled)
    assert not Notification.objects.filter(type=WAITING).exists()


def test_replacing_notifies_the_approvers_again_but_not_the_uploader(
    director, second_director, view_only, scheduled, upload
) -> None:
    document = upload(view_only, scheduled)
    services.return_document(director, document, "Wrong file")
    services.replace_document_file(view_only, document, make_file("v2.pdf"))
    assert len(waiting_for(director)) == 2 and len(waiting_for(second_director)) == 2
    assert waiting_for(view_only) == []


def test_the_director_replacing_their_own_file_is_not_notified(director, second_director, scheduled, upload) -> None:
    own = upload(director, scheduled)
    services.replace_document_file(director, own, make_file("v2.pdf"))
    assert waiting_for(director) == []
    assert len(waiting_for(second_director)) == 2


def test_inactive_approvers_are_not_notified(director, view_only, scheduled, upload) -> None:
    director.is_active = False
    director.save()
    upload(view_only, scheduled)
    assert not Notification.objects.filter(type=WAITING).exists()


def test_a_refused_upload_notifies_nobody(director, view_only, scheduled, doc_type) -> None:
    with pytest.raises(Exception):
        services.upload_document(view_only, scheduled, doc_type, make_file("fake.pdf", b"not a pdf"))
    assert not Notification.objects.filter(type=WAITING).exists()


def test_a_refused_new_activity_with_documents_notifies_nobody(director, head, doc_type) -> None:
    from documents.services import NewActivityError, NewDocument

    with pytest.raises(NewActivityError):
        services.create_activity_with_documents(head, {"title": ""}, [NewDocument(doc_type, make_file(), "")])
    assert not Notification.objects.filter(type=WAITING).exists()
