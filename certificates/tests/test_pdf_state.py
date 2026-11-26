"""Approved but never issued (ASSUMPTIONS 368): the last column says the real reason (approved before the evaluation
rule, PDF failed — Retry, waiting for the background worker, PDF being prepared), a failed render is recorded in plain
words with a Retry button, the daily retry and the 5-minute catch-up pick up approved certificates without a PDF."""

from datetime import timedelta

import pytest
from django.test import Client
from django.urls import reverse
from django.utils import timezone

from accounts import system_status
from activities import tasks as activity_tasks
from certificates import pages, pdf, services
from certificates.models import Certificate
from certificates.pages import tab_url
from conftest import VIEW_ONLY_ROLE

from .conftest import S, appreciation_of, certificate_of

pytestmark = pytest.mark.django_db


def client_for(member) -> Client:
    client = Client()
    client.force_login(member)
    return client


@pytest.fixture
def worker(monkeypatch):
    """worker(True/False): whether qcluster looks running."""

    def set_running(running: bool) -> None:
        tone = system_status.OK if running else system_status.WARNING
        monkeypatch.setattr(system_status, "qcluster_status", lambda: system_status.StatusItem("q", "q", tone, ""))

    set_running(True)
    return set_running


def sent_column(client, activity, certificate) -> str:
    html = client.get(tab_url(activity.pk)).content.decode()
    row = html.split(f">{certificate.certificate_number}</a></th>")[1].split("</tr>")[0]  # a preview link (457)
    return row


def approve_before_the_rule(certificate, member) -> None:
    """As the demo seed did on Oct 23: Approved without an evaluation (impossible since item 362)."""
    Certificate.objects.filter(pk=certificate.pk).update(status=S.APPROVED, approved_by=member, approved_at=timezone.now())


def test_approved_before_the_evaluation_rule_while_the_evaluation_is_open(generated, director, clock, worker) -> None:
    activity, people = generated
    clock(timezone.localtime(activity.evaluation_deadline) - timedelta(days=1))
    certificate = certificate_of(people[0])
    approve_before_the_rule(certificate, director)
    certificate.refresh_from_db()
    assert services.pdf_state(certificate, evaluated=False, worker_running=lambda: True) == services.PDF_PRE_RULE_WAITING
    assert "Approved before the evaluation rule — waiting for the evaluation" in sent_column(client_for(director), activity, certificate)


def test_approved_before_the_evaluation_rule_after_the_evaluation_closed(generated, director, clock, worker) -> None:
    activity, people = generated
    clock(timezone.localtime(activity.evaluation_deadline) + timedelta(days=1))
    certificate = certificate_of(people[0])
    approve_before_the_rule(certificate, director)
    row = sent_column(client_for(director), activity, certificate)
    assert "Approved before the evaluation rule — evaluation closed without an answer" in row
    assert Certificate.objects.get(pk=certificate.pk).status == S.APPROVED  # stays Approved (owner decision)


def test_pdf_being_prepared_and_waiting_for_the_worker(generated, director, worker) -> None:
    activity, _ = generated
    speaker = appreciation_of(activity)
    services.approve_activity_certificates(director, activity)  # only the resource person is ready  # its render is queued (not run: no qcluster in tests)
    assert "PDF being prepared" in sent_column(client_for(director), activity, speaker)
    worker(False)
    assert "Waiting for the background worker" in sent_column(client_for(director), activity, speaker)


def test_a_failed_render_is_recorded_in_plain_words_with_retry(generated, director, worker, fake_pdf_renderer, run_tasks,
                                                               django_capture_on_commit_callbacks) -> None:
    activity, _ = generated
    speaker = appreciation_of(activity)
    fake_pdf_renderer.fail = OSError("cannot load library 'libgobject-2.0-0': C:\\secret\\path")
    with django_capture_on_commit_callbacks(execute=True):
        services.approve_activity_certificates(director, activity)  # only the resource person is ready
    speaker.refresh_from_db()
    assert speaker.render_failed_at is not None and "WeasyPrint" in speaker.render_error
    assert "secret" not in speaker.render_error  # never the raw error
    row = sent_column(client_for(director), activity, speaker)
    assert "PDF failed" in row and "WeasyPrint" in row and f'form="retry-{speaker.pk}"' in row

    fake_pdf_renderer.fail = None
    with django_capture_on_commit_callbacks(execute=True):
        response = client_for(director).post(reverse("activities:certificates_action", args=[activity.pk]),
                                             {"action": "retry_pdf", "certificate_ids": [speaker.pk]})
    assert response.status_code == 302
    speaker.refresh_from_db()
    assert speaker.file_path and speaker.render_failed_at is None and speaker.render_error is None


