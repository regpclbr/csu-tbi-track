"""Issuing and emailing: Approved AND evaluated (whichever happens last), the name and address from
the evaluation, never twice, sent_at only on success, retries, hard copies never emailed."""

import threading
import time
from pathlib import Path

import pytest
from django.conf import settings
from django.core import mail
from django.db import connection
from django.utils import timezone
from django_q.models import OrmQ, Schedule

from certificates import emails, services, tasks
from certificates.models import Certificate

from .conftest import S, appreciation_of, certificate_of

pytestmark = pytest.mark.django_db


def certificate_files() -> list[Path]:
    folder = Path(settings.MEDIA_ROOT) / "certificates"
    return sorted(p for p in folder.rglob("*") if p.is_file()) if folder.exists() else []


def assert_emailed(certificate: Certificate, name: str, address: str, fake_pdf_renderer) -> None:
    certificate.refresh_from_db()
    assert certificate.status == S.ISSUED and certificate.issued_at and certificate.sent_at
    assert certificate.evaluation_submission is not None  # linked when issued (item 42)
    [message] = [m for m in mail.outbox if m.to == [address]]
    filename, content, mimetype = message.attachments[0]
    assert filename == f"{certificate.certificate_number}.pdf" and mimetype == "application/pdf"
    assert content.startswith(b"%PDF-") and name.encode() in content  # the fake PDF carries its HTML
    assert certificate.file_path.name == f"certificates/{certificate.certificate_number.split('-')[1]}/{certificate.certificate_number}.pdf"


# --- Both orders ------------------------------------------------------------------------------------


def test_evaluation_after_approval_issues_and_emails(generated, approve_all, evaluate, run_tasks, fake_pdf_renderer, django_capture_on_commit_callbacks) -> None:
    activity, people = generated
    with django_capture_on_commit_callbacks(execute=True):
        approve_all(activity)
    certificate = certificate_of(people[0])
    assert certificate.status == S.APPROVED and not certificate.file_path  # no evaluation yet: nothing rendered
    assert mail.outbox == []

    with django_capture_on_commit_callbacks(execute=True):
        evaluate(people[0], name="Ma. Ana  dela Cruz-Reyes", email=" Ana.Personal@Example.COM ")
    assert_emailed(certificate, "Ma. Ana  dela Cruz-Reyes", "ana.personal@example.com", fake_pdf_renderer)
    assert "Ma. Ana  dela Cruz-Reyes" in fake_pdf_renderer.last  # exactly as typed (only outer spaces trimmed)
    assert "Ana Cruz" not in fake_pdf_renderer.last  # never the registration name
    assert certificate_of(people[1]).status == S.APPROVED  # the other participant has not evaluated


def test_evaluation_before_approval_is_emailed_right_after_approval(generated, approve_all, evaluate, run_tasks, fake_pdf_renderer, django_capture_on_commit_callbacks) -> None:
    activity, people = generated
    with django_capture_on_commit_callbacks(execute=True):
        evaluate(people[1], name="Ben Santos", email="ben.cert@example.com")
    assert certificate_of(people[1]).status == S.GENERATED and mail.outbox == []  # not approved yet: nothing happens

    with django_capture_on_commit_callbacks(execute=True):
        approve_all(activity)
    assert_emailed(certificate_of(people[1]), "Ben Santos", "ben.cert@example.com", fake_pdf_renderer)
    assert len(mail.outbox) == 1  # people[0] has not evaluated: no email for them


def test_printed_name_is_the_evaluation_name_not_the_registration_name(generated, approve_all, evaluate, run_tasks, fake_pdf_renderer, django_capture_on_commit_callbacks) -> None:
    activity, people = generated
    with django_capture_on_commit_callbacks(execute=True):
        approve_all(activity)
        evaluate(people[0], name="Juana Dela Cruz", email="juana@example.com")
    html = fake_pdf_renderer.last
    assert "Juana Dela Cruz" in html and "Ana Cruz" not in html
    assert html.count("data:image/png;base64,") == 3  # background + both signatures
    assert "PREVIEW" not in html


def test_approval_queues_tasks_after_commit(generated, approve_all, evaluate, monkeypatch, django_capture_on_commit_callbacks) -> None:
    """Without the shortcut, real Django-Q2 tasks are queued (qcluster runs them)."""
    activity, people = generated
    evaluate(people[0])
    with django_capture_on_commit_callbacks(execute=True):
        approve_all(activity)
    queued = OrmQ.objects.count()
    assert queued == 2  # people[0] (evaluated) and the resource person; not people[1]


# --- Resource persons: rendered on approval, handed over, never emailed --------------------------------


