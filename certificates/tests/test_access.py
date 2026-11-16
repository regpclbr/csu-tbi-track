"""Who sees certificates: staff (list, detail, download), the startup portal (only its own Issued
certificates), and evidence documents (DOCUMENT.related_certificate, Step 6 rules)."""

import pytest
from django.core.exceptions import PermissionDenied
from django.core.files.uploadedfile import SimpleUploadedFile

from certificates import services
from certificates.models import Certificate
from documents import services as document_services
from documents.models import Document
from registrations.models import Registration

from .conftest import S, appreciation_of, certificate_of

pytestmark = pytest.mark.django_db

PDF = b"%PDF-1.7\n1 0 obj\n<<>>\nendobj\n"


@pytest.fixture
def both_issued(generated, approve_all, evaluate, run_tasks, django_capture_on_commit_callbacks):
    """Both participants evaluated and their certificates Issued (and emailed)."""
    activity, people = generated
    with django_capture_on_commit_callbacks(execute=True):
        evaluate(people[0], name="Person Zero", email="zero.cert@example.com")
        evaluate(people[1], name="Person One", email="one.cert@example.com")
        approve_all(activity)
    return activity, people


# --- Staff ------------------------------------------------------------------------------------------


def test_any_active_staff_member_lists_and_reads(client, both_issued, view_only) -> None:
    activity, people = both_issued
    client.force_login(view_only)  # no can_create_activity: reading is still allowed
    data = client.get(f"/certificates/api/?activity={activity.pk}&status=issued").json()
    assert data["total"] == 2 and {c["recipient"] for c in data["certificates"]} == {"Ana Cruz"}
    assert client.get("/certificates/api/?recipient_type=resource_person").json()["total"] == 1
    assert client.get("/certificates/api/?status=lost").status_code == 400

    certificate = certificate_of(people[0])
    detail = client.get(f"/certificates/api/{certificate.pk}/").json()["certificate"]
    assert detail["certificate_name"] == "Person Zero" and detail["certificate_email"] == "zero.cert@example.com"
    assert detail["status"] == "issued" and detail["download_url"] == f"/certificates/{certificate.pk}/download/"

    response = client.get(detail["download_url"])
    assert response.status_code == 200 and response["Content-Disposition"].startswith("attachment")
    assert response["X-Content-Type-Options"] == "nosniff"


def test_download_before_the_pdf_exists_gives_the_preview(client, generated, view_only) -> None:
    """ASSUMPTIONS 358: "Download PDF" works before rendering: the preview, as an attachment (was 404)."""
    client.force_login(view_only)
    certificate = appreciation_of(generated[0])
    response = client.get(f"/certificates/{certificate.pk}/download/")
    assert response.status_code == 200 and response["Content-Type"] == "application/pdf"
    assert response["Content-Disposition"] == f'attachment; filename="{certificate.certificate_number}-preview.pdf"'


def test_not_logged_in_is_sent_to_the_login(client, both_issued) -> None:
    certificate = certificate_of(both_issued[1][0])
    for url in ("/certificates/api/", f"/certificates/api/{certificate.pk}/", f"/certificates/{certificate.pk}/download/"):
        assert client.get(url).status_code == (401 if "/api/" in url else 302)  # API: JSON; file: login page


# --- Startup portal ---------------------------------------------------------------------------------


def test_startup_sees_and_downloads_only_its_own_issued_certificates(both_issued, make_startup, portal_for) -> None:
    activity, people = both_issued
    mine = make_startup(email="person0@example.com", navigatu_id="NAV-0")  # matches people[0] by email (Step 7)
    other = make_startup(email="person1@example.com", navigatu_id="NAV-1", name="Other Startup")
    portal = portal_for(mine)

    listed = portal.get("/startups/portal/api/certificates/").json()["certificates"]
    assert [c["certificate_number"] for c in listed] == [certificate_of(people[0]).certificate_number]
    assert portal.get(listed[0]["download_url"]).status_code == 200

    others = certificate_of(people[1])
    assert portal.get(f"/startups/portal/certificates/{others.pk}/download/").status_code == 404
    assert portal.get(f"/startups/portal/certificates/{appreciation_of(activity).pk}/download/").status_code == 404
    assert portal_for(other).get(listed[0]["download_url"]).status_code == 404


