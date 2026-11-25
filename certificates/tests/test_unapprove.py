"""Unapprove an activity's certificate approval (ASSUMPTIONS 437): can_approve_certificates, a reason, only while no
certificate is Issued; Approved certificates go back to waiting (PDF removed), History by the member, the creator and
the preparer told; the tab's button and URL."""

import pytest
from django.core.exceptions import PermissionDenied, ValidationError
from django.test import Client
from django.urls import reverse

from activities.models import Activity, ActivityAuditLog
from certificates import services
from certificates.models import Certificate
from notifications.models import Notification

from .conftest import S, appreciation_of

pytestmark = pytest.mark.django_db


@pytest.fixture
def approved(generated, director, run_tasks, django_capture_on_commit_callbacks):
    """Approved: the resource person's certificate is Approved and rendered; the participants' wait for evaluations."""
    activity, people = generated
    with django_capture_on_commit_callbacks(execute=True):
        services.approve_activity_certificates(director, activity)
    assert appreciation_of(activity).status == S.APPROVED and appreciation_of(activity).file_path
    return Activity.objects.get(pk=activity.pk)


@pytest.fixture
def other_director(make_member):
    return make_member("Director", email="other.director@example.com")


def test_unapprove_sends_approved_certificates_back_and_logs_it(approved, other_director,
                                                                   django_capture_on_commit_callbacks) -> None:
    with django_capture_on_commit_callbacks(execute=True):
        back = services.unapprove_activity_certificates(other_director, approved, "Wrong venue on the certificate")
    assert back == 1
    activity = Activity.objects.get(pk=approved.pk)
    assert not services.is_approved(activity) and activity.certificates_approved_by is None
    certificate = appreciation_of(activity)
    assert certificate.status == S.GENERATED and not certificate.file_path
    row = ActivityAuditLog.objects.filter(activity=activity, field_changed="Certificate approval").latest("pk")
    assert row.changed_by == other_director
    assert row.new_value == f"Needs approval (unapproved by {other_director.name}: Wrong venue on the certificate)"


def test_the_creator_is_told_never_the_member_who_unapproved(approved, director, other_director) -> None:
    services.unapprove_activity_certificates(other_director, approved, "Check the titles")
    told = Notification.objects.filter(type=Notification.CERTIFICATES_UNAPPROVED)
    assert list(told.values_list("member", flat=True)) == [approved.created_by_id]
    assert "Check the titles" in told.get().message
    Notification.objects.all().delete()
    services.approve_activity_certificates(director, Activity.objects.get(pk=approved.pk))
    services.unapprove_activity_certificates(director, Activity.objects.get(pk=approved.pk), "Again")
    assert not Notification.objects.filter(type=Notification.CERTIFICATES_UNAPPROVED, member=director).exists()


def test_a_reason_is_required(approved, director) -> None:
    with pytest.raises(ValidationError):
        services.unapprove_activity_certificates(director, approved, "   ")
    assert services.is_approved(Activity.objects.get(pk=approved.pk))


def test_refused_once_a_certificate_is_issued(approved, director) -> None:
    Certificate.objects.filter(pk=appreciation_of(approved).pk).update(status=S.ISSUED)
    assert services.unapprove_closed_reason(approved) == services.UNAPPROVE_ISSUED
    with pytest.raises(services.CertificateError, match="already issued"):
        services.unapprove_activity_certificates(director, approved, "Too late")
    assert services.is_approved(Activity.objects.get(pk=approved.pk))


def test_refused_when_not_approved(generated, director) -> None:
    activity, _ = generated
    with pytest.raises(services.CertificateError, match="not approved"):
        services.unapprove_activity_certificates(director, activity, "Nothing to undo")


def test_needs_can_approve_certificates(approved, make_member) -> None:
    staff = make_member("Admin Staff", email="staff2@example.com")
    with pytest.raises(PermissionDenied):
        services.unapprove_activity_certificates(staff, approved, "No")


def test_the_tab_button_and_url(approved, director, make_member) -> None:
    client = Client()
    client.force_login(director)
    tab = client.get(reverse("activities:detail", args=[approved.pk]), {"tab": "certificates"})
    assert reverse("activities:certificates_unapprove_activity", args=[approved.pk]).encode() in tab.content
    response = client.post(reverse("activities:certificates_unapprove_activity", args=[approved.pk]), {"reason": "Fix it"})
    assert response.status_code == 302 and not services.is_approved(Activity.objects.get(pk=approved.pk))

    staff = Client()
    staff.force_login(make_member("Admin Staff", email="staff3@example.com"))
    assert staff.post(reverse("activities:certificates_unapprove_activity", args=[approved.pk]), {"reason": "x"}).status_code == 403