def test_resource_person_rendered_on_approval_never_emailed(generated, approve_all, director, run_tasks, fake_pdf_renderer, django_capture_on_commit_callbacks) -> None:
    activity, _ = generated
    with django_capture_on_commit_callbacks(execute=True):
        approve_all(activity)
    certificate = appreciation_of(activity)
    assert certificate.status == S.APPROVED and certificate.file_path
    assert "Dr. Maria Santos" in fake_pdf_renderer.last_text() and "Pitch Decks" in fake_pdf_renderer.last_text()
    services.mark_handed_over(director, certificate_ids=[certificate.pk])
    assert services.send_certificate(certificate.pk) == "hard copy: never emailed"
    certificate.refresh_from_db()
    assert certificate.sent_at is None and mail.outbox == []


# --- Never twice; sent_at only on success ------------------------------------------------------------


@pytest.fixture
def issued(generated, approve_all, evaluate, monkeypatch, django_capture_on_commit_callbacks) -> Certificate:
    """people[0]'s certificate, Issued with its PDF, but the email not sent yet."""
    activity, people = generated
    monkeypatch.setattr(services, "queue_send", lambda *args: None)
    monkeypatch.setattr(services, "queue_prepare", services.prepare_certificate)
    with django_capture_on_commit_callbacks(execute=True):
        approve_all(activity)
        evaluate(people[0], name="Ana Cruz", email="ana.cert@example.com")
    certificate = certificate_of(people[0])
    assert certificate.status == S.ISSUED and certificate.sent_at is None
    return certificate


def test_never_sent_twice(issued) -> None:
    assert services.send_certificate(issued.pk) == "sent"
    assert services.send_certificate(issued.pk) == "already sent"
    assert len(mail.outbox) == 1


def test_sent_at_only_after_a_successful_send(issued, monkeypatch) -> None:
    def fail(*args):
        raise ConnectionError("SMTP server unreachable")

    monkeypatch.setattr(emails, "send_certificate_email", fail)
    result = tasks.run_send_certificate(issued.pk)
    assert result["result"] == "failed"
    issued.refresh_from_db()
    assert issued.sent_at is None and issued.status == S.ISSUED


def test_failed_send_is_retried_with_backoff(issued, monkeypatch) -> None:
    monkeypatch.setattr(emails, "send_certificate_email", lambda *args: (_ for _ in ()).throw(ConnectionError("down")))
    for attempt, minutes in enumerate(settings.CERTIFICATE_EMAIL_RETRY_MINUTES, start=1):
        tasks.run_send_certificate(issued.pk, attempt)
        retry = Schedule.objects.order_by("-pk").first()
        assert retry.schedule_type == Schedule.ONCE and retry.func == "certificates.tasks.run_send_certificate"
        assert retry.args == f"({issued.pk}, {attempt + 1})"
        assert abs((retry.next_run - timezone.now()).total_seconds() - minutes * 60) < 60
    count = Schedule.objects.count()
    tasks.run_send_certificate(issued.pk, len(settings.CERTIFICATE_EMAIL_RETRY_MINUTES) + 1)
    assert Schedule.objects.count() == count  # no more one-off retries: the daily retry takes over


def test_daily_retry_sends_issued_and_unsent(issued) -> None:
    assert services.retry_pending_certificates()["sent"] == 1
    issued.refresh_from_db()
    assert issued.sent_at is not None and len(mail.outbox) == 1
    assert services.retry_pending_certificates()["sent"] == 0  # nothing left


