"""Phase F4 and ASSUMPTIONS 362, 369: the activity's Certificates tab (created automatically, "Approve certificates for
this activity", Apply the current design, row actions, previews, evidence) and the Certificates page, plus the
small read-only service helpers they use. The rules are tested with the services; these tests check that the pages call
them, show their messages and refusals, and respect the permissions. More in test_tab_flow.py."""

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


def row_action(client, activity, action: str, certificates, **extra):
    data = {"action": action, "certificate_ids": [c.pk for c in certificates]}
    return client.post(reverse("activities:certificates_action", args=[activity.pk]), data, **extra)


def approve_url(activity) -> str:
    return reverse("activities:certificates_approve_activity", args=[activity.pk])


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
    assert "Waiting for evaluation" in html and "Needs approval" in html  # the computed label + the activity's state
    assert "Created automatically when the activity is Conducted" in html
    page = reverse("certificates:preview_dialog", args=[certificate_of(people[0]).pk])
    assert f'href="{page}"' in html and f"$dispatch('certificate-preview', {certificate_of(people[0]).pk})" in html
    assert reverse("certificates:download", args=[certificate_of(people[0]).pk]) in html  # Download PDF (preview until rendered)


def test_before_conducted_the_tab_explains_when_certificates_come(director, login, scheduled) -> None:
    html = login(director).get(tab(scheduled)).content.decode()
    assert "Certificates are created automatically when the activity is Conducted." in html
    assert "Generate certificates" not in html


def test_without_a_template_the_tab_says_why_nothing_was_created(director, login, conducted) -> None:
    activity, _ = conducted
    html = login(director).get(tab(activity)).content.decode()
    assert "Certificates are not created yet: Cannot create certificates: there is no active certificate design" in html
    assert not Certificate.objects.exists()


def test_approve_the_activity_after_checking_the_preview(director, login, generated, evaluate, run_tasks,
                                                         django_capture_on_commit_callbacks) -> None:
    activity, people = generated
    evaluate(people[0])
    client = login(director)
    html = client.get(tab(activity)).content.decode()
    assert "Approve certificates for this activity" in html and 'id="approve-activity-dialog"' in html
    assert "2 participant certificates not issued yet" in html and "1 resource person certificate" in html
    assert approve_url(activity) in html
    first = Certificate.objects.filter(activity=activity).order_by("certificate_number").first()
    assert f'src="{reverse("certificates:preview", args=[first.pk])}?v=' in html  # the first row's preview
    assert 'id="certificate-previews"' in html and "Previous" in html and "Next" in html

    with django_capture_on_commit_callbacks(execute=True):  # approval queues the PDF rendering after the commit
        response = client.post(approve_url(activity), follow=True)
    assert any(m.startswith("Approved the certificates of this activity. 1 participant who already evaluated is emailed")
               for m in messages_of(response))
    assert certificate_of(people[0]).status == S.ISSUED and certificate_of(people[1]).status == S.GENERATED
    response = client.post(approve_url(activity), follow=True)
    assert messages_of(response)[0] == f"Not approved: {services.ALREADY_APPROVED}"


def test_project_heads_approve(make_member, login, generated) -> None:
    activity, _ = generated
    client = login(make_member("Incubation Project Head"))
    html = client.get(tab(activity)).content.decode()
    assert "Approve certificates for this activity" in html and "Apply the current design" in html
    client.post(approve_url(activity))
    assert appreciation_of(activity).status == S.APPROVED


def test_admin_staff_see_no_approval_controls(make_member, login, generated) -> None:
    activity, _ = generated
    client = login(make_member("Admin Staff"))
    html = client.get(tab(activity)).content.decode()
    assert "Approve certificates for this activity" not in html and "Apply the current design" not in html
    assert "Needs approval" in html  # the state is shown to everyone
    assert client.post(approve_url(activity)).status_code == 403
    assert set(Certificate.objects.values_list("status", flat=True)) == {S.GENERATED}


