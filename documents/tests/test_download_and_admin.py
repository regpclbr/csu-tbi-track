"""Serving files: only through the permission-checked download view, always as an attachment.
Also the Django admin screens for documents and notifications (view only)."""

import pytest
from django.urls import reverse

from documents.models import Document
from notifications.models import Notification

from .conftest import PDF_BYTES

pytestmark = pytest.mark.django_db


@pytest.fixture
def document(upload, director, scheduled) -> Document:
    return upload(director, scheduled)


def url(document: Document) -> str:
    return reverse("documents:download", args=[document.pk])


def test_download_without_login_is_refused(client, document) -> None:
    response = client.get(url(document))
    assert response.status_code == 302 and "/accounts/login/" in response.url


def test_download_with_a_deactivated_account_is_refused(client, document, admin_staff) -> None:
    client.force_login(admin_staff)
    admin_staff.is_active = False
    admin_staff.save()
    response = client.get(url(document))
    assert response.status_code == 302 and "/accounts/login/" in response.url


def test_any_active_staff_member_downloads_as_attachment(client, document, admin_staff) -> None:
    client.force_login(admin_staff)  # not the uploader, no can_create_activity
    response = client.get(url(document))
    assert response.status_code == 200
    assert response["Content-Disposition"].startswith("attachment;")
    assert 'filename="Invitation.pdf"' in response["Content-Disposition"]  # the original name
    assert response["X-Content-Type-Options"] == "nosniff"
    assert b"".join(response.streaming_content) == PDF_BYTES


def test_missing_file_gives_404(client, document, director) -> None:
    document.file_path.delete(save=False)
    client.force_login(director)
    assert client.get(url(document)).status_code == 404


def test_certificate_files_are_not_served_here_yet(client, document, director, scheduled, certificate_for) -> None:
    Document.objects.filter(pk=document.pk).update(related_certificate=certificate_for(scheduled))
    client.force_login(director)
    assert client.get(url(document)).status_code == 404


def test_media_url_is_not_served_even_in_debug(client, document, director, settings) -> None:
    settings.DEBUG = True
    client.force_login(director)
    assert client.get("/media/" + document.file_path.name).status_code == 404


# --- Django admin ---------------------------------------------------------------------------------


def test_document_admin_is_view_only_with_download_link(client, admin_member, document) -> None:
    client.force_login(admin_member)
    listing = client.get("/admin/documents/document/")
    assert listing.status_code == 200 and url(document) in listing.content.decode()
    page = client.get(f"/admin/documents/document/{document.pk}/change/")
    html = page.content.decode()
    assert page.status_code == 200 and url(document) in html
    assert "/media/" not in html
    assert 'name="_save"' not in html  # no Save button: view only
    assert client.get("/admin/documents/document/add/").status_code == 403
    assert client.post(f"/admin/documents/document/{document.pk}/delete/").status_code == 403


def test_notification_admin_is_view_only(client, admin_member, director, scheduled) -> None:
    notification = Notification.objects.create(member=director, activity=scheduled, message="Hi", type="x")
    client.force_login(admin_member)
    assert client.get("/admin/notifications/notification/").status_code == 200
    page = client.get(f"/admin/notifications/notification/{notification.pk}/change/").content.decode()
    assert 'name="_save"' not in page
    assert client.get("/admin/notifications/notification/add/").status_code == 403
    assert client.post(f"/admin/notifications/notification/{notification.pk}/delete/").status_code == 403
