"""Phase F4: the activity's Certificates tab (Generate, the bulk sign-off actions, previews, evidence) and the
Certificates page, plus the small read-only service helpers they use. The rules are tested with the services; these
tests check that the pages call them, show their messages and refusals, and respect the permissions."""

import re

import pytest
from django.test import Client
from django.urls import reverse

from certificates import services
from certificates.models import Certificate
from documents.models import Document
from documents.tests.conftest import make_file

from .conftest import S, appreciation_of, certificate_of

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


def tab(activity) -> str:
    return f"{reverse('activities:detail', args=[activity.pk])}?tab=certificates"


def bulk(client, activity, action: str, certificates, **extra):
    data = {"action": action, "certificate_ids": [c.pk for c in certificates]}
    return client.post(reverse("activities:certificates_bulk", args=[activity.pk]), data, **extra)


# --- The tab ---------------------------------------------------------------------------------------------------


def test_tab_lists_every_certificate(director, login, generated) -> None:
    activity, people = generated
    html = login(director).get(tab(activity)).content.decode()
    assert 'id="certificates-tab"' in html and "coming in phase" not in html
    for certificate in Certificate.objects.filter(activity=activity):
        assert certificate.certificate_number in html
    assert "Dr. Maria Santos" in html and "Participation" in html and "Appreciation" in html
    assert "Hard copy" in html and "Email" in html
    assert re.search(r'Generated</span>\s*<span class="font-semibold text-ink">3</span>', html)
    preview = reverse("certificates:preview", args=[certificate_of(people[0]).pk])
    assert re.search(rf'<a href="{preview}" target="_blank" rel="noopener"', html)
    assert reverse("certificates:download", args=[certificate_of(people[0]).pk]) not in html  # no PDF yet


def test_generate_is_refused_before_conducted(director, login, scheduled) -> None:
    client = login(director)
    html = client.get(tab(scheduled)).content.decode()
    assert services.NOT_CONDUCTED in html and re.search(r"Generate certificates</button>", html)
    assert re.search(r'<button type="submit" class="btn btn-primary btn-sm" disabled', html)
    response = client.post(reverse("activities:certificates_generate", args=[scheduled.pk]), follow=True)
    assert services.NOT_CONDUCTED in messages_of(response) and not Certificate.objects.exists()


def test_generating_without_a_template_shows_the_service_message(director, login, conducted) -> None:
    activity, _ = conducted
    client = login(director)
    html = client.get(tab(activity)).content.decode()
    assert "Cannot generate certificates: there is no active certificate template" in html
    response = client.post(reverse("activities:certificates_generate", args=[activity.pk]), follow=True)
    assert any(m.startswith("Cannot generate certificates: there is no active certificate template") for m in messages_of(response))
    assert not Certificate.objects.exists()


def test_generate_creates_the_missing_certificates(director, login, conducted, design) -> None:
    activity, _ = conducted
    response = login(director).post(reverse("activities:certificates_generate", args=[activity.pk]), follow=True)
    assert "Created 2 Certificate(s) of Participation and 1 of Appreciation. The activity now has 3." in messages_of(response)
    response = login(director).post(reverse("activities:certificates_generate", args=[activity.pk]), follow=True)
    assert "Nothing new to create: the activity already has all 3 certificates." in messages_of(response)


def test_bulk_review_sign_and_approve(director, login, generated, run_tasks, django_capture_on_commit_callbacks) -> None:
    activity, _ = generated
    client = login(director)
    certificates = list(Certificate.objects.filter(activity=activity))
    response = bulk(client, activity, "review", certificates, follow=True)
    assert "Reviewed 3 certificates: now Pending Sign-off." in messages_of(response)
    assert bulk(client, activity, "sign_director", certificates).status_code == 302
    assert bulk(client, activity, "sign_vp", certificates).status_code == 302
    with django_capture_on_commit_callbacks(execute=True):  # approval queues the PDF rendering after the commit
        response = bulk(client, activity, "approve", certificates, follow=True)
    assert "Approved 3 certificates. Ready PDFs are rendered in the background." in messages_of(response)
    assert set(Certificate.objects.values_list("status", flat=True)) == {S.APPROVED}
    html = client.get(tab(activity)).content.decode()
    assert reverse("certificates:download", args=[appreciation_of(activity).pk]) in html  # its PDF was rendered