def test_missing_signature_disables_the_approval(director, login, generated, design) -> None:
    activity, _ = generated
    from pathlib import Path
    from django.conf import settings
    (Path(settings.MEDIA_ROOT) / design[1].signature_image_path.name).unlink()
    client = login(director)
    html = client.get(tab(activity)).content.decode()
    message = "Add the signature of Dr. Vice Pres (Vice President) first."
    assert message in html and 'aria-describedby="approval-reason"' in html and 'id="approve-activity-dialog"' not in html
    response = client.post(approve_url(activity), follow=True)
    assert f"Not approved: {message}" in messages_of(response)


def test_apply_the_current_design_goes_back_to_waiting_for_approval(director, login, generated) -> None:
    activity, _ = generated
    services.approve_activity_certificates(director, activity)  # only the resource person is ready
    response = login(director).post(reverse("activities:certificates_apply_design", args=[activity.pk]), follow=True)
    assert ("Applied the current design to 3 certificates. Approved ones wait for approval again; issued certificates "
            "are not changed.") in messages_of(response)
    activity.refresh_from_db()
    assert not services.is_approved(activity)  # ASSUMPTIONS 369
    assert set(Certificate.objects.values_list("status", flat=True)) == {S.GENERATED}


def test_hand_over_only_for_hard_copy(director, login, generated, approve_all, run_tasks, django_capture_on_commit_callbacks) -> None:
    activity, people = generated
    with django_capture_on_commit_callbacks(execute=True):  # the resource person's PDF is rendered after approval
        approve_all(activity)
    client = login(director)
    html = client.get(tab(activity)).content.decode()
    speaker = appreciation_of(activity)
    assert f'form="hand-over-{speaker.pk}"' in html and f'form="hand-over-{certificate_of(people[0]).pk}"' not in html
    participant = certificate_of(people[0])
    response = row_action(client, activity, "hand_over", [participant], follow=True)
    assert response.redirect_chain[-1][0] == tab(activity)
    assert f"{participant.certificate_number}: only hard-copy certificates are handed over" in response.content.decode()

    response = row_action(client, activity, "hand_over", [speaker], follow=True)
    assert "Marked 1 certificate as handed over: now Issued." in messages_of(response)
    assert appreciation_of(activity).status == S.ISSUED


def test_row_action_needs_a_certificate_of_this_activity(director, login, generated) -> None:
    activity, _ = generated
    client = login(director)
    response = row_action(client, activity, "resend", [], follow=True)
    assert "Choose a certificate of this activity first." in messages_of(response)
    response = client.post(reverse("activities:certificates_action", args=[activity.pk]), {"action": "resend", "certificate_ids": ["999999"]}, follow=True)
    assert "Choose a certificate of this activity first." in messages_of(response)
    response = client.post(reverse("activities:certificates_action", args=[activity.pk]), {"action": "approve", "certificate_ids": [Certificate.objects.first().pk]}, follow=True)
    assert "Choose an action." in messages_of(response)  # approval is per activity (its own button)
    assert not Certificate.objects.exclude(status=S.GENERATED).exists()


def test_read_only_member_sees_no_actions(make_member, login, generated) -> None:
    activity, _ = generated
    client = login(make_member("Admin Staff"))
    html = client.get(tab(activity)).content.decode()
    assert "Generate certificates" not in html and 'name="certificate_ids"' not in html
    assert "Approve certificates for this activity" not in html
    assert "Preview" in html and "Evidence" in html and "PDF<span" in html
    for action in ("hand_over", "resend"):
        assert row_action(client, activity, action, Certificate.objects.all()).status_code == 403
    assert client.post(reverse("activities:certificates_apply_design", args=[activity.pk])).status_code == 403
    assert not Certificate.objects.exclude(status=S.GENERATED).exists()