def test_startup_list_is_based_on_registrations_not_on_certificate_email(both_issued, make_startup, portal_for) -> None:
    """The certificate went to zero.cert@example.com, but "My certificates" follows the registration."""
    stranger = make_startup(email="zero.cert@example.com", navigatu_id="NAV-9")
    assert portal_for(stranger).get("/startups/portal/api/certificates/").json()["certificates"] == []


def test_startup_sees_linked_registrations_and_only_issued_ones(generated, make_startup, portal_for) -> None:
    activity, people = generated
    linked = make_startup(email="different@example.com", navigatu_id="NAV-5")
    Registration.objects.filter(pk=people[1].pk).update(startup=linked)
    portal = portal_for(linked)
    assert portal.get("/startups/portal/api/certificates/").json()["certificates"] == []  # Generated: not shown yet
    Certificate.objects.filter(registration=people[1]).update(status=S.ISSUED)
    assert len(portal.get("/startups/portal/api/certificates/").json()["certificates"]) == 1


def test_staff_and_anonymous_cannot_use_the_portal(client, both_issued, director) -> None:
    certificate = certificate_of(both_issued[1][0])
    url = f"/startups/portal/certificates/{certificate.pk}/download/"
    assert client.get(url).status_code == 401
    client.force_login(director)
    assert client.get(url).status_code == 403


# --- Evidence documents (owner decision Q1 = B) -----------------------------------------------------


def test_evidence_follows_the_step6_rules(client, generated, director, view_only, make_member) -> None:
    activity, _ = generated
    certificate = appreciation_of(activity)
    client.force_login(view_only)  # any active staff member may upload (Step 6)
    response = client.post(f"/certificates/api/{certificate.pk}/evidence/", {"file": SimpleUploadedFile("receipt.pdf", PDF), "title": "Signed receipt"})
    assert response.status_code == 201, response.json()
    evidence = Document.objects.get(pk=response.json()["document"]["document_id"])
    assert evidence.related_certificate == certificate and evidence.status.status_name == "For Review"
    assert evidence.document_type.name == "Certificate" and "receipt" not in evidence.file_path.name

    bad = client.post(f"/certificates/api/{certificate.pk}/evidence/", {"file": SimpleUploadedFile("x.pdf", b"not a pdf")})
    assert bad.status_code == 400

    # Review: can_create_activity, never the uploader.
    assert client.post(f"/certificates/api/evidence/{evidence.pk}/approve/").status_code == 403
    client.force_login(director)
    assert client.post(f"/certificates/api/evidence/{evidence.pk}/approve/").json()["document"]["status"] == "Approved"
    assert client.get(f"/certificates/evidence/{evidence.pk}/download/").status_code == 200

    # Never in the repository, the checklist or the document download (item 26).
    assert evidence not in document_services.documents_for_listing()
    assert client.get(f"/documents/{evidence.pk}/download/").status_code == 404
    detail = client.get(f"/certificates/api/{certificate.pk}/").json()["certificate"]
    assert [d["document_id"] for d in detail["evidence"]] == [evidence.pk]


def test_approved_evidence_is_not_indexed(generated, director, view_only, monkeypatch, django_capture_on_commit_callbacks) -> None:
    queued = []
    monkeypatch.setattr(document_services, "queue_indexing", queued.append)
    evidence = services.upload_evidence(view_only, appreciation_of(generated[0]), SimpleUploadedFile("scan.pdf", PDF))
    with django_capture_on_commit_callbacks(execute=True):
        document_services.approve_document(director, evidence)
    assert queued == []


def test_only_the_uploader_deletes_evidence(generated, director, view_only) -> None:
    evidence = services.upload_evidence(view_only, appreciation_of(generated[0]), SimpleUploadedFile("scan.pdf", PDF))
    with pytest.raises(PermissionDenied):
        document_services.delete_document(director, evidence)
    document_services.delete_document(view_only, evidence)
    assert not Document.objects.filter(pk=evidence.pk).exists()
