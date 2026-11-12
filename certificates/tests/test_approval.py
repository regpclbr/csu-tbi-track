"""Certificate approval (ASSUMPTIONS 357, 361, 362): who may approve, approve one or all ready certificates of an
activity, the missing signature image, apply the current design (approve again), hand-over, failed send and resend. More
in test_signatures.py and test_automatic_flow.py. Participants are approved only after their evaluation (ready)."""

import logging
from pathlib import Path

import pytest
from django.conf import settings
from django.core import mail
from django.core.exceptions import PermissionDenied

from accounts.models import Role
from certificates import emails, services, tasks
from certificates.models import Certificate, CertificateSignatory

from .conftest import S, appreciation_of, certificate_of, template_upload, upload
from conftest import image_bytes

pytestmark = pytest.mark.django_db

MISSING_VP = "Add the VP's signature first."
MISSING_DIRECTOR = "Add the Director's signature first."


def ids(*certificates: Certificate) -> list[int]:
    return [c.pk for c in certificates]


def remove_signature_file(signatory: CertificateSignatory) -> None:
    (Path(settings.MEDIA_ROOT) / signatory.signature_image_path.name).unlink()


# --- Who may do what ---------------------------------------------------------------------------------


def test_admin_staff_cannot_approve(generated, make_member) -> None:
    activity, _ = generated
    member = make_member("Admin Staff")
    with pytest.raises(PermissionDenied, match="approve certificates"):
        services.approve_certificates(member, activity=activity)
    with pytest.raises(PermissionDenied):
        services.approve_certificates(member, certificate_ids=list(Certificate.objects.values_list("pk", flat=True)))
    assert set(Certificate.objects.values_list("status", flat=True)) == {S.GENERATED}


def test_admin_staff_alone_lacks_the_flag(roles) -> None:
    assert {name for name, role in roles.items() if not role.can_approve_certificates} == {"Admin Staff"}


@pytest.mark.parametrize("role_name", ["Admin Staff", Role.SYSTEM_ADMINISTRATOR, "Incubation Project Head"])
def test_regenerate_hand_over_and_resend_need_can_create_activity(generated, make_member, role_name) -> None:
    activity, _ = generated
    member = make_member(role_name)
    if member.role.can_create_activity:
        assert services.regenerate_certificates(member, activity=activity) == 3
        return
    for action in (services.regenerate_certificates, services.mark_handed_over, services.resend_certificates):
        with pytest.raises(PermissionDenied):
            action(member, activity=activity)


# --- Approve all for this activity ---------------------------------------------------------------------


@pytest.fixture
def evaluated(generated, evaluate):
    """Both participants evaluated: every certificate is ready for approval."""
    activity, people = generated
    for n, person in enumerate(people):
        evaluate(person, name=f"Person {n}", email=f"person{n}.cert@example.com")
    return activity, people


def test_approve_all_for_the_activity(evaluated, director, caplog) -> None:
    activity, _ = evaluated
    assert services.approval_closed_reason(activity) is None
    with caplog.at_level(logging.INFO, logger="certificates"):
        assert services.approve_certificates(director, activity=activity) == 3
    assert f"Member {director.pk} approved certificates" in caplog.text
    for certificate in Certificate.objects.all():
        assert certificate.status == S.APPROVED and certificate.approved_by == director and certificate.approved_at
    with pytest.raises(services.CertificateError, match="No certificates of this activity are waiting for approval"):
        services.approve_certificates(director, activity=activity)
    assert services.approval_closed_reason(activity) == "No certificates of this activity are waiting for approval."


def test_approve_one_then_the_rest(evaluated, director) -> None:
    activity, people = evaluated
    assert services.approve_certificates(director, certificate_ids=ids(certificate_of(people[0]))) == 1
    assert Certificate.objects.filter(status=S.GENERATED).count() == 2
    assert services.approve_certificates(director, activity=activity) == 2  # only the ones still waiting


def test_approving_a_selection_is_all_or_nothing(evaluated, director) -> None:
    _, people = evaluated
    first, second = certificate_of(people[0]), certificate_of(people[1])
    services.approve_certificates(director, certificate_ids=ids(first))
    with pytest.raises(services.CertificateError) as error:
        services.approve_certificates(director, certificate_ids=ids(first, second))
    assert error.value.problems == [f"{first.certificate_number}: Cannot move a certificate from Approved to Approved."]
    second.refresh_from_db()
    assert second.status == S.GENERATED and second.approved_by is None