def test_bulk_with_one_invalid_certificate_changes_nothing_and_lists_it(director, login, generated) -> None:
    activity, people = generated
    already = certificate_of(people[0])
    services.review_certificates(director, certificate_ids=[already.pk])
    certificates = list(Certificate.objects.filter(activity=activity))
    response = bulk(login(director), activity, "review", certificates)
    html = response.content.decode()
    assert response.status_code == 400
    assert "Nothing was changed. These certificates were refused:" in html
    assert re.search(rf"<li>{already.certificate_number}: Cannot move a certificate from Pending Sign-off to Pending Sign-off.</li>", html)
    assert Certificate.objects.filter(status=S.GENERATED).count() == 2  # the other two were not changed
    assert "Nothing was changed: 1 of the 3 certificates selected was refused. See the list on the page." in messages_of(response)


def test_reject_goes_back_to_generated(director, login, generated) -> None:
    activity, _ = generated
    services.review_certificates(director, activity=activity)
    response = bulk(login(director), activity, "reject", Certificate.objects.filter(activity=activity), follow=True)
    assert "Rejected 3 certificates: back to Generated, sign-offs cleared." in messages_of(response)
    assert set(Certificate.objects.values_list("status", flat=True)) == {S.GENERATED}


def test_hand_over_only_for_hard_copy(director, login, generated, approve_all, run_tasks, django_capture_on_commit_callbacks) -> None:
    activity, people = generated
    with django_capture_on_commit_callbacks(execute=True):  # the resource person's PDF is rendered after approval
        approve_all(activity)
    client = login(director)
    participant = certificate_of(people[0])
    response = bulk(client, activity, "hand_over", [participant, appreciation_of(activity)])
    assert response.status_code == 400
    assert f"{participant.certificate_number}: only hard-copy certificates are handed over" in response.content.decode()
    assert appreciation_of(activity).status == S.APPROVED  # all or nothing

    response = bulk(client, activity, "hand_over", [appreciation_of(activity)], follow=True)
    assert "Marked 1 certificate as handed over: now Issued." in messages_of(response)
    assert appreciation_of(activity).status == S.ISSUED


def test_bulk_needs_a_selection_of_this_activity(director, login, generated, conducted) -> None:
    activity, _ = generated
    client = login(director)
    response = bulk(client, activity, "review", [], follow=True)
    assert "Select at least one certificate of this activity first." in messages_of(response)
    response = client.post(reverse("activities:certificates_bulk", args=[activity.pk]), {"action": "review", "certificate_ids": ["999999"]}, follow=True)
    assert "Select at least one certificate of this activity first." in messages_of(response)
    assert not Certificate.objects.exclude(status=S.GENERATED).exists()


def test_read_only_member_sees_no_actions(admin_staff, login, generated) -> None:
    activity, _ = generated
    client = login(admin_staff)
    html = client.get(tab(activity)).content.decode()
    assert "Generate certificates" not in html and 'name="certificate_ids"' not in html and 'value="approve"' not in html
    assert "Preview" in html and "Evidence" in html
    assert bulk(client, activity, "review", Certificate.objects.all()).status_code == 403
    assert client.post(reverse("activities:certificates_generate", args=[activity.pk])).status_code == 403
    assert not Certificate.objects.exclude(status=S.GENERATED).exists()


def test_preview_opens_inline_and_downloads_need_a_login(director, login, generated, client) -> None:
    activity, people = generated
    certificate = certificate_of(people[0])
    response = login(director).get(reverse("certificates:preview", args=[certificate.pk]))
    assert response["Content-Disposition"].startswith("inline")
    assert client.get(reverse("certificates:preview", args=[certificate.pk])).status_code == 302
    assert client.get(reverse("certificates:download", args=[certificate.pk])).status_code == 302
    assert login(director).get(reverse("certificates:download", args=[certificate.pk])).status_code == 404  # no PDF yet


# --- Evidence ----------------------------------------------------------------------------------------------------


