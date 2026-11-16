"""Issued with an old signature and reissue (ASSUMPTIONS 364): computed outdated state, who may reissue, the reissue
itself (same number, still Issued, new PDF, "Updated copy" email / reprint), failures and Resend, qcluster not
running, the notices, and Needs attention (informational: never counted on the list or the dashboard)."""

import pytest
from django.core import mail
from django.core.exceptions import PermissionDenied
from django.test import Client
from django.urls import reverse

from activities import attention
from activities import pages as activity_pages
from activities.models import ActivityAuditLog
from certificates import emails, services, tasks
from certificates.models import Certificate, CertificateSignatory
from certificates.pages import tab_url

from .conftest import S, appreciation_of, certificate_of, upload
from conftest import image_bytes

pytestmark = pytest.mark.django_db


def client_for(member) -> Client:
    client = Client()
    client.force_login(member)
    return client


def png() -> object:
    return upload("sig.png", image_bytes(40, 20))


@pytest.fixture
def issued(generated, evaluate, approve_all, run_tasks, director, django_capture_on_commit_callbacks):
    """people[0] evaluated, issued and emailed; the resource person approved and handed over (Issued, hard copy);
    people[1] still waiting for the evaluation."""
    activity, people = generated
    with django_capture_on_commit_callbacks(execute=True):
        evaluate(people[0])
        approve_all(activity)
    services.mark_handed_over(director, activity=activity)
    mail.outbox.clear()
    run_tasks.clear()
    return activity, people


@pytest.fixture
def president(director) -> CertificateSignatory:
    return services.add_signatory(director, "Dr. Ana Reyes", "University President", png())


@pytest.fixture
def outdated(issued, president, director):
    """The activity's right signatory changed to the University President: both Issued certificates are outdated."""
    activity, people = issued
    services.set_activity_signatory(director, activity, "right", president)
    return activity, people


# --- Computed: "Issued with an old signature" ---------------------------------------------------------------------


def test_nothing_is_outdated_while_the_signatories_are_current(issued) -> None:
    activity, _ = issued
    assert services.outdated_ids(activity) == set()


def test_a_slot_change_makes_issued_certificates_outdated(outdated, design) -> None:
    activity, people = outdated
    participant, speaker = certificate_of(people[0]), appreciation_of(activity)
    assert services.outdated_ids(activity) == {participant.pk, speaker.pk}
    assert services.outdated_counts(activity) == {"total": 2, "by_email": 1, "by_hand": 1}
    assert (participant.status, participant.right_signatory_id) == (S.ISSUED, design[1].pk)  # untouched


def test_a_replaced_image_makes_issued_certificates_outdated(issued, design, director) -> None:
    activity, _ = issued
    services.replace_signature(director, design[2], png())  # the Director (left)
    assert len(services.outdated_ids(activity)) == 2


def test_list_and_preview_show_the_outdated_state(outdated, director, view_only) -> None:
    activity, people = outdated
    participant = certificate_of(people[0])
    for member in (director, view_only):
        html = client_for(member).get(tab_url(activity.pk)).content.decode()
        assert "Issued with an old signature" in html
    page = client_for(director).get(reverse("certificates:preview_dialog", args=[participant.pk])).content.decode()
    assert "Issued with an old signature: this copy was printed with a signatory" in page
    assert "Reissue with current signatures" in page
    listing = client_for(director).get(reverse("certificates:list")).content.decode()
    assert "Issued with an old signature" in listing


# --- Who may reissue -----------------------------------------------------------------------------------------------


def test_only_certificate_approvers_reissue(outdated, view_only, make_member) -> None:
    from accounts import services as account_services
    from accounts.models import Role

    activity, people = outdated
    creator = make_member("Incubation Project Head", email="iph.reissue@example.com")
    admin = make_member(Role.SYSTEM_ADMINISTRATOR, email="sysadmin.reissue@example.com")
    account_services.set_role_certificate_approval(admin, creator.role, False)
    creator.refresh_from_db()
    for member in (view_only, creator):
        with pytest.raises(PermissionDenied):
            services.reissue_certificates(member, activity=activity)
        client = client_for(member)
        assert client.post(reverse("activities:certificates_reissue_all", args=[activity.pk])).status_code == 403
        assert client.post(reverse("activities:certificates_reissue",
                                   args=[activity.pk, certificate_of(people[0]).pk])).status_code == 403
        html = client.get(tab_url(activity.pk)).content.decode()
        assert "Reissue all outdated certificates" not in html and "Reissue with current signatures" not in html
    assert services.outdated_ids(activity)  # nothing changed


def test_approvers_see_the_button_with_the_counts(outdated, director) -> None:
    activity, _ = outdated
    html = client_for(director).get(tab_url(activity.pk)).content.decode()
    assert "Reissue all outdated certificates (2)" in html
    assert "1 will be emailed again to the participant" in html and "1 hard copy needs reprinting" in html


# --- Reissuing ------------------------------------------------------------------------------------------------------