@pytest.mark.parametrize(("position", "message"), [("vp", MISSING_VP), ("director", MISSING_DIRECTOR)])
def test_a_missing_signature_image_blocks_approval(generated, design, director, position, message) -> None:
    activity, _ = generated
    _, vp, head = design
    remove_signature_file(vp if position == "vp" else head)
    assert services.approval_closed_reason(activity) == message
    with pytest.raises(services.CertificateError) as error:
        services.approve_certificates(director, activity=activity)
    assert str(error.value) == message
    with pytest.raises(services.CertificateError, match="Waiting for signatures"):
        services.approve_certificates(director, certificate_ids=[Certificate.objects.first().pk])
    assert set(Certificate.objects.values_list("status", flat=True)) == {S.GENERATED}


def test_a_file_that_is_not_a_png_or_jpg_counts_as_missing(generated, design, director) -> None:
    _, vp, _ = design
    (Path(settings.MEDIA_ROOT) / vp.signature_image_path.name).write_bytes(b"not an image")
    assert services.approval_closed_reason(generated[0]) == MISSING_VP


def test_new_signatory_plus_regenerate_unblocks_approval(evaluated, design, director, admin_member) -> None:
    activity, _ = evaluated
    remove_signature_file(design[1])
    new_vp = services.add_signatory(admin_member, "Dr. New Vice Pres", "vp", upload("vp.png", image_bytes()))
    assert services.approval_closed_reason(activity) == MISSING_VP  # the certificates keep the old VP
    assert services.regenerate_certificates(director, activity=activity) == 3
    assert set(Certificate.objects.values_list("vp_signatory", flat=True)) == {new_vp.pk}
    assert services.approve_certificates(director, activity=activity) == 3


def test_replacing_a_lost_signature_on_the_tab_unblocks_approval(evaluated, design, director) -> None:
    activity, _ = evaluated
    remove_signature_file(design[1])
    services.upload_signature(director, activity, "vp", upload("vp.png", image_bytes()))  # no regenerate needed
    assert services.approval_closed_reason(activity) is None
    assert services.approve_certificates(director, activity=activity) == 3


# --- Regenerating sends a certificate back to "Waiting for approval" ---------------------------------------


def test_regenerating_after_approval_needs_approval_again(generated, design, director, admin_member, run_tasks,
                                                          django_capture_on_commit_callbacks) -> None:
    activity, _ = generated
    with django_capture_on_commit_callbacks(execute=True):
        assert services.approve_certificates(director, activity=activity) == 1  # the resource person: ready
    speaker = appreciation_of(activity)
    assert speaker.file_path  # resource persons are rendered on approval
    old_pdf = Path(settings.MEDIA_ROOT) / speaker.file_path.name
    assert old_pdf.exists()

    new_template = services.upload_template(admin_member, "New design", template_upload("new.png"), activate=True)
    with django_capture_on_commit_callbacks(execute=True):
        assert services.regenerate_certificates(director, activity=activity) == 3
    for certificate in Certificate.objects.all():
        assert certificate.status == S.GENERATED
        assert certificate.approved_by is None and certificate.approved_at is None
        assert certificate.certificate_template == new_template and not certificate.file_path
    assert not old_pdf.exists()  # the old rendered PDF is removed after the commit
    assert services.approve_certificates(director, activity=activity) == 1


def test_regenerate_keeps_the_number_and_refuses_issued(generated, director, approve_all, evaluate, run_tasks,
                                                        django_capture_on_commit_callbacks) -> None:
    activity, people = generated
    number = certificate_of(people[1]).certificate_number
    with django_capture_on_commit_callbacks(execute=True):
        evaluate(people[0])
        approve_all(activity)
    issued = certificate_of(people[0])
    assert issued.status == S.ISSUED
    with pytest.raises(services.CertificateError, match="Issued certificates are final"):
        services.regenerate_certificates(director, certificate_ids=ids(issued))
    assert services.regenerate_certificates(director, activity=activity) == 2  # Issued ones are left out
    assert certificate_of(people[1]).certificate_number == number