def test_an_unreadable_signature_is_named_in_the_error(generated, director) -> None:
    activity, _ = generated
    speaker = appreciation_of(activity)
    services.approve_activity_certificates(director, activity)  # only the resource person is ready
    head = services.activity_signatories(activity)["left"]
    head.signature_image_path.storage.delete(head.signature_image_path.name)
    with pytest.raises(pdf.ImageUnreadable) as error:
        services.prepare_certificate(speaker.pk)
    services.record_render_failure(speaker.pk, error.value)
    speaker.refresh_from_db()
    assert speaker.render_error == "The signature image of Engr. Dee Rector could not be read."


def test_retry_needs_the_approval_flag(generated, director, make_member, fake_pdf_renderer) -> None:
    activity, _ = generated
    speaker = appreciation_of(activity)
    services.approve_activity_certificates(director, activity)  # only the resource person is ready
    services.record_render_failure(speaker.pk, RuntimeError("boom"))
    viewer = make_member(VIEW_ONLY_ROLE, email="viewer@example.com")
    response = client_for(viewer).post(reverse("activities:certificates_action", args=[activity.pk]),
                                       {"action": "retry_pdf", "certificate_ids": [speaker.pk]})
    assert response.status_code == 403
    assert f'form="retry-{speaker.pk}"' not in sent_column(client_for(viewer), activity, speaker)
    assert "PDF failed" in sent_column(client_for(viewer), activity, speaker)  # everyone sees the reason


def test_the_daily_retry_picks_up_failed_and_never_queued_pdfs(generated, director, fake_pdf_renderer) -> None:
    activity, _ = generated
    speaker = appreciation_of(activity)
    services.approve_activity_certificates(director, activity)  # only the resource person is ready
    services.record_render_failure(speaker.pk, RuntimeError("boom"))
    assert speaker.pk in set(services.pending_preparation().values_list("pk", flat=True))
    result = services.retry_pending_certificates()
    speaker.refresh_from_db()
    assert result["prepared"] == 1 and speaker.file_path and speaker.render_error is None


def test_the_daily_retry_records_a_failure(generated, director, fake_pdf_renderer) -> None:
    activity, _ = generated
    speaker = appreciation_of(activity)
    services.approve_activity_certificates(director, activity)  # only the resource person is ready
    fake_pdf_renderer.fail = RuntimeError("boom")
    assert services.retry_pending_certificates()["prepare_failed"] == 1
    speaker.refresh_from_db()
    assert speaker.render_error.startswith("The PDF could not be created (RuntimeError).")


def test_the_five_minute_job_queues_a_stalled_render_after_ten_minutes(generated, director, monkeypatch) -> None:
    activity, _ = generated
    speaker = appreciation_of(activity)
    queued: list[int] = []
    monkeypatch.setattr(services, "queue_prepare", queued.append)
    now = timezone.now()
    services.approve_activity_certificates(director, activity, now=now)  # only the resource person is ready
    assert services.queue_stalled_preparations(now + timedelta(minutes=5)) == 0
    assert services.queue_stalled_preparations(now + timedelta(minutes=11)) == 1 and queued == [speaker.pk]
    services.record_render_failure(speaker.pk, RuntimeError("boom"))
    assert services.queue_stalled_preparations(now + timedelta(minutes=30)) == 0  # failed ones wait for Retry
    assert "pdfs_queued" in activity_tasks.run_activity_automation()


def test_pre_rule_certificates_are_not_rendered_by_the_retry(generated, director) -> None:
    activity, people = generated
    certificate = certificate_of(people[0])
    approve_before_the_rule(certificate, director)
    assert certificate.pk not in set(services.pending_preparation().values_list("pk", flat=True))
    certificate.refresh_from_db()
    assert certificate.status == S.APPROVED  # stays Approved (owner decision)


def test_labels_cover_every_state() -> None:
    assert set(pages.PDF_STATE_LABELS) == {services.PDF_PRE_RULE_WAITING, services.PDF_PRE_RULE_CLOSED,
                                           services.PDF_FAILED, services.PDF_WORKER, services.PDF_PREPARING}