def test_reissue_participant_emails_an_updated_copy(outdated, president, director, run_tasks,
                                                    django_capture_on_commit_callbacks) -> None:
    activity, people = outdated
    certificate = certificate_of(people[0])
    number, file_name, sent_before = certificate.certificate_number, certificate.file_path.name, certificate.sent_at
    with django_capture_on_commit_callbacks(execute=True):
        services.reissue_certificate(director, certificate)
    certificate.refresh_from_db()
    assert certificate.certificate_number == number and certificate.status == S.ISSUED
    assert certificate.right_signatory == president and certificate.reissued_at is not None
    assert certificate.file_path.name == file_name  # the new PDF replaced the old one at the same name
    assert certificate.sent_at is not None and certificate.sent_at >= sent_before and certificate.send_failed_at is None
    [message] = mail.outbox
    assert message.subject.startswith("Updated copy: Your Certificate of Participation")
    assert "It replaces the copy we sent you earlier." in message.body
    assert message.to == ["ana.cert@example.com"]  # the evaluation's certificate_email
    assert run_tasks == [("prepare", certificate.pk), ("send", certificate.pk)]
    assert certificate.pk not in services.outdated_ids(activity)
    row = ActivityAuditLog.objects.filter(activity=activity, field_changed="Certificates").latest("pk")
    assert row.changed_by == director and row.new_value == f"Reissued with the current signatures: {number}"


def test_reissue_resource_person_needs_a_new_hand_over(outdated, director, run_tasks,
                                                       django_capture_on_commit_callbacks) -> None:
    activity, _ = outdated
    speaker = appreciation_of(activity)
    with django_capture_on_commit_callbacks(execute=True):
        services.reissue_certificate(director, speaker)
    speaker.refresh_from_db()
    assert speaker.status == S.ISSUED and speaker.file_path and mail.outbox == []  # never emailed
    assert services.awaiting_hand_over(speaker)
    html = client_for(director).get(tab_url(activity.pk)).content.decode()
    assert "Not handed over (updated copy to print)" in html and "Mark handed over" in html
    texts = [item.text for item in attention.activity_attention(activity)]
    assert "1 certificate for resource persons reissued: updated copy to print and hand over." in texts
    services.mark_handed_over(director, certificate_ids=[speaker.pk])
    speaker.refresh_from_db()
    assert speaker.status == S.ISSUED and not services.awaiting_hand_over(speaker)
    with pytest.raises(services.CertificateError, match="already handed over"):
        services.mark_handed_over(director, certificate_ids=[speaker.pk])


def test_reissue_all_outdated(outdated, director, run_tasks, django_capture_on_commit_callbacks) -> None:
    activity, _ = outdated
    with django_capture_on_commit_callbacks(execute=True):
        response = client_for(director).post(reverse("activities:certificates_reissue_all", args=[activity.pk]), follow=True)
    assert [str(m) for m in response.context["messages"]][0].startswith(
        "Reissued 2 certificates with the current signatures. 1 will be emailed again once the new PDF is ready; "
        "1 hard copy needs reprinting")
    assert services.outdated_ids(activity) == set() and len(mail.outbox) == 1
    with pytest.raises(services.CertificateError, match="No issued certificates of this activity have an old signature"):
        services.reissue_certificates(director, activity=activity)


def test_reissue_refusals(outdated, issued, president, director, design) -> None:
    activity, people = outdated
    waiting = certificate_of(people[1])
    with pytest.raises(services.CertificateError, match="only Issued"):
        services.reissue_certificates(director, certificate_ids=[waiting.pk])
    CertificateSignatory.objects.filter(pk=president.pk).update(signature_image_path=None)
    with pytest.raises(services.CertificateError, match=r"Add the signature of Dr\. Ana Reyes"):
        services.reissue_certificates(director, activity=activity)
    html = client_for(director).get(tab_url(activity.pk)).content.decode()
    assert "Reissue: Add the signature of Dr. Ana Reyes (University President) first." in html
    assert services.outdated_ids(activity)  # all or nothing: nothing changed


def test_not_outdated_is_refused(issued, director) -> None:
    activity, people = issued
    with pytest.raises(services.CertificateError, match="already has the current signatures"):
        services.reissue_certificates(director, certificate_ids=[certificate_of(people[0]).pk])


def test_failed_updated_copy_email_and_resend(outdated, director, run_tasks, monkeypatch,
                                              django_capture_on_commit_callbacks) -> None:
    activity, people = outdated
    certificate = certificate_of(people[0])

    real_send, broken = emails.send_certificate_email, [True]

    def send(*args):
        if broken:
            raise ConnectionError("SMTP server unreachable")
        real_send(*args)

    monkeypatch.setattr(emails, "send_certificate_email", send)
    with django_capture_on_commit_callbacks(execute=True):
        services.reissue_certificate(director, certificate)
    certificate.refresh_from_db()
    assert certificate.sent_at is None and certificate.send_failed_at is not None and certificate.file_path
    html = client_for(director).get(tab_url(activity.pk)).content.decode()
    assert "Send failed" in html and "Resend" in html
    broken.clear()  # the mail server works again
    with django_capture_on_commit_callbacks(execute=True):
        assert services.resend_certificates(director, certificate_ids=[certificate.pk]) == 1
    certificate.refresh_from_db()
    assert certificate.sent_at is not None and certificate.send_failed_at is None
    assert mail.outbox[-1].subject.startswith("Updated copy:")


