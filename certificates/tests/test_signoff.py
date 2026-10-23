"""Review, sign-offs, approval, rejection and hand-over: one certificate or a whole activity."""

import logging

import pytest
from django.core.exceptions import PermissionDenied

from certificates import services
from certificates.models import Certificate

from .conftest import S, appreciation_of, certificate_of

pytestmark = pytest.mark.django_db


def ids(*certificates: Certificate) -> list[int]:
    return [c.pk for c in certificates]


def test_review_sets_reviewed_by_and_at(generated, director) -> None:
    activity, people = generated
    certificate = certificate_of(people[0])
    assert services.review_certificates(director, certificate_ids=ids(certificate)) == 1
    certificate.refresh_from_db()
    assert certificate.status == S.PENDING_SIGNOFF and certificate.reviewed_by == director and certificate.reviewed_at
    assert services.review_certificates(director, activity=activity) == 2  # the other two of the activity


def test_sign_offs_only_while_pending(generated, director) -> None:
    certificate = certificate_of(generated[1][0])
    with pytest.raises(services.CertificateError, match="only while Pending Sign-off"):
        services.record_signoff(director, "director", certificate_ids=ids(certificate))
    services.review_certificates(director, certificate_ids=ids(certificate))
    services.record_signoff(director, "director", certificate_ids=ids(certificate))
    certificate.refresh_from_db()
    assert certificate.director_signed_at and certificate.vp_signed_at is None
    with pytest.raises(services.CertificateError, match="already recorded"):
        services.record_signoff(director, "director", certificate_ids=ids(certificate))


def test_sign_off_for_the_whole_activity_logs_who_recorded_it(generated, director, caplog) -> None:
    activity, _ = generated
    services.review_certificates(director, activity=activity)
    with caplog.at_level(logging.INFO, logger="certificates"):
        assert services.record_signoff(director, "vp", activity=activity) == 3
    assert f"Member {director.pk} recorded the vp sign-off" in caplog.text
    assert all(c.vp_signed_at for c in Certificate.objects.all())
    with pytest.raises(services.CertificateError, match="no certificates to act on"):
        services.record_signoff(director, "vp", activity=activity)  # nothing left without a VP sign-off


def test_sign_off_position_must_be_known(generated, director) -> None:
    with pytest.raises(services.CertificateError, match="director or vp"):
        services.record_signoff(director, "dean", activity=generated[0])


def test_approval_needs_both_sign_offs(generated, director) -> None:
    activity, _ = generated
    services.review_certificates(director, activity=activity)
    services.record_signoff(director, "director", activity=activity)
    with pytest.raises(services.CertificateError, match="missing vp_signed_at"):
        services.approve_certificates(director, activity=activity)
    assert set(Certificate.objects.values_list("status", flat=True)) == {S.PENDING_SIGNOFF}


def test_bulk_actions_are_all_or_nothing(generated, director) -> None:
    activity, people = generated
    first, second = certificate_of(people[0]), certificate_of(people[1])
    services.review_certificates(director, activity=activity)
    for position in ("director", "vp"):
        services.record_signoff(director, position, certificate_ids=ids(first))
    with pytest.raises(services.CertificateError) as error:
        services.approve_certificates(director, certificate_ids=ids(first, second))
    assert second.certificate_number in str(error.value) and "Nothing was changed" in str(error.value)
    first.refresh_from_db()
    assert first.status == S.PENDING_SIGNOFF  # the valid one was not approved either


def test_reject_clears_the_sign_offs_and_keeps_the_reviewer(generated, director) -> None:
    activity, people = generated
    services.review_certificates(director, activity=activity)
    services.record_signoff(director, "director", activity=activity)
    services.record_signoff(director, "vp", activity=activity)
    assert services.reject_certificates(director, activity=activity) == 3
    certificate = certificate_of(people[0])
    assert certificate.status == S.GENERATED and certificate.reviewed_by == director
    assert (certificate.reviewed_at, certificate.director_signed_at, certificate.vp_signed_at) == (None, None, None)


@pytest.mark.parametrize(
    "action, start, message",
    [
        (services.review_certificates, S.PENDING_SIGNOFF, "from Pending Sign-off to Pending Sign-off"),
        (services.approve_certificates, S.GENERATED, "from Generated to Approved"),
        (services.reject_certificates, S.GENERATED, "from Generated to Generated"),
        (services.reject_certificates, S.APPROVED, "from Approved to Generated"),
        (services.review_certificates, S.ISSUED, "from Issued to Pending Sign-off"),
    ],
)
def test_refused_moves(generated, director, action, start, message) -> None:
    certificate = appreciation_of(generated[0])
    Certificate.objects.filter(pk=certificate.pk).update(status=start)
    with pytest.raises(services.CertificateError, match=message):
        action(director, certificate_ids=[certificate.pk])
    certificate.refresh_from_db()
    assert certificate.status == start


def test_unknown_or_foreign_ids_are_refused(generated, director) -> None:
    with pytest.raises(services.CertificateError, match="not found"):
        services.review_certificates(director, certificate_ids=[999999])
    with pytest.raises(services.CertificateError, match="either"):
        services.review_certificates(director)


@pytest.mark.parametrize("action", ["review_certificates", "approve_certificates", "reject_certificates", "mark_handed_over"])
def test_actions_need_can_create_activity(generated, admin_staff, action) -> None:
    with pytest.raises(PermissionDenied):
        getattr(services, action)(admin_staff, activity=generated[0])
    with pytest.raises(PermissionDenied):
        services.record_signoff(admin_staff, "vp", activity=generated[0])


# --- Hand-over (hard copy) ----------------------------------------------------------------------


def test_hand_over_only_hard_copies_with_a_pdf(generated, director, approve_all, run_tasks, django_capture_on_commit_callbacks) -> None:
    activity, people = generated
    with django_capture_on_commit_callbacks(execute=True):
        approve_all(activity)
    participant = certificate_of(people[0])
    with pytest.raises(services.CertificateError, match="only hard-copy"):
        services.mark_handed_over(director, certificate_ids=[participant.pk])

    appreciation = appreciation_of(activity)
    assert appreciation.file_path  # rendered on approval
    assert services.mark_handed_over(director, activity=activity) == 1
    appreciation.refresh_from_db()
    assert appreciation.status == S.ISSUED and appreciation.issued_at and appreciation.sent_at is None


def test_hand_over_waits_for_the_pdf(generated, director, approve_all) -> None:
    activity, _ = generated
    approve_all(activity)  # no task ran: the PDF is not rendered yet
    with pytest.raises(services.CertificateError, match="PDF is not ready"):
        services.mark_handed_over(director, activity=activity)


# --- API -----------------------------------------------------------------------------------------


def test_api_runs_the_whole_flow(client, conducted, design, director, admin_staff) -> None:
    activity, _ = conducted
    client.force_login(director)
    assert client.post(f"/certificates/api/activities/{activity.pk}/generate/").json()["participation_created"] == 2
    body = {"activity_id": activity.pk}
    for action, extra in (("review", {}), ("sign", {"position": "director"}), ("sign", {"position": "vp"}), ("approve", {})):
        response = client.post(f"/certificates/api/{action}/", {**body, **extra}, content_type="application/json")
        assert response.status_code == 200, response.json()
        assert response.json()["changed"] == 3
    assert set(Certificate.objects.values_list("status", flat=True)) == {S.APPROVED}

    bad = client.post("/certificates/api/approve/", {"certificate_ids": "all"}, content_type="application/json")
    assert bad.status_code == 400
    client.force_login(admin_staff)
    assert client.post("/certificates/api/review/", body, content_type="application/json").status_code == 403