@pytest.mark.django_db(transaction=True)
def test_never_sent_twice_under_concurrency(director, conducted, design, approve_all, evaluate, monkeypatch) -> None:
    """Two send tasks at the same moment: the row lock makes the second wait, then skip."""
    activity, people = conducted
    services.generate_certificates(director, activity)
    monkeypatch.setattr(services, "queue_send", lambda *args: None)
    monkeypatch.setattr(services, "queue_prepare", services.prepare_certificate)
    approve_all(activity)  # transaction=True: on_commit callbacks run for real
    evaluate(people[0])
    certificate = certificate_of(people[0])
    assert certificate.status == S.ISSUED

    real_send = emails.send_certificate_email

    def slow_send(*args):
        time.sleep(0.5)  # the second task arrives while the first is still sending
        real_send(*args)

    monkeypatch.setattr(emails, "send_certificate_email", slow_send)
    start = threading.Barrier(2)
    results: list[str] = []

    def send() -> None:
        try:
            start.wait()
            results.append(services.send_certificate(certificate.pk))
        finally:
            connection.close()

    threads = [threading.Thread(target=send) for _ in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert sorted(results) == ["already sent", "sent"]
    assert len([m for m in mail.outbox if m.to == ["ana.cert@example.com"]]) == 1


# --- Failed render: never a half-written file, retried -----------------------------------------------


def test_failed_render_leaves_no_file_and_is_retried(generated, approve_all, evaluate, run_tasks, fake_pdf_renderer, django_capture_on_commit_callbacks) -> None:
    activity, people = generated
    fake_pdf_renderer.fail = OSError("cannot load library 'libgobject-2.0-0'")
    with django_capture_on_commit_callbacks(execute=True):
        approve_all(activity)
        evaluate(people[0])
    certificate = certificate_of(people[0])
    assert certificate.status == S.APPROVED and not certificate.file_path and certificate.issued_at is None
    assert certificate_files() == [] and mail.outbox == []

    fake_pdf_renderer.fail = None
    with django_capture_on_commit_callbacks(execute=True):
        result = services.retry_pending_certificates()
    assert result["prepared"] == 2  # people[0] and the resource person
    certificate.refresh_from_db()
    assert certificate.status == S.ISSUED and certificate.sent_at and len(mail.outbox) == 1


def test_failed_save_removes_the_written_file(generated, approve_all, evaluate, monkeypatch, django_capture_on_commit_callbacks) -> None:
    activity, people = generated
    approve_all(activity)
    evaluate(people[0])
    certificate = certificate_of(people[0])

    def broken(*args, **kwargs):
        raise services.CertificateStatusError("database error")

    monkeypatch.setattr(services, "set_certificate_status", broken)
    with pytest.raises(services.CertificateStatusError):
        services.prepare_certificate(certificate.pk)
    certificate.refresh_from_db()
    assert not certificate.file_path and certificate.status == S.APPROVED
    assert certificate_files() == []  # neither the PDF nor a temporary file is left


def test_prepare_twice_renders_once(generated, approve_all, evaluate, monkeypatch, fake_pdf_renderer) -> None:
    activity, people = generated
    monkeypatch.setattr(services, "queue_send", lambda *args: None)
    approve_all(activity)
    evaluate(people[0])
    certificate = certificate_of(people[0])
    assert services.prepare_certificate(certificate.pk) == "issued"
    assert services.prepare_certificate(certificate.pk) == "not Approved"  # already Issued: nothing to do
    assert len(certificate_files()) == 1


# --- Preview ------------------------------------------------------------------------------------------


def test_preview_looks_like_the_certificate_with_a_corner_mark(generated, director, fake_pdf_renderer, client) -> None:
    """ASSUMPTIONS 358: same template as the final PDF, both signatures, a small PREVIEW mark while waiting."""
    certificate = certificate_of(generated[1][0])
    content = services.preview_pdf(director, certificate)
    html = fake_pdf_renderer.last
    assert content.startswith(b"%PDF-") and 'class="preview-mark">PREVIEW<' in html and "NOT VALID" not in html
    assert html.count("data:image/png;base64,") == 3  # background + both signatures
    assert "Ana Cruz" in html and "(name from evaluation)" not in html
    assert certificate_files() == []  # never stored

    client.force_login(director)
    response = client.get(f"/certificates/{certificate.pk}/preview/")
    assert response.status_code == 200 and response["Content-Type"] == "application/pdf"
    assert response["Content-Disposition"].startswith("inline")  # F4: the preview opens in a new tab
    assert response["X-Content-Type-Options"] == "nosniff"


def test_preview_of_a_resource_person_shows_their_name(generated, director, fake_pdf_renderer) -> None:
    services.preview_pdf(director, appreciation_of(generated[0]))
    assert "Dr. Maria Santos" in fake_pdf_renderer.last and "(name from evaluation)" not in fake_pdf_renderer.last


def test_preview_explains_a_rendering_problem(generated, director, fake_pdf_renderer, client) -> None:
    fake_pdf_renderer.fail = OSError("cannot load library")
    client.force_login(director)
    response = client.get(f"/certificates/{appreciation_of(generated[0]).pk}/preview/")
    assert response.status_code == 503 and "check_pdf" in response.content.decode()  # plain text in the iframe


# --- Demo addresses are never really emailed ------------------------------------------------------------


@pytest.mark.parametrize("debug", [True, False])
def test_demo_certificate_email_is_never_sent_by_smtp(issued, settings, monkeypatch, capsys, debug) -> None:
    from django.core.mail.backends.smtp import EmailBackend as SMTPBackend

    sent_by_smtp = []
    monkeypatch.setattr(SMTPBackend, "send_messages", lambda self, messages: sent_by_smtp.extend(messages) or len(messages))
    settings.MAILERS = {"default": settings.SMTP_MAILER}  # real sending switched on
    settings.DEBUG = debug
    issued.evaluation_submission.__class__.objects.filter(pk=issued.evaluation_submission_id).update(
        certificate_email="someone@demo.example.com"
    )
    assert services.send_certificate(issued.pk) == "sent"
    assert sent_by_smtp == []
    out = capsys.readouterr().out
    if debug:
        assert "To: someone@demo.example.com" in out and "[attachment TBI-" in out  # printed, PDF summarized
    else:
        assert out == ""  # a real server never prints the email (only that one was blocked is logged)