def test_a_render_of_an_old_design_is_not_saved(generated, approve_all, admin_member, monkeypatch) -> None:
    """A task that rendered before the certificate was regenerated (and approved again) must not save the old
    design's PDF: the next task renders the new one."""
    activity, _ = generated
    approve_all(activity)
    speaker = appreciation_of(activity)
    newer = services.upload_template(admin_member, "Newer design", template_upload("newer.png"))
    real_render = services.pdf.render_certificate_pdf

    def render_then_redesign(certificate, **kwargs):
        content = real_render(certificate, **kwargs)
        Certificate.objects.filter(pk=certificate.pk).update(certificate_template=newer)  # meanwhile: new design, approved
        return content

    monkeypatch.setattr(services.pdf, "render_certificate_pdf", render_then_redesign)
    assert services.prepare_certificate(speaker.pk) == "design changed"
    speaker.refresh_from_db()
    assert not speaker.file_path and speaker.status == S.APPROVED


# --- Sending: failed, resend ------------------------------------------------------------------------------


def test_failed_send_is_recorded_and_resend_sends_it_once(generated, approve_all, evaluate, run_tasks, monkeypatch,
                                                         django_capture_on_commit_callbacks, director) -> None:
    activity, people = generated
    real_send = emails.send_certificate_email

    def broken(*args, **kwargs):
        raise ConnectionRefusedError("mail server down")

    monkeypatch.setattr(emails, "send_certificate_email", broken)
    monkeypatch.setattr(services, "schedule_send_retry", lambda *args: None)  # no automatic retry in this test
    with django_capture_on_commit_callbacks(execute=True):
        evaluate(people[0])
        approve_all(activity)
    certificate = certificate_of(people[0])
    assert certificate.status == S.ISSUED and certificate.sent_at is None and certificate.send_failed_at
    assert mail.outbox == []

    monkeypatch.setattr(emails, "send_certificate_email", real_send)
    with django_capture_on_commit_callbacks(execute=True):
        assert services.resend_certificates(director, certificate_ids=ids(certificate)) == 1
    certificate.refresh_from_db()
    assert certificate.sent_at and certificate.send_failed_at is None
    assert len(mail.outbox) == 1
    with pytest.raises(services.CertificateError, match="no certificate emails waiting"):
        services.resend_certificates(director, activity=activity)
    assert services.send_certificate(certificate.pk) == "already sent"
    assert len(mail.outbox) == 1  # sent once only


def test_resend_refuses_what_cannot_be_emailed(generated, director) -> None:
    activity, people = generated
    with pytest.raises(services.CertificateError) as error:
        services.resend_certificates(director, certificate_ids=ids(certificate_of(people[0]), appreciation_of(activity)))
    assert any("hard-copy certificates are never emailed" in p for p in error.value.problems)
    assert any("only Issued certificates are emailed" in p for p in error.value.problems)


def test_the_send_task_records_the_failure_too(generated, approve_all, evaluate, run_tasks, monkeypatch,
                                               django_capture_on_commit_callbacks) -> None:
    activity, people = generated
    monkeypatch.setattr(emails, "send_certificate_email", lambda *a, **k: (_ for _ in ()).throw(OSError("down")))
    retries = []
    monkeypatch.setattr(services, "schedule_send_retry", lambda *args: retries.append(args))
    with django_capture_on_commit_callbacks(execute=True):
        evaluate(people[0])
        approve_all(activity)
    certificate = certificate_of(people[0])
    assert certificate.send_failed_at and retries  # recorded, and the automatic retries still run
    assert tasks.run_send_certificate(certificate.pk, 99)["result"] == "failed"


# --- Hand-over (resource persons) ----------------------------------------------------------------------


def test_hand_over_after_approval(generated, director, approve_all, run_tasks, django_capture_on_commit_callbacks) -> None:
    activity, _ = generated
    with pytest.raises(services.CertificateError):
        services.mark_handed_over(director, activity=activity)  # nothing approved yet
    with django_capture_on_commit_callbacks(execute=True):
        approve_all(activity)
    assert services.mark_handed_over(director, activity=activity) == 1
    speaker = appreciation_of(activity)
    assert speaker.status == S.ISSUED and speaker.issued_at and speaker.sent_at is None
