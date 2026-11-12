"""Phase F4: the activity's Certificates tab (Generate, the Director's approval, the bulk actions, previews, evidence) and the
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
    assert re.search(r'Waiting for approval</span>\s*<span class="font-semibold text-ink">3</span>', html)
    dialog = reverse("certificates:preview_dialog", args=[certificate_of(people[0]).pk])
    assert f'hx-get="{dialog}" hx-target="#certificate-preview-body"' in html
    assert reverse("certificates:download", args=[certificate_of(people[0]).pk]) in html  # Download PDF (preview until rendered)


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


def test_director_sees_the_approval_screen_and_approves_all(director, login, generated, run_tasks,
                                                            django_capture_on_commit_callbacks) -> None:
    activity, people = generated
    client = login(director)
    html = client.get(tab(activity)).content.decode()
    assert "Waiting for your approval" in html
    assert "3 certificates · 2 participants by email · 1 resource person by hard copy" in html
    assert "Approve all for this activity" in html and 'id="approve-all-dialog"' in html
    assert reverse("activities:certificates_approve_all", args=[activity.pk]) in html
    first = Certificate.objects.filter(activity=activity, status=S.GENERATED).order_by("certificate_number").first()
    assert f'src="{reverse("certificates:preview", args=[first.pk])}"' in html  # the first certificate's preview
    assert 'id="approval-previews"' in html and "Previous" in html and "Next" in html

    with django_capture_on_commit_callbacks(execute=True):  # approval queues the PDF rendering after the commit
        response = client.post(reverse("activities:certificates_approve_all", args=[activity.pk]), follow=True)
    assert ("Approved 3 certificates. Each participant's certificate is emailed once their evaluation is in; "
            "ready PDFs are rendered in the background.") in messages_of(response)
    assert set(Certificate.objects.values_list("status", flat=True)) == {S.APPROVED}
    html = client.get(tab(activity)).content.decode()
    assert "Waiting for your approval" not in html and "Approve all for this activity" not in html


def test_others_see_waiting_for_the_director(make_member, login, generated) -> None:
    activity, _ = generated
    head = make_member("Incubation Project Head")
    client = login(head)
    html = client.get(tab(activity)).content.decode()
    assert "Waiting for the Director's approval" in html  # template text: not escaped
    assert "Approve all for this activity" not in html and 'value="approve"' not in html
    assert 'value="regenerate"' in html  # project heads still regenerate / hand over / resend
    assert client.post(reverse("activities:certificates_approve_all", args=[activity.pk])).status_code == 403
    assert bulk(client, activity, "approve", Certificate.objects.all()).status_code == 403
    assert set(Certificate.objects.values_list("status", flat=True)) == {S.GENERATED}


def test_missing_signature_disables_approve_all(director, login, generated, design) -> None:
    activity, _ = generated
    from pathlib import Path
    from django.conf import settings
    (Path(settings.MEDIA_ROOT) / design[1].signature_image_path.name).unlink()
    client = login(director)
    html = client.get(tab(activity)).content.decode()
    message = "The VP&#x27;s signature image is missing. Upload it in the Admin Console, then regenerate the certificates."
    assert message in html and 'aria-describedby="approval-reason"' in html and 'id="approve-all-dialog"' not in html
    response = client.post(reverse("activities:certificates_approve_all", args=[activity.pk]), follow=True)
    assert message.replace("&#x27;", "'") in messages_of(response)


def test_bulk_approve_selected(director, login, generated) -> None:
    activity, people = generated
    response = bulk(login(director), activity, "approve", [certificate_of(people[0])], follow=True)
    assert any(m.startswith("Approved 1 certificate.") for m in messages_of(response))
    assert certificate_of(people[0]).status == S.APPROVED


def test_bulk_with_one_invalid_certificate_changes_nothing_and_lists_it(director, login, generated) -> None:
    activity, people = generated
    already = certificate_of(people[0])
    services.approve_certificates(director, certificate_ids=[already.pk])
    certificates = list(Certificate.objects.filter(activity=activity))
    response = bulk(login(director), activity, "approve", certificates)
    html = response.content.decode()
    assert response.status_code == 400
    assert "Nothing was changed. These certificates were refused:" in html
    assert re.search(rf"<li>{already.certificate_number}: Cannot move a certificate from Approved to Approved.</li>", html)
    assert Certificate.objects.filter(status=S.GENERATED).count() == 2  # the other two were not changed
    assert "Nothing was changed: 1 of the 3 certificates selected was refused. See the list on the page." in messages_of(response)


def test_regenerate_goes_back_to_waiting_for_approval(director, login, generated) -> None:
    activity, _ = generated
    services.approve_certificates(director, activity=activity)
    response = bulk(login(director), activity, "regenerate", Certificate.objects.filter(activity=activity), follow=True)
    assert ("Regenerated 3 certificates with the current design: waiting for the Director's approval again."
            in messages_of(response))
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
    response = bulk(client, activity, "approve", [], follow=True)
    assert "Select at least one certificate of this activity first." in messages_of(response)
    response = client.post(reverse("activities:certificates_bulk", args=[activity.pk]), {"action": "approve", "certificate_ids": ["999999"]}, follow=True)
    assert "Select at least one certificate of this activity first." in messages_of(response)
    response = client.post(reverse("activities:certificates_bulk", args=[activity.pk]), {"action": "review", "certificate_ids": [Certificate.objects.first().pk]}, follow=True)
    assert "Choose an action." in messages_of(response)  # the old sign-off actions are gone
    assert not Certificate.objects.exclude(status=S.GENERATED).exists()


@pytest.mark.parametrize("role_name", ["Admin Staff", "System Administrator"])
def test_read_only_member_sees_no_actions(make_member, login, generated, role_name) -> None:
    activity, _ = generated
    client = login(make_member(role_name))
    html = client.get(tab(activity)).content.decode()
    assert "Generate certificates" not in html and 'name="certificate_ids"' not in html and 'value="approve"' not in html
    assert "Approve all for this activity" not in html
    assert "Preview" in html and "Evidence" in html and "PDF<span" in html
    for action in ("approve", "regenerate", "hand_over", "resend"):
        assert bulk(client, activity, action, Certificate.objects.all()).status_code == 403
    assert client.post(reverse("activities:certificates_generate", args=[activity.pk])).status_code == 403
    assert client.post(reverse("activities:certificates_approve_all", args=[activity.pk])).status_code == 403
    assert not Certificate.objects.exclude(status=S.GENERATED).exists()


def test_preview_opens_inline_and_downloads_need_a_login(director, login, generated, client) -> None:
    activity, people = generated
    certificate = certificate_of(people[0])
    response = login(director).get(reverse("certificates:preview", args=[certificate.pk]))
    assert response["Content-Disposition"].startswith("inline")
    assert response["X-Frame-Options"] == "SAMEORIGIN"  # shown in the preview dialog's <iframe>
    assert client.get(reverse("certificates:preview", args=[certificate.pk])).status_code == 302
    assert client.get(reverse("certificates:download", args=[certificate.pk])).status_code == 302
    response = login(director).get(reverse("certificates:download", args=[certificate.pk]))  # not rendered yet: the preview
    assert response.status_code == 200 and response["Content-Disposition"].startswith("attachment")
    assert f'{certificate.certificate_number}-preview.pdf' in response["Content-Disposition"]


def test_preview_dialog_and_page(admin_staff, login, generated) -> None:
    activity, people = generated
    certificate = certificate_of(people[0])
    client = login(admin_staff)  # view only: may still preview
    url = reverse("certificates:preview_dialog", args=[certificate.pk])
    html = client.get(url, **HTMX).content.decode()
    assert certificate.certificate_number in html and "Download PDF" in html
    assert f'src="{reverse("certificates:preview", args=[certificate.pk])}"' in html
    assert "Marked “PREVIEW” until the Director approves it." in html
    assert "The printed name is the one the participant types in the evaluation." in html  # not evaluated yet
    page = client.get(url)
    assert page.status_code == 200 and "<html" in page.content.decode() and tab(activity) in page.content.decode()


def test_preview_shows_both_signatures_and_a_corner_mark_until_approved(director, generated, fake_pdf_renderer) -> None:
    activity, people = generated
    certificate = certificate_of(people[0])
    services.preview_pdf(director, certificate)
    html = fake_pdf_renderer.last
    assert html.count("data:image/png;base64,") == 3  # background + both signatures: exactly as printed
    assert 'class="preview-mark">PREVIEW<' in html and "NOT VALID" not in html
    assert "Ana Cruz" in html  # the registration name until the evaluation is in
    services.approve_certificates(director, activity=activity)
    certificate.refresh_from_db()
    services.preview_pdf(director, certificate)
    assert "preview-mark" not in fake_pdf_renderer.last.split("<body>", 1)[1]  # approved: no mark


def test_failed_send_shows_send_failed_and_resend(director, login, generated, approve_all, evaluate, run_tasks,
                                                  monkeypatch, django_capture_on_commit_callbacks) -> None:
    from certificates import emails
    from certificates import pages as certificate_pages
    activity, people = generated
    monkeypatch.setattr(certificate_pages, "prints_emails", lambda: True)  # development mode (console mailer)
    real_send = emails.send_certificate_email
    monkeypatch.setattr(emails, "send_certificate_email", lambda *a, **k: (_ for _ in ()).throw(OSError("down")))
    monkeypatch.setattr(services, "schedule_send_retry", lambda *args: None)
    with django_capture_on_commit_callbacks(execute=True):
        approve_all(activity)
        evaluate(people[0])
    certificate = certificate_of(people[0])
    client = login(director)
    html = client.get(tab(activity)).content.decode()
    assert "Send failed" in html and f'form="resend-{certificate.pk}"' in html and f'id="resend-{certificate.pk}"' in html
    assert "Emails are only printed in the qcluster terminal, not sent." in html  # development mode (console mailer)

    monkeypatch.setattr(emails, "send_certificate_email", real_send)
    with django_capture_on_commit_callbacks(execute=True):
        response = bulk(client, activity, "resend", [certificate], follow=True)
    assert any(m.startswith("Queued 1 certificate to be emailed again.") for m in messages_of(response))
    certificate.refresh_from_db()
    assert certificate.sent_at and certificate.send_failed_at is None
    assert "Send failed" not in client.get(tab(activity)).content.decode()


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
    services.approve_certificates(director, certificate_ids=[certificate_of(people[0]).pk])
    client = login(director)
    html = client.get(reverse("certificates:list")).content.decode()
    assert "3 certificates" in html and activity.title in html and "is coming soon" not in html
    assert re.search(r'Approved</span>\s*<span class="font-semibold text-ink">1</span>', html)
    assert re.search(r'Waiting for approval</span>\s*<span class="font-semibold text-ink">2</span>', html)
    html = client.get(reverse("certificates:list"), {"delivery_method": "hard_copy"}, **HTMX).content.decode()
    assert "1 certificate matching the filters" in html and "Dr. Maria Santos" in html and "layouts/app.html" not in html
    html = client.get(reverse("certificates:list"), {"status": "approved", "recipient_type": "registration"}).content.decode()
    assert "1 certificate matching the filters" in html
    html = client.get(reverse("certificates:list"), {"status": "nope", "activity": "x"}).content.decode()
    assert "Some filters could not be read and were ignored: activity, status." in html and "3 certificates" in html


def test_certificates_page_empty_state(admin_staff, login) -> None:
    assert "No certificates yet" in login(admin_staff).get(reverse("certificates:list")).content.decode()


# --- Read-only service helpers -----------------------------------------------------------------------------------


def test_status_counts_ignore_the_status_filter_and_fill_zeros(director, generated) -> None:
    activity, people = generated
    services.approve_certificates(director, certificate_ids=[certificate_of(people[0]).pk])
    counts = services.certificate_status_counts({"activity": activity.pk, "status": S.ISSUED})
    assert counts == {S.GENERATED: 2, S.APPROVED: 1, S.ISSUED: 0}
    assert services.certificate_status_counts({"delivery_method": "hard_copy"})[S.GENERATED] == 1


def test_list_filters_by_delivery_method(generated) -> None:
    assert services.list_certificates({"delivery_method": "email"}).total == 2
    with pytest.raises(services.CertificateError, match="delivery_method must be one of"):
        services.list_certificates({"delivery_method": "pigeon"})


def test_bulk_refusal_lists_its_problems(director, generated) -> None:
    activity, people = generated
    services.approve_certificates(director, certificate_ids=[certificate_of(people[0]).pk])
    with pytest.raises(services.CertificateError) as error:
        services.approve_certificates(director, certificate_ids=list(Certificate.objects.values_list("pk", flat=True)))
    assert error.value.problems == [f"{certificate_of(people[0]).certificate_number}: Cannot move a certificate from Approved to Approved."]
    assert str(error.value).startswith("Nothing was changed. ")


def test_generation_closed_reason_before_conducted(scheduled, design) -> None:
    assert services.generation_closed_reason(scheduled) == services.NOT_CONDUCTED


def test_generation_closed_reason(conducted, design) -> None:
    activity, _ = conducted
    assert services.generation_closed_reason(activity) is None
    design[0].is_active = False
    design[0].save()
    assert services.generation_closed_reason(activity).startswith("Cannot generate certificates: there is no active certificate template")