def test_qcluster_not_running(outdated, director, monkeypatch, django_capture_on_commit_callbacks) -> None:
    """Reissue only queues the work: until qcluster runs, the certificate waits with no file, the preview is drawn
    live, nothing is emailed, Resend refuses, and the tab says the worker is not running."""
    from accounts import system_status

    activity, people = outdated
    certificate = certificate_of(people[0])
    queued = []
    monkeypatch.setattr(services, "queue_prepare", queued.append)  # nobody runs the queue
    with django_capture_on_commit_callbacks(execute=True):
        services.reissue_certificate(director, certificate)
    certificate.refresh_from_db()
    assert queued == [certificate.pk] and not certificate.file_path and mail.outbox == []
    assert services.updated_copy_pending(certificate) and not services.shows_final_copy(certificate)
    assert services.send_certificate(certificate.pk) == "PDF not ready (updated copy being prepared)"
    with pytest.raises(services.CertificateError, match="still being prepared"):
        services.resend_certificates(director, certificate_ids=[certificate.pk])
    assert certificate.pk in services.pending_preparation().values_list("pk", flat=True)
    assert certificate.pk not in services.pending_emails().values_list("pk", flat=True)
    monkeypatch.setattr(system_status, "qcluster_status", lambda: system_status.StatusItem("q", "q", system_status.WARNING, ""))
    html = client_for(director).get(tab_url(activity.pk)).content.decode()
    assert "Certificates are waiting for the background worker." in html and "Updated copy being prepared" in html
    response = client_for(director).get(reverse("certificates:preview", args=[certificate.pk]))
    assert response.status_code == 200 and response.content.startswith(b"%PDF-")  # drawn now (FakeRenderer)

    # The daily retry (or qcluster later) finishes it.
    result = services.retry_pending_certificates()
    certificate.refresh_from_db()
    assert result["prepared"] == 1 and result["sent"] == 1 and certificate.file_path and certificate.sent_at
    assert mail.outbox[0].subject.startswith("Updated copy:")


def test_the_api_reissues_for_approvers_only(outdated, director, view_only) -> None:
    activity, _ = outdated
    url = reverse("certificates:api_action", args=["reissue"])
    assert client_for(view_only).post(url, {"activity_id": activity.pk}, content_type="application/json").status_code == 403
    response = client_for(director).post(url, {"activity_id": activity.pk}, content_type="application/json")
    assert response.status_code == 200 and response.json() == {"changed": 2}


# --- Notices and Needs attention (addition B) ----------------------------------------------------------------------


def test_notice_after_a_signatory_change(issued, president, director) -> None:
    activity, _ = issued
    response = client_for(director).post(reverse("activities:certificates_signatory", args=[activity.pk, "right"]),
                                         {"signatory": president.pk}, follow=True)
    text = " ".join(str(m) for m in response.context["messages"])
    assert "2 issued certificates still have the old signature. Reissue them from the Certificates tab." in text


def test_notice_after_an_image_replacement_counts_every_activity(issued, design, director) -> None:
    activity, _ = issued
    response = client_for(director).post(reverse("certificates:signatory_image", args=[design[1].pk]),
                                         {"file": png()}, follow=True)
    text = " ".join(str(m) for m in response.context["messages"])
    assert "2 issued certificates still have the old signature. Reissue them from the Certificates tab." in text


def test_old_signature_line_is_informational(outdated, director) -> None:
    """Addition B: the line shows in the activity's Needs attention box, but never in the Activities list's warning
    count (nor on the dashboard)."""
    activity, _ = outdated
    items = attention.activity_attention(activity)
    line = next(item for item in items if item.text == "2 issued certificates have an old signature.")
    assert line.informational and line.tab == "certificates"
    assert attention.warning_count(items) == len(items) - 1
    assert attention.attention_counts([activity])[activity.pk] == len(items) - 1
    from django.db.models import Count

    from activities.models import Activity

    row = activity_pages.list_rows([Activity.objects.annotate(registered=Count("registrations")).get(pk=activity.pk)])[0]
    assert row["attention"] == len(items) - 1

    # The activity page shows it (marked for information); the count excludes it.
    html = client_for(director).get(reverse("activities:detail", args=[activity.pk])).content.decode()
    assert "2 issued certificates have an old signature." in html and "(for information)" in html
    dashboard = client_for(director).get("/").content.decode()
    assert "old signature" not in dashboard


def test_the_line_disappears_after_reissue(outdated, director) -> None:
    activity, people = outdated
    services.reissue_certificates(director, activity=activity)
    assert services.outdated_ids(activity) == set()
    texts = [item.text for item in attention.activity_attention(activity)]
    assert "2 issued certificates have an old signature." not in texts