def test_evidence_upload_list_and_review(director, admin_staff, login, generated) -> None:
    activity, people = generated
    certificate = certificate_of(people[0])
    uploader = login(admin_staff)
    url = reverse("certificates:evidence", args=[certificate.pk])
    response = uploader.get(url, **HTMX)
    assert "certificates/_evidence.html" in [t.name for t in response.templates] and "No evidence uploaded yet." in response.content.decode()

    upload_url = reverse("certificates:evidence_upload", args=[certificate.pk])
    response = uploader.post(upload_url, {"file": make_file("notes.txt", b"plain")}, **HTMX)
    assert response.status_code == 200 and 'id="evidence-file-error"' in response.content.decode()
    response = uploader.post(upload_url, {"file": make_file("receipt.pdf"), "title": "Receipt", "next": tab(activity)}, **HTMX)
    assert response["HX-Redirect"] == tab(activity)
    evidence = Document.objects.get(related_certificate=certificate)
    assert evidence.title == "Receipt" and evidence.uploaded_by == admin_staff

    html = login(director).get(url, **HTMX).content.decode()
    assert "Receipt" in html and reverse("documents:approve", args=[evidence.pk]) in html  # director may review it
    assert reverse("certificates:evidence_download", args=[evidence.pk]) in html
    assert "Evidence (1)" in login(director).get(tab(activity)).content.decode()
    login(director).post(reverse("documents:approve", args=[evidence.pk]))
    evidence.refresh_from_db()
    assert evidence.review_state == Document.APPROVED


def test_evidence_page_without_javascript(admin_staff, login, generated) -> None:
    activity, people = generated
    response = login(admin_staff).get(reverse("certificates:evidence", args=[certificate_of(people[0]).pk]))
    assert "layouts/app.html" in [t.name for t in response.templates] and 'name="file"' in response.content.decode()


# --- The Certificates page ------------------------------------------------------------------------------------


def test_certificates_page_lists_counts_and_filters(director, login, generated) -> None:
    activity, people = generated
    services.review_certificates(director, certificate_ids=[certificate_of(people[0]).pk])
    client = login(director)
    html = client.get(reverse("certificates:list")).content.decode()
    assert "3 certificates" in html and activity.title in html and "is coming soon" not in html
    assert re.search(r'Pending Sign-off</span>\s*<span class="font-semibold text-ink">1</span>', html)
    html = client.get(reverse("certificates:list"), {"delivery_method": "hard_copy"}, **HTMX).content.decode()
    assert "1 certificate matching the filters" in html and "Dr. Maria Santos" in html and "layouts/app.html" not in html
    html = client.get(reverse("certificates:list"), {"status": "pending_signoff", "recipient_type": "registration"}).content.decode()
    assert "1 certificate matching the filters" in html
    html = client.get(reverse("certificates:list"), {"status": "nope", "activity": "x"}).content.decode()
    assert "Some filters could not be read and were ignored: activity, status." in html and "3 certificates" in html


def test_certificates_page_empty_state(admin_staff, login) -> None:
    assert "No certificates yet" in login(admin_staff).get(reverse("certificates:list")).content.decode()


# --- Read-only service helpers -----------------------------------------------------------------------------------


def test_status_counts_ignore_the_status_filter_and_fill_zeros(director, generated) -> None:
    activity, people = generated
    services.review_certificates(director, certificate_ids=[certificate_of(people[0]).pk])
    counts = services.certificate_status_counts({"activity": activity.pk, "status": S.ISSUED})
    assert counts == {S.GENERATED: 2, S.PENDING_SIGNOFF: 1, S.APPROVED: 0, S.ISSUED: 0}
    assert services.certificate_status_counts({"delivery_method": "hard_copy"})[S.GENERATED] == 1


def test_list_filters_by_delivery_method(generated) -> None:
    assert services.list_certificates({"delivery_method": "email"}).total == 2
    with pytest.raises(services.CertificateError, match="delivery_method must be one of"):
        services.list_certificates({"delivery_method": "pigeon"})


def test_bulk_refusal_lists_its_problems(director, generated) -> None:
    activity, people = generated
    services.review_certificates(director, certificate_ids=[certificate_of(people[0]).pk])
    with pytest.raises(services.CertificateError) as error:
        services.review_certificates(director, certificate_ids=list(Certificate.objects.values_list("pk", flat=True)))
    assert error.value.problems == [f"{certificate_of(people[0]).certificate_number}: Cannot move a certificate from Pending Sign-off to Pending Sign-off."]
    assert str(error.value).startswith("Nothing was changed. ")


def test_generation_closed_reason_before_conducted(scheduled, design) -> None:
    assert services.generation_closed_reason(scheduled) == services.NOT_CONDUCTED


def test_generation_closed_reason(conducted, design) -> None:
    activity, _ = conducted
    assert services.generation_closed_reason(activity) is None
    design[0].is_active = False
    design[0].save()
    assert services.generation_closed_reason(activity).startswith("Cannot generate certificates: there is no active certificate template")