def test_system_administrator_approves_but_never_applies_the_design(admin_member, login, generated) -> None:
    activity, _ = generated
    client = login(admin_member)
    html = client.get(tab(activity)).content.decode()
    assert "Approve certificates for this activity" in html and "Apply the current design" not in html
    for action in ("hand_over", "resend"):
        assert row_action(client, activity, action, Certificate.objects.all()).status_code == 403
    assert client.post(reverse("activities:certificates_apply_design", args=[activity.pk])).status_code == 403
    client.post(approve_url(activity))
    assert appreciation_of(activity).status == S.APPROVED


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
    assert f'src="{reverse("certificates:preview", args=[certificate.pk])}?v=' in html
    assert "Marked “PREVIEW” until it is approved." in html
    assert "The final name comes from the evaluation." in html  # not evaluated yet
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
    Certificate.objects.filter(pk=certificate.pk).update(status=S.APPROVED)
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
        evaluate(people[0])
        approve_all(activity)
    certificate = certificate_of(people[0])
    client = login(director)
    html = client.get(tab(activity)).content.decode()
    assert "Send failed" in html and f'form="resend-{certificate.pk}"' in html and f'id="resend-{certificate.pk}"' in html
    assert "Emails are only printed in the qcluster terminal, not sent." in html  # development mode (console mailer)

    monkeypatch.setattr(emails, "send_certificate_email", real_send)
    with django_capture_on_commit_callbacks(execute=True):
        response = row_action(client, activity, "resend", [certificate], follow=True)
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
    services.approve_activity_certificates(director, activity)  # only the resource person is ready
    client = login(director)
    html = client.get(reverse("certificates:list")).content.decode()
    assert "3 certificates" in html and activity.title in html and "is coming soon" not in html
    assert re.search(r'Approved</span>\s*<span class="font-semibold text-ink">1</span>', html)
    assert re.search(r'Waiting for approval</span>\s*<span class="font-semibold text-ink">2</span>', html)
    html = client.get(reverse("certificates:list"), {"delivery_method": "hard_copy"}, **HTMX).content.decode()
    assert "1 certificate matching the filters" in html and "Dr. Maria Santos" in html and "layouts/app.html" not in html
    html = client.get(reverse("certificates:list"), {"status": "approved", "recipient_type": "resource_person"}).content.decode()
    assert "1 certificate matching the filters" in html
    html = client.get(reverse("certificates:list"), {"status": "nope", "activity": "x"}).content.decode()
    assert "Some filters could not be read and were ignored: activity, status." in html and "3 certificates" in html


def test_certificates_page_empty_state(admin_staff, login) -> None:
    assert "No certificates yet" in login(admin_staff).get(reverse("certificates:list")).content.decode()


# --- Read-only service helpers -----------------------------------------------------------------------------------


def test_status_counts_ignore_the_status_filter_and_fill_zeros(director, generated) -> None:
    activity, people = generated
    services.approve_activity_certificates(director, activity)  # only the resource person is ready
    counts = services.certificate_status_counts({"activity": activity.pk, "status": S.ISSUED})
    assert counts == {S.GENERATED: 2, S.APPROVED: 1, S.ISSUED: 0}
    assert services.certificate_status_counts({"delivery_method": "email"})[S.GENERATED] == 2


def test_list_filters_by_delivery_method(generated) -> None:
    assert services.list_certificates({"delivery_method": "email"}).total == 2
    with pytest.raises(services.CertificateError, match="delivery_method must be one of"):
        services.list_certificates({"delivery_method": "pigeon"})


def test_bulk_refusal_lists_its_problems(director, generated) -> None:
    activity, people = generated
    with pytest.raises(services.CertificateError) as error:
        services.mark_handed_over(director, certificate_ids=[certificate_of(people[0]).pk])
    assert error.value.problems == [f"{certificate_of(people[0]).certificate_number}: only hard-copy certificates are handed over; participant certificates are issued automatically."]
    assert str(error.value).startswith("Nothing was changed. ")


def test_generation_closed_reason_before_conducted(scheduled, design) -> None:
    assert services.generation_closed_reason(scheduled) == services.NOT_CONDUCTED


def test_generation_closed_reason(conducted, design) -> None:
    activity, _ = conducted
    assert services.generation_closed_reason(activity) is None
    design[0].is_active = False
    design[0].save()
    assert services.generation_closed_reason(activity).startswith("Cannot create certificates: there is no active certificate design")
